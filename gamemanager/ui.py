# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PocketKode
"""SDL2 renderer helpers: window, fonts (with per-glyph fallback), text cache, images."""
import ctypes
import math
import os
import time
from collections import OrderedDict

import sdl2
import sdl2.sdlimage as img
import sdl2.sdlttf as ttf

W, H = 640, 480

C = {
    "bg": (15, 15, 15),
    "bar": (28, 28, 28),
    "row": (36, 36, 36),
    "sel": (62, 62, 62),
    "accent": (0, 137, 123),
    "text": (241, 241, 241),
    "dim": (170, 170, 170),
    "faint": (110, 110, 110),
    "key": (48, 48, 48),
    "black": (0, 0, 0),
    "ok": (46, 160, 67),
    "warn": (214, 150, 20),
    "bad": (200, 64, 64),
    "star": (245, 190, 50),
}


class Fonts:
    """A stack of fonts; each character is drawn with the first font that has it."""

    def __init__(self, specs):
        self.specs = specs  # list of (path, face_index)
        self.by_size = {}
        self.provided = {}
        self._has32 = hasattr(ttf, "TTF_GlyphIsProvided32")

    def get(self, size):
        fonts = self.by_size.get(size)
        if fonts is None:
            fonts = []
            for path, idx in self.specs:
                f = ttf.TTF_OpenFontIndex(path.encode(), size, idx)
                if f:
                    fonts.append(f)
                else:
                    print(f"[ui] could not open font {path}: {ttf.TTF_GetError()}")
            if not fonts:
                raise RuntimeError("No usable font found in fonts/")
            self.by_size[size] = fonts
        return fonts

    def _has(self, fi, font, cp):
        key = (fi, cp)
        r = self.provided.get(key)
        if r is None:
            try:
                if self._has32:
                    r = bool(ttf.TTF_GlyphIsProvided32(font, cp))
                else:
                    r = cp < 0x10000 and bool(ttf.TTF_GlyphIsProvided(font, cp))
            except Exception:  # noqa: BLE001 - older SDL_ttf
                self._has32 = False
                r = cp < 0x10000 and bool(ttf.TTF_GlyphIsProvided(font, cp))
            self.provided[key] = r
        return r

    def runs(self, text, size):
        fonts = self.get(size)
        out = []
        for ch in text:
            cp = ord(ch)
            chosen = 0
            if cp > 32:
                for fi, f in enumerate(fonts):
                    if self._has(fi, f, cp):
                        chosen = fi
                        break
            if out and out[-1][0] == chosen:
                out[-1][1].append(ch)
            else:
                out.append((chosen, [ch]))
        return [(fonts[fi], "".join(chs)) for fi, chs in out]


class UI:
    def __init__(self, app_dir):
        self.app_dir = app_dir
        self.window = None
        self.renderer = None
        self.text_cache = OrderedDict()
        self.measure_cache = {}
        self.thumb_tex = {}
        ttf.TTF_Init()
        img.IMG_Init(img.IMG_INIT_JPG | img.IMG_INIT_PNG)
        self.fonts = Fonts(self._font_specs())
        self.open()

    def _font_specs(self):
        """DejaVu Sans first (Latin, Cyrillic, symbols), then the Noto fonts, the current language's one first."""
        from . import i18n
        fdir = os.path.join(self.app_dir, "fonts")
        noto = ["NotoSansJP-Regular.otf", "NotoSansSC-Regular.otf", "NotoSansKR-Regular.otf"]
        mine = i18n.font_file()
        order = ["DejaVuSans.ttf"] + ([mine] if i18n.current() != "en" else []) + [f for f in noto if f != mine or i18n.current() == "en"]
        specs = [(os.path.join(fdir, f), 0) for f in order if os.path.exists(os.path.join(fdir, f))]
        extra = os.path.join(fdir, "extra")
        if os.path.isdir(extra):
            for f in sorted(os.listdir(extra)):
                if f.lower().endswith((".ttf", ".otf", ".ttc")):
                    specs.append((os.path.join(extra, f), 0))
        return specs

    # ---------------------------------------------------------------- window
    def open(self):
        sdl2.SDL_SetHint(sdl2.SDL_HINT_RENDER_SCALE_QUALITY, b"1")
        flags = sdl2.SDL_WINDOW_SHOWN
        if not os.environ.get("GM_WINDOWED"):
            flags |= sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP
        self.window = sdl2.SDL_CreateWindow(b"Game Manager", sdl2.SDL_WINDOWPOS_UNDEFINED,
                                            sdl2.SDL_WINDOWPOS_UNDEFINED, W, H, flags)
        if not self.window:
            raise RuntimeError(f"SDL_CreateWindow failed: {sdl2.SDL_GetError()}")
        self.renderer = sdl2.SDL_CreateRenderer(self.window, -1, sdl2.SDL_RENDERER_ACCELERATED)
        if not self.renderer:
            self.renderer = sdl2.SDL_CreateRenderer(self.window, -1, sdl2.SDL_RENDERER_SOFTWARE)
        if not self.renderer:
            raise RuntimeError(f"SDL_CreateRenderer failed: {sdl2.SDL_GetError()}")
        sdl2.SDL_RenderSetLogicalSize(self.renderer, W, H)
        sdl2.SDL_SetRenderDrawBlendMode(self.renderer, sdl2.SDL_BLENDMODE_BLEND)
        sdl2.SDL_ShowCursor(sdl2.SDL_DISABLE)

    def language_changed(self):
        """Reload the font order for the new language (and forget texts drawn with the old one)."""
        for tex, _w, _h in self.text_cache.values():
            sdl2.SDL_DestroyTexture(tex)
        self.text_cache.clear()
        self.measure_cache.clear()
        self.fonts = Fonts(self._font_specs())

    def close(self):
        """Release the screen so an emulator can use it. Call open() afterwards."""
        for tex, _, _ in self.text_cache.values():
            sdl2.SDL_DestroyTexture(tex)
        self.text_cache.clear()
        for tex in self.thumb_tex.values():
            if tex:
                sdl2.SDL_DestroyTexture(tex)
        self.thumb_tex.clear()
        if self.renderer:
            sdl2.SDL_DestroyRenderer(self.renderer)
        if self.window:
            sdl2.SDL_DestroyWindow(self.window)
        self.renderer = self.window = None

    # --------------------------------------------------------------- drawing
    def clear(self, color=C["bg"]):
        sdl2.SDL_SetRenderDrawColor(self.renderer, *color, 255)
        sdl2.SDL_RenderClear(self.renderer)

    def present(self):
        sdl2.SDL_RenderPresent(self.renderer)

    def fill(self, x, y, w, h, color, alpha=255):
        sdl2.SDL_SetRenderDrawColor(self.renderer, *color, alpha)
        sdl2.SDL_RenderFillRect(self.renderer, sdl2.SDL_Rect(int(x), int(y), int(w), int(h)))

    def outline(self, x, y, w, h, color, thick=2):
        for i in range(thick):
            sdl2.SDL_SetRenderDrawColor(self.renderer, *color, 255)
            sdl2.SDL_RenderDrawRect(self.renderer, sdl2.SDL_Rect(int(x + i), int(y + i),
                                                                 int(w - 2 * i), int(h - 2 * i)))

    def rounded(self, x, y, w, h, r, color, alpha=255):
        r = min(r, h // 2, w // 2)
        self.fill(x + r, y, w - 2 * r, h, color, alpha)
        for i in range(r):
            dx = r - int(math.sqrt(r * r - (r - i - 0.5) ** 2))
            self.fill(x + dx, y + i, r - dx, 1, color, alpha)
            self.fill(x + w - r, y + i, r - dx, 1, color, alpha)
            self.fill(x + dx, y + h - 1 - i, r - dx, 1, color, alpha)
            self.fill(x + w - r, y + h - 1 - i, r - dx, 1, color, alpha)
        self.fill(x, y + r, r, h - 2 * r, color, alpha)
        self.fill(x + w - r, y + r, r, h - 2 * r, color, alpha)

    def triangle_right(self, x, y, size, color):
        for i in range(size):
            half = (size - abs(size - 1 - 2 * i) ) // 2
            self.fill(x, y + i, max(1, half), 1, color)

    def circle(self, cx, cy, r, color):
        for i in range(-r, r + 1):
            half = int(math.sqrt(max(0, r * r - i * i)))
            self.fill(cx - half, cy + i, 2 * half + 1, 1, color)

    def logo(self, x, y):
        """Small gamepad: body, d-pad and two buttons."""
        self.rounded(x, y + 5, 30, 18, 8, C["accent"])
        self.fill(x + 6, y + 12, 8, 3, C["text"])
        self.fill(x + 8, y + 10, 3, 8, C["text"])
        self.circle(x + 20, y + 15, 2, C["text"])
        self.circle(x + 24, y + 11, 2, C["text"])

    # ------------------------------------------------------------------ text
    def measure(self, text, size):
        key = (text, size)
        r = self.measure_cache.get(key)
        if r is None:
            w = h = 0
            wp, hp = ctypes.c_int(), ctypes.c_int()
            for font, s in self.fonts.runs(text, size):
                ttf.TTF_SizeUTF8(font, s.encode("utf-8"), ctypes.byref(wp), ctypes.byref(hp))
                w += wp.value
                h = max(h, hp.value)
            r = (w, h or size)
            if len(self.measure_cache) > 4000:
                self.measure_cache.clear()
            self.measure_cache[key] = r
        return r

    def ellipsize(self, text, size, max_w):
        if self.measure(text, size)[0] <= max_w:
            return text
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.measure(text[:mid].rstrip() + "…", size)[0] <= max_w:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo].rstrip() + "…"

    def wrap(self, text, size, max_w, max_lines=2):
        lines, rest = [], text.strip()
        while rest and len(lines) < max_lines:
            if len(lines) == max_lines - 1 or self.measure(rest, size)[0] <= max_w:
                lines.append(self.ellipsize(rest, size, max_w))
                break
            lo, hi = 1, len(rest)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if self.measure(rest[:mid], size)[0] <= max_w:
                    lo = mid
                else:
                    hi = mid - 1
            cut = lo
            sp = rest.rfind(" ", 0, lo + 1)
            if sp > lo * 0.5:
                cut = sp
            lines.append(rest[:cut].rstrip())
            rest = rest[cut:].lstrip()
        return lines

    def _text_texture(self, text, size, color):
        key = (text, size, color)
        hit = self.text_cache.get(key)
        if hit:
            self.text_cache.move_to_end(key)
            return hit
        runs = self.fonts.runs(text, size)
        col = sdl2.SDL_Color(*color, 255)
        surfs, total_w, asc_max, below_max = [], 0, 0, 0
        for font, s in runs:
            surf = ttf.TTF_RenderUTF8_Blended(font, s.encode("utf-8"), col)
            if not surf:
                continue
            asc = ttf.TTF_FontAscent(font)
            surfs.append((surf, asc))
            total_w += surf.contents.w
            asc_max = max(asc_max, asc)
            below_max = max(below_max, surf.contents.h - asc)
        if not surfs:
            return None
        total_h = asc_max + below_max
        dst = sdl2.SDL_CreateRGBSurfaceWithFormat(0, max(1, total_w), max(1, total_h), 32,
                                                  sdl2.SDL_PIXELFORMAT_ARGB8888)
        sdl2.SDL_FillRect(dst, None, 0)
        x = 0
        for surf, asc in surfs:
            sdl2.SDL_SetSurfaceBlendMode(surf, sdl2.SDL_BLENDMODE_NONE)
            sdl2.SDL_BlitSurface(surf, None, dst, sdl2.SDL_Rect(x, asc_max - asc, 0, 0))
            x += surf.contents.w
            sdl2.SDL_FreeSurface(surf)
        tex = sdl2.SDL_CreateTextureFromSurface(self.renderer, dst)
        sdl2.SDL_SetTextureBlendMode(tex, sdl2.SDL_BLENDMODE_BLEND)
        entry = (tex, total_w, total_h)
        sdl2.SDL_FreeSurface(dst)
        self.text_cache[key] = entry
        if len(self.text_cache) > 400:
            _, (old, _, _) = self.text_cache.popitem(last=False)
            sdl2.SDL_DestroyTexture(old)
        return entry

    def text(self, x, y, text, size=20, color=C["text"], max_w=None, align="left"):
        if not text:
            return 0
        if max_w:
            text = self.ellipsize(text, size, max_w)
        entry = self._text_texture(text, size, color)
        if not entry:
            return 0
        tex, w, h = entry
        if align == "center":
            x -= w // 2
        elif align == "right":
            x -= w
        sdl2.SDL_RenderCopy(self.renderer, tex, None, sdl2.SDL_Rect(int(x), int(y), w, h))
        return w

    # ---------------------------------------------------------------- images
    def image(self, key, data, x, y, w, h, fit=False):
        """Draw image bytes (cached as a texture under `key`). fit=True keeps the aspect ratio."""
        tex = self.thumb_tex.get(key)
        if tex is None and data:
            buf = ctypes.create_string_buffer(data, len(data))
            rw = sdl2.SDL_RWFromConstMem(buf, len(data))
            surf = img.IMG_Load_RW(rw, 1)
            tex = sdl2.SDL_CreateTextureFromSurface(self.renderer, surf) if surf else 0
            if surf:
                sdl2.SDL_FreeSurface(surf)
            self.thumb_tex[key] = tex
            if len(self.thumb_tex) > 80:
                old_key = next(iter(self.thumb_tex))
                old = self.thumb_tex.pop(old_key)
                if old:
                    sdl2.SDL_DestroyTexture(old)
        if tex:
            if fit:
                tw, th = ctypes.c_int(), ctypes.c_int()
                sdl2.SDL_QueryTexture(tex, None, None, ctypes.byref(tw), ctypes.byref(th))
                if tw.value and th.value:
                    s = min(w / tw.value, h / th.value)
                    dw, dh = tw.value * s, th.value * s
                    x, y, w, h = x + (w - dw) / 2, y + (h - dh) / 2, dw, dh
            sdl2.SDL_RenderCopy(self.renderer, tex, None, sdl2.SDL_Rect(int(x), int(y), int(w), int(h)))
            return True
        return False

    def spinner(self, cx, cy, r=18):
        t = time.monotonic()
        n = 10
        head = int(t * 12) % n
        for i in range(n):
            a = 2 * math.pi * i / n
            fade = ((i - head) % n) / n
            shade = int(60 + 190 * (1 - fade))
            self.fill(cx + math.cos(a) * r - 3, cy + math.sin(a) * r - 3, 6, 6, (shade, shade, shade))

    def screenshot(self, path):
        surf = sdl2.SDL_CreateRGBSurfaceWithFormat(0, W, H, 32, sdl2.SDL_PIXELFORMAT_ARGB8888)
        sdl2.SDL_RenderReadPixels(self.renderer, None, sdl2.SDL_PIXELFORMAT_ARGB8888,
                                  surf.contents.pixels, surf.contents.pitch)
        sdl2.SDL_SaveBMP(surf, path.encode())
        sdl2.SDL_FreeSurface(surf)
