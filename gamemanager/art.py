# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PocketKode
"""Save cover images as PNG box art for muOS (uses the system SDL2_image)."""
import ctypes

import sdl2
import sdl2.sdlimage as img

BOX_MAX = 256


def save_png(data, path, max_size=BOX_MAX):
    """Decode image bytes (PNG/JPEG/GIF…), shrink to fit max_size, save as PNG. Returns True on success."""
    if not data:
        return False
    buf = ctypes.create_string_buffer(data, len(data))
    src = img.IMG_Load_RW(sdl2.SDL_RWFromConstMem(buf, len(data)), 1)
    if not src:
        return False
    conv = dst = None
    try:
        conv = sdl2.SDL_ConvertSurfaceFormat(src, sdl2.SDL_PIXELFORMAT_ARGB8888, 0)
        if not conv:
            return False
        w, h = conv.contents.w, conv.contents.h
        scale = min(1.0, max_size / max(w, h))
        dw, dh = max(1, int(w * scale)), max(1, int(h * scale))
        dst = sdl2.SDL_CreateRGBSurfaceWithFormat(0, dw, dh, 32, sdl2.SDL_PIXELFORMAT_ARGB8888)
        if not dst:
            return False
        sdl2.SDL_SetSurfaceBlendMode(conv, sdl2.SDL_BLENDMODE_NONE)
        sdl2.SDL_BlitScaled(conv, None, dst, sdl2.SDL_Rect(0, 0, dw, dh))
        return img.IMG_SavePNG(dst, path.encode()) == 0
    finally:
        for s in (dst, conv, src):
            if s:
                sdl2.SDL_FreeSurface(s)
