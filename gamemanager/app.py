# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PocketKode
"""Screens and main loop for Game Manager."""
import configparser
import os
import queue
import re
import shutil
import threading
import time

import sdl2

from . import updater
from . import art
from . import boxart
from . import installer
from . import i18n
from . import launcher
from . import mygames
from . import net
from . import romscan
from .input import Input
from .i18n import _, tr
from .sources import GENRES, HomebrewHub, Itch, fetch_rating
from .sources_extra import PortMaster
from .systems import BY_ID, Device
from .tasks import Task
from .ui import C, H, UI, W

APP_VERSION = "1.3.0"

HEADER_H = 44
FOOTER_H = 34
ITCH_KEY_URL = "itch.io/user/settings/api-keys"


# ----------------------------------------------------------------- helpers
def yes(v):
    return str(v).strip().lower() in ("1", "yes", "true", "on")


def load_config(app_dir):
    path = os.path.join(app_dir, "config.ini")
    if not os.path.exists(path) and os.path.exists(path + ".example"):
        # The package ships config.ini.example, so installing a new version never replaces your settings or API key
        try:
            shutil.copyfile(path + ".example", path)
        except OSError:
            pass
    cp = configparser.ConfigParser(interpolation=None)
    cp.read(path)
    cfg = {"swap_ab": "no", "roms_path": "/mnt/mmc/ROMS", "hide_pc_only": "yes",
           "include_demos": "no", "api_key": ""}
    for section in cp.sections():
        cfg.update({k: v for k, v in cp.items(section)})
    return cfg


def save_api_key(app_dir, key):
    """Write api_key into config.ini, keeping the comments."""
    path = os.path.join(app_dir, "config.ini")
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        text = ""
    line = f"api_key = {key}"
    if re.search(r"^api_key\s*=.*$", text, re.M):
        text = re.sub(r"^api_key\s*=.*$", line, text, count=1, flags=re.M)
    else:
        text = text.rstrip("\n") + f"\n\n[itch]\n{line}\n"
    with open(path, "w") as f:
        f.write(text)


def fmt_size(n):
    if not n:
        return ""
    for unit, div in (("MB", 1024 * 1024), ("KB", 1024)):
        if n >= div:
            return f"{n / div:.1f} {unit}".replace(".0 ", " ")
    return f"{n} B"


class Images:
    """Background image downloader (3 threads), keeps the most recent ~150 images."""

    def __init__(self):
        self.data = {}
        self.order = []
        self.q = queue.Queue()
        self.requested = set()
        self.lock = threading.Lock()
        for _i in range(3):
            threading.Thread(target=self._worker, daemon=True).start()

    def want(self, url):
        with self.lock:
            if not url or url in self.requested:
                return
            self.requested.add(url)
        self.q.put(url)

    def _worker(self):
        while True:
            url = self.q.get()
            try:
                data = net.get_bytes(url, timeout=15, limit=4 * 1024 * 1024)
            except Exception:  # noqa: BLE001
                data = b""
            with self.lock:
                self.data[url] = data
                self.order.append(url)
                while len(self.order) > 150:
                    old = self.order.pop(0)
                    self.data.pop(old, None)
                    self.requested.discard(old)


class Inspector:
    """Background checks: what an itch.io game's downloads are, and every game's itch.io rating."""

    def __init__(self, app):
        self.app = app
        self.q = queue.Queue()
        self.queued = set()
        for _i in range(3):
            threading.Thread(target=self._worker, daemon=True).start()

    def want(self, game):
        needs_files = game["src"] == "itch" and game.get("files") is None
        needs_rating = "rating" not in game and game.get("rating_page")
        if (needs_files or needs_rating) and game["key"] not in self.queued:
            self.queued.add(game["key"])
            self.q.put(game)

    def _worker(self):
        while True:
            game = self.q.get()
            try:
                if game["src"] == "itch" and game.get("files") is None:
                    self.app.itch.inspect(game, self.app.device)
                else:
                    fetch_rating(game)
            except Exception:  # noqa: BLE001
                if game["src"] == "itch" and game.get("files") is None:
                    game["check"] = ("warn", "?")
                game.setdefault("rating", None)


# ------------------------------------------------------------------ drawing
def draw_header(ui, title, right=""):
    ui.fill(0, 0, W, HEADER_H, C["bar"])
    ui.logo(12, 8)
    ui.text(54, 10, title, 21, C["text"], max_w=W - 76 - (ui.measure(right, 16)[0] if right else 0))
    if right:
        ui.text(W - 12, 14, right, 16, C["dim"], align="right")


def draw_footer(ui, hints):
    y = H - FOOTER_H
    ui.fill(0, y, W, FOOTER_H, C["bar"])
    x = 12
    for btn, label in hints:
        bw = max(24, ui.measure(btn, 14)[0] + 12)
        ui.rounded(x, y + 6, bw, 22, 11, C["text"])
        ui.text(x + bw // 2, y + 8, btn, 14, C["black"], align="center")
        x += bw + 6
        x += ui.text(x, y + 7, label, 16, C["dim"]) + 14


def draw_scrollbar(ui, top, height, first, visible, total):
    if total <= visible:
        return
    bar_h = max(24, height * visible // total)
    y = top + (height - bar_h) * first // max(1, total - visible)
    ui.fill(W - 5, top, 3, height, C["row"])
    ui.fill(W - 5, y, 3, bar_h, C["dim"])


def draw_badge(ui, x_right, y, level, label):
    color = C.get(level, C["faint"])
    w = ui.measure(label, 13)[0] + 14
    ui.rounded(x_right - w, y, w, 20, 10, color)
    ui.text(x_right - w // 2, y + 2, label, 13, C["text"], align="center")
    return w


def game_badge(app, g):
    """(level, label) shown next to a game in lists and on its page."""
    if app.library.get(g["key"]) or (g["src"] == "pm" and app.pm.is_installed(g)):
        return "accent", "INSTALLED"
    if g.get("check"):
        return g["check"]
    if g["src"] == "pm":
        return "ok", "READY"
    if g["src"] != "itch":
        return ("ok", "READY") if g.get("system") and app.device.cores(g["system"]) else ("bad", "NO EMU")
    return "faint", "…"


def rating_text(r):
    """'★ 4.8 (116)' for lists."""
    return f"★ {r['value']:.1f} ({r['count']})" if r else ""


def rating_stars(r):
    full = int(round(r["value"]))
    return "★" * full + "☆" * (5 - full)


def draw_cover(ui, images, url, x, y, w, h):
    ui.fill(x, y, w, h, C["row"])
    if url:
        images.want(url)
        ui.image(url, images.data.get(url), x, y, w, h, fit=True)


def wrap_paragraphs(ui, text, size, width, max_lines=200):
    lines = []
    for para in (text or "").split("\n"):
        para = para.strip()
        if not para:
            if lines and lines[-1]:
                lines.append("")
            continue
        lines += ui.wrap(para, size, width, 50)
        if len(lines) >= max_lines:
            break
    return lines[:max_lines]


# ------------------------------------------------------------------ screens
class Screen:
    def __init__(self, app):
        self.app = app
        self.ui = app.ui

    def handle(self, b):
        pass

    def update(self):
        pass

    def draw(self):
        pass


class MenuList(Screen):
    """A list of (key, title, subtitle, level) rows."""
    ROW_H = 60
    TITLE = ""

    def __init__(self, app):
        super().__init__(app)
        self.sel = 0
        self.top = 0

    def rows(self):
        return []

    def choose(self, key):
        pass

    def hints(self):
        return [("A", _("Select")), ("B", _("Back"))]

    def handle(self, b):
        rows = self.rows()
        if not rows:
            if b in ("B", "SELECT"):
                self.back()
            return
        if b == "UP":
            self.sel = (self.sel - 1) % len(rows)
        elif b == "DOWN":
            self.sel = (self.sel + 1) % len(rows)
        elif b == "A":
            self.choose(rows[self.sel][0])
        elif b in ("B", "SELECT"):
            self.back()
        else:
            self.other(b, rows[self.sel][0])

    def other(self, b, key):
        pass

    def back(self):
        self.app.pop()

    def right_text(self):
        return ""

    def draw(self):
        ui = self.ui
        draw_header(ui, _(self.TITLE), self.right_text())
        rows = self.rows()
        area_top = HEADER_H + 10
        visible = (H - FOOTER_H - area_top - 4) // self.ROW_H
        self.sel = min(self.sel, max(0, len(rows) - 1))
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + visible:
            self.top = self.sel - visible + 1
        for i in range(self.top, min(len(rows), self.top + visible)):
            key, title, sub, level = rows[i]
            y = area_top + (i - self.top) * self.ROW_H
            if i == self.sel:
                ui.rounded(10, y, W - 24, self.ROW_H - 6, 8, C["sel"])
                ui.fill(10, y + 10, 4, self.ROW_H - 26, C["accent"])
            title_color = C["dim"] if level in ("bad", "lock") else C["accent"] if level == "accent" else C["text"]
            sub_color = C["bad"] if level == "bad" else C["accent"] if level in ("lock", "accent") else C["dim"]
            ui.text(28, y + 6, title, 20, title_color, max_w=W - 70)
            ui.text(28, y + 32, sub, 14, sub_color, max_w=W - 70)
        draw_scrollbar(ui, area_top, visible * self.ROW_H, self.top, visible, len(rows))
        draw_footer(ui, self.hints())


class HomeScreen(MenuList):
    TITLE = "Game Manager"

    def rows(self):
        app = self.app
        rows = []
        u = app.updater
        if u.state in ("available", "downloading", "ready") and u.info:
            sub = {"available": _("What's new · A to update"), "downloading": _("Downloading {n}%", n=int(u.progress * 100)),
                   "ready": _("Downloaded · restart to finish")}[u.state]
            rows.insert(0, ("update", _("Update available: {v}", v=u.info['version']), sub, "accent"))
        rows += [("mine", _("My games"), _("Every game on your SD card: start, uninstall, favourites, box art"), None),
                 ("search", _("Search"), _("Search all the sources below by title"), None),
                 ("top", _("Top rated"), _("The highest-rated free homebrew on itch.io, by system"), None),
                 ("hbh", "Homebrew Hub", _("Free Game Boy, Game Boy Color, GBA and NES homebrew"), None),
                 ("itch", "itch.io", _("Free homebrew for NES, SNES, Game Boy, GBA, Mega Drive and more"), None),
                 ("pm", _("PortMaster ports"), _("Ready-to-run PC games ported to handhelds, installed into Ports"), None)]
        if app.itch.has_key:
            rows.append(("itchlib", _("My itch.io library"), _("Games you have claimed or bought on itch.io"), None))
        return rows

    def right_text(self):
        v = f"v{APP_VERSION}"
        return v if self.app.device.is_muos else _("{edition} · test mode", edition=v)

    def hints(self):
        return [("A", _("Open")), ("START", _("Menu")), ("B", _("Quit"))]

    def choose(self, key):
        app = self.app
        if key == "update":
            app.push(UpdateScreen(app))
        elif key == "hbh":
            app.push(SystemScreen(app, app.hbh))
        elif key == "itch":
            app.push(SystemScreen(app, app.itch))
        elif key == "top":
            app.push(SystemScreen(app, app.itch, sort="top"))
        elif key == "pm":
            src = app.pm
            sid = src.systems()[0]
            app.push(GameListScreen(app, src.NAME, lambda page, sort, genre: src.browse(sid, page, sort, genre),
                                    sorts=src.SORTS, server_sorts=src.SERVER_SORTS, genres=src.GENRES))
        elif key == "itchlib":
            app.push(GameListScreen(app, _("My itch.io library"), lambda page, sort, genre: app.itch.library(page),
                                    sorts=[("owned", "Newest first"), ("top", "Top rated first"), ("az", "A to Z")],
                                    server_sorts={"owned"}))
        elif key == "search":
            app.push(KeyboardScreen(app, app.last_search, _("Game title"), app.search))
        elif key == "mine":
            app.push(MyGamesScreen(app))

    def other(self, b, key):
        if b == "START":
            self.app.push(SettingsScreen(self.app))

    def back(self):
        self.app.running = False


SORT_OPTIONS = {
    "hbh": [("new", "Newest first"), ("top", "Top rated first"), ("az", "A to Z")],
    "itch": [("popular", "Most popular"), ("top", "Top rated first"), ("new", "Newest first"), ("az", "A to Z")],
    "search": [("relevance", "Best match"), ("top", "Top rated first"), ("az", "A to Z")],
}


class SystemScreen(MenuList):
    def __init__(self, app, source, sort=None):
        super().__init__(app)
        self.source = source
        self.sort = sort
        self.TITLE = _("Top rated") if sort == "top" else source.NAME

    def rows(self):
        rows = []
        for sid in self.source.systems():
            cores = self.app.device.cores(sid)
            if cores:
                sub = _("Emulator: {names}", names=", ".join(c.name for c in cores[:3]))
            else:
                sub = _("No emulator for this system on this device")
            rows.append((sid, BY_ID[sid].name, sub, None if cores else "bad"))
        return rows

    def choose(self, sid):
        src = self.source
        sorts = getattr(src, "SORTS", None) or SORT_OPTIONS["hbh" if src is self.app.hbh else "itch"]
        genres = GENRES if src in (self.app.hbh, self.app.itch) else None
        self.app.push(GameListScreen(self.app, f"{src.NAME} · {BY_ID[sid].name}",
                                     lambda page, sort, genre: src.browse(sid, page, sort, genre),
                                     sorts=sorts, sort=self.sort or sorts[0][0],
                                     server_sorts=src.SERVER_SORTS, genres=genres))


class GameListScreen(Screen):
    ROW_H = 88
    COVER_W, COVER_H = 104, 78

    def __init__(self, app, title, loader, sorts=None, sort=None, server_sorts=(), genres=None):
        """loader(page, sort, genre) -> (games, more, total)."""
        super().__init__(app)
        self.base_title = title
        self.loader = loader
        self.sorts = sorts or SORT_OPTIONS["search"]
        self.sort = sort or self.sorts[0][0]
        self.server_sorts = set(server_sorts)
        self.genres = genres
        self.genre = None
        self.reset()

    def reset(self):
        if getattr(self, "task", None):
            self.task.cancel()
        self.games = []
        self.total = None
        self.page = 0
        self.more = True
        self.task = None
        self.error = None
        self.sel_key = None
        self.top = 0
        self.auto_loads = 0
        self.load_more()

    @property
    def title(self):
        genre = dict(self.genres or []).get(self.genre)
        return f"{self.base_title} · {_(genre)}" if genre else self.base_title

    def set_options(self, sort, genre):
        reload = (genre != self.genre) or (sort != self.sort and (sort in self.server_sorts
                                                                   or self.sort in self.server_sorts))
        self.sort, self.genre = sort, genre
        if reload:
            self.reset()

    def load_more(self):
        if self.task or not self.more:
            return
        page = self.page + 1
        server_sort = self.sort if self.sort in self.server_sorts else self.sorts[0][0]
        loader, genre = self.loader, self.genre
        self.task = Task(lambda t: loader(page, server_sort, genre))

    def visible_games(self):
        games = self.games
        if self.app.hide_pc_only:
            games = [g for g in games if (g.get("check") or ("", ""))[0] != "bad" or g["key"] == self.sel_key]
        if self.sort not in self.server_sorts:
            if self.sort == "top":
                games = sorted(games, key=lambda g: (-(g.get("rating") or {}).get("value", -1),
                                                     -(g.get("rating") or {}).get("count", 0)))
            elif self.sort == "az":
                games = sorted(games, key=lambda g: g["title"].lower())
        return games

    def update(self):
        t = self.task
        if t and t.done:
            self.task = None
            if t.cancelled:
                return
            if t.error:
                self.error = t.error
                self.more = False
            elif t.result:
                games, more, total = t.result
                known = {g["key"] for g in self.games}
                self.games += [g for g in games if g["key"] not in known]
                self.more = more and bool(games)
                self.total = total
                self.page += 1
                if self.sel_key is None and self.games:
                    self.sel_key = self.games[0]["key"]
        games = self.visible_games()
        # Keep loading (up to 8 extra pages) while the filter leaves too few games on screen
        if self.more and not self.task and len(games) < 12 and self.auto_loads < 8:
            self.auto_loads += 1
            self.load_more()

    def _index(self, games):
        for i, g in enumerate(games):
            if g["key"] == self.sel_key:
                return i
        return 0

    def handle(self, b):
        games = self.visible_games()
        n = len(games)
        i = self._index(games)
        if b == "UP":
            i = max(0, i - 1)
        elif b == "DOWN":
            i = min(n - 1, i + 1)
        elif b == "L1":
            i = max(0, i - 4)
        elif b == "R1":
            i = min(n - 1, i + 4)
        elif b == "A" and n:
            self.app.push(DetailsScreen(self.app, games[i]))
        elif b == "X":
            self.app.push(KeyboardScreen(self.app, self.app.last_search, _("Game title"), self.app.search))
        elif b == "Y":
            self.app.push(FilterScreen(self.app, self))
        elif b in ("B", "SELECT"):
            if self.task:
                self.task.cancel()
            self.app.pop()
            return
        if n:
            self.sel_key = games[max(0, min(i, n - 1))]["key"]
            if i >= n - 4:
                self.load_more()

    def draw(self):
        ui = self.ui
        self.update()
        games = self.visible_games()
        sort_label = _(dict(self.sorts).get(self.sort, ""))
        if self.sort not in self.server_sorts and self.sort == "top":
            sort_label = _("Rated first")
        count = _("{n} games", n=self.total) if self.total else (_("{n} shown", n=len(games)) if games else "")
        count = " · ".join(x for x in (sort_label if self.sort != self.sorts[0][0] else "", count) if x)
        draw_header(ui, self.title, count)
        area_top = HEADER_H + 4
        visible = (H - FOOTER_H - area_top) // self.ROW_H
        sel = self._index(games)
        if sel < self.top:
            self.top = sel
        elif sel >= self.top + visible:
            self.top = sel - visible + 1
        if not games:
            if self.task:
                ui.spinner(W // 2, 220)
                ui.text(W // 2, 250, _("Loading…"), 18, C["dim"], align="center")
            else:
                msg = tr(self.error) or _("Nothing found.")
                for li, line in enumerate(ui.wrap(msg, 18, W - 120, 4)):
                    ui.text(W // 2, 200 + li * 26, line, 18, C["dim"], align="center")
        for i in range(self.top, min(len(games), self.top + visible + 1)):
            g = games[i]
            y = area_top + (i - self.top) * self.ROW_H
            if i == sel:
                ui.rounded(6, y + 2, W - 18, self.ROW_H - 4, 10, C["sel"])
            self.app.inspector.want(g)
            draw_cover(ui, self.app.images, g.get("cover"), 14, y + 5, self.COVER_W, self.COVER_H)
            lx = 14 + self.COVER_W + 12
            level, label = self.badge(g)
            bw = draw_badge(ui, W - 22, y + 8, level, label) if label else 0
            ui.text(lx, y + 6, g["title"], 19, C["text"], max_w=W - lx - bw - 34)
            rw = 0
            if g.get("rating"):
                rw = ui.text(W - 22, y + 33, rating_text(g["rating"]), 15, C["star"], align="right") + 12
            meta = " · ".join(s for s in (g.get("author"), BY_ID[g["system"]].name if g.get("system") else "") if s)
            ui.text(lx, y + 33, meta, 15, C["dim"], max_w=W - lx - 24 - rw)
            ui.text(lx, y + 56, g.get("summary") or "", 14, C["faint"], max_w=W - lx - 24)
        if self.task and games:
            ui.spinner(W // 2, H - FOOTER_H - 16, 9)
        draw_scrollbar(ui, area_top, visible * self.ROW_H, self.top, visible, len(games))
        draw_footer(ui, [("A", _("Details")), ("Y", _("Sort & filter")), ("X", _("Search")), ("B", _("Back"))])

    def badge(self, g):
        return game_badge(self.app, g)


class FilterScreen(MenuList):
    TITLE = "Sort & filter"
    ROW_H = 44

    def __init__(self, app, lst):
        super().__init__(app)
        self.lst = lst

    def rows(self):
        lst = self.lst
        rows = [(f"sort:{k}", ("● " if k == lst.sort else "○ ") + _(label), "", None) for k, label in lst.sorts]
        if lst.genres:
            rows.append(("genre:", ("● " if not lst.genre else "○ ") + _("All genres"), "", None))
            rows += [(f"genre:{k}", ("● " if k == lst.genre else "○ ") + _(label), "", None) for k, label in lst.genres]
        return rows

    def choose(self, key):
        kind, _sep, val = key.partition(":")
        if kind == "sort":
            self.lst.set_options(val, self.lst.genre)
        else:
            self.lst.set_options(self.lst.sort, val or None)

    def hints(self):
        return [("A", _("Choose")), ("B", _("Done"))]

    def draw(self):
        ui = self.ui
        draw_header(ui, _(self.TITLE))
        rows = self.rows()
        n_sorts = len(self.lst.sorts)
        area_top = HEADER_H + 6
        visible = (H - FOOTER_H - area_top - 4) // self.ROW_H
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + visible:
            self.top = self.sel - visible + 1
        for i in range(self.top, min(len(rows), self.top + visible)):
            y = area_top + (i - self.top) * self.ROW_H
            if i == self.sel:
                ui.rounded(10, y, W - 24, self.ROW_H - 4, 8, C["sel"])
            group = _("Sort") if i < n_sorts else _("Genre")
            if i in (0, n_sorts):
                ui.text(W - 26, y + 11, group, 14, C["faint"], align="right")
            ui.text(28, y + 9, rows[i][1], 19, C["text"] if rows[i][1].startswith("●") else C["dim"])
        draw_scrollbar(ui, area_top, visible * self.ROW_H, self.top, visible, len(rows))
        if self.lst.sort not in self.lst.server_sorts and self.lst.sort == "top":
            ui.text(W // 2, H - FOOTER_H - 22, _("Sorted by the ratings of the games loaded so far"), 13,
                    C["faint"], align="center")
        draw_footer(ui, self.hints())


class DetailsScreen(Screen):
    COVER_W, COVER_H = 240, 180

    def __init__(self, app, game):
        super().__init__(app)
        self.game = game
        self.file_i = 0
        self.shot_i = 0
        self.scroll = 0
        self.task = None
        self.error = None
        files = game.get("files")
        stale = app.itch.has_key and files and not any(f.get("upload_id") for f in files)
        if game["src"] == "itch" and (files is None or stale):
            self.task = Task(lambda t: app.itch.inspect(game, app.device))
        else:
            app.inspector.want(game)

    def files(self):
        return self.game.get("files") or []

    def pictures(self):
        pics = [self.game.get("cover")] + list(self.game.get("screenshots") or [])
        return list(dict.fromkeys(p for p in pics if p))

    def current_file(self):
        fs = self.files()
        return fs[self.file_i] if fs else None

    def handle(self, b):
        fs = self.files()
        rec = self.app.library.get(self.game["key"])
        if b in ("B", "SELECT"):
            self.app.pop()
        elif b in ("LEFT", "RIGHT") and len(fs) > 1:
            self.file_i = (self.file_i + (1 if b == "RIGHT" else -1)) % len(fs)
        elif b == "UP":
            self.scroll = max(0, self.scroll - 3)
        elif b == "DOWN":
            self.scroll += 3
        elif b in ("L1", "R1") and len(self.pictures()) > 1:
            self.shot_i = (self.shot_i + (1 if b == "R1" else -1)) % len(self.pictures())
        elif b == "A":
            self.install()
        elif b == "Y" and rec:
            self.app.try_game(rec)
        elif b == "X" and rec:
            self.app.confirm_uninstall(rec)

    def install(self):
        app, g, f = self.app, self.game, self.current_file()
        if self.task and not self.task.done:
            return
        if not f:
            app.push(MessageScreen(app, _("Nothing to download"), _("This game has no downloadable file.")))
        elif f["kind"] == "pc":
            app.push(MessageScreen(app, _("PC version"), _("This file is for PC or phones, not a console ROM. "
                                                         "Use ◀ ▶ to pick another file if the game has one.")))
        elif g["src"] == "itch" and not app.itch.has_key:
            app.push(MessageScreen(app, _("itch.io API key needed"),
                                   _("Browsing itch.io works without an account, but downloading needs your "
                                     "own free API key.\nCreate one at {url}, then add it in "
                                     "START → Settings on the home screen, or in config.ini.", url=ITCH_KEY_URL)))
        elif f.get("install") != "port" and f.get("system") and not app.device.cores(f["system"]):
            app.push(MessageScreen(app, _("No emulator"), _("This device has no emulator for {system}.",
                                                            system=BY_ID[f['system']].name)))
        else:
            app.push(InstallScreen(app, g, f))

    def draw(self):
        ui, g, app = self.ui, self.game, self.app
        loading = self.task and not self.task.done
        if self.task and self.task.done and self.task.error and not g.get("files"):
            g["files"] = []
            g["check"] = ("warn", "?")
            self.error = self.task.error
        draw_header(ui, g["title"])
        top = HEADER_H + 12
        pics = self.pictures()
        self.shot_i = min(self.shot_i, max(0, len(pics) - 1))
        draw_cover(ui, app.images, pics[self.shot_i] if pics else None, 14, top, self.COVER_W, self.COVER_H)
        if len(pics) > 1:
            label = f"L1 ◀  {self.shot_i + 1}/{len(pics)}  ▶ R1"
            lw = ui.measure(label, 13)[0] + 14
            ui.rounded(14 + (self.COVER_W - lw) // 2, top + self.COVER_H - 24, lw, 20, 10, C["black"], 200)
            ui.text(14 + self.COVER_W // 2, top + self.COVER_H - 22, label, 13, C["text"], align="center")
            for p in pics[self.shot_i + 1:self.shot_i + 2]:
                app.images.want(p)  # load the next picture ahead of time
        x = 14 + self.COVER_W + 16
        cw = W - x - 14
        y = top
        for line in ui.wrap(g["title"], 21, cw, 2):
            ui.text(x, y, line, 21, C["text"])
            y += 28
        if g.get("author"):
            ui.text(x, y, g["author"], 16, C["dim"], max_w=cw)
            y += 24
        r = g.get("rating")
        if r:
            sw = ui.text(x, y, rating_stars(r), 17, C["star"])
            ui.text(x + sw + 8, y + 1, f"{r['value']:.1f} · " + (_("1 rating") if r['count'] == 1 else
                                                                 _("{n} ratings", n=r['count'])),
                    15, C["dim"], max_w=cw - sw - 8)
            y += 24
        elif "rating" in g and g["src"] in ("hbh", "itch"):
            ui.text(x, y, _("No ratings yet") if g.get("rating_page") else _("No ratings (not on itch.io)"), 14, C["faint"])
            y += 22
        f = self.current_file()
        installed = app.library.get(g["key"])
        sid = (installed or {}).get("system") or (f or {}).get("system") or g.get("system")
        if sid:
            ui.text(x, y, BY_ID[sid].name, 16, C["dim"], max_w=cw)
            y += 24
        if g.get("license"):
            ui.text(x, y, _("License: {name}", name=g["license"]), 14, C["faint"], max_w=cw)
            y += 22
        rec = app.library.get(g["key"])
        level, label = game_badge(app, g)
        if label == "…":
            level, label = None, None
        if loading:
            level, label = "faint", "CHECKING…"
        if label:
            ui.rounded(x, y + 4, ui.measure(label, 13)[0] + 14, 20, 10, C.get(level, C["faint"]))
            ui.text(x + 7, y + 6, label, 13, C["text"])

        # file line
        y = top + self.COVER_H + 12
        ui.fill(14, y, W - 28, 1, C["row"])
        y += 8
        fs = self.files()
        if loading:
            ui.text(14, y, _("Checking the downloads…"), 16, C["dim"])
        elif f:
            arrows = f"◀ {self.file_i + 1}/{len(fs)} ▶  " if len(fs) > 1 else ""
            kind = {"rom": "ROM", "zip": "ZIP", "maybe": "ROM?", "pc": _("PC/phone"), "other": _("file")}[f["kind"]]
            kind = _("Port") if f.get("install") == "port" else kind
            size = fmt_size(f.get("size")) or f.get("size_text") or ""
            info = " · ".join(s for s in (kind, size) if s)
            ui.text(14, y, f"{arrows}{f['name']}", 16, C["text"] if f["kind"] != "pc" else C["dim"], max_w=W - 150)
            ui.text(W - 14, y, info, 14, C["dim"], align="right")
        else:
            ui.text(14, y, tr(self.error) or _("No downloadable files found."), 16, C["dim"], max_w=W - 28)
        y += 28

        # notes from a previous install + description
        lines = []
        if rec:
            lines.append(_("Installed: {path}", path=os.path.relpath(rec['path'], os.path.dirname(app.device.roms_path))))
            status = {"works": _("You marked it as working."), "broken": _("You marked it as not working.")}.get(rec.get("status"))
            lines.append(status or _("Not tried yet. Press Y to try it."))
            lines += [f"• {tr(n)}" for n in rec.get("notes") or []]
            lines.append("")
        lines += wrap_paragraphs(ui, g.get("description") or g.get("summary") or "", 15, W - 40)
        room = (H - FOOTER_H - 8 - y) // 21
        self.scroll = max(0, min(self.scroll, max(0, len(lines) - room)))
        for i, line in enumerate(lines[self.scroll:self.scroll + room]):
            ui.text(20, y + i * 21, line, 15, C["dim"])
        hints = [("A", _("Reinstall") if rec else _("Install"))]
        if rec:
            hints += [("Y", _("Try it")), ("X", _("Uninstall"))]
        if len(fs) > 1:
            hints.append(("◀▶", _("File")))
        hints.append(("B", _("Back")))
        draw_footer(ui, hints)


class InstallScreen(Screen):
    def __init__(self, app, game, f):
        super().__init__(app)
        self.game = game
        source = app.source_for(game)

        def job(t):
            cover = app.images.data.get(game.get("cover")) if game.get("cover") else None
            if game.get("cover") and not cover:
                try:
                    cover = net.get_bytes(game["cover"], timeout=15, limit=4 * 1024 * 1024)
                except net.NetError:
                    cover = None
            return installer.install(game, f, app.device, source, app.library,
                                     os.path.join(app.app_dir, "data"), t,
                                     save_art=art.save_png, cover_data=cover)

        self.task = Task(job)

    def handle(self, b):
        t = self.task
        if not t.done:
            if b in ("B", "SELECT"):
                t.cancel()
            return
        if b == "Y" and t.result:
            self.app.pop()
            self.app.try_game(t.result)
        elif b in ("A", "B", "START", "SELECT"):
            self.app.pop()

    def draw(self):
        ui, t = self.ui, self.task
        self.app.draw_below(self)
        ui.fill(0, 0, W, H, C["black"], 170)
        ui.rounded(50, 110, W - 100, 260, 14, C["bar"])
        ui.fill(50, 110, W - 100, 5, C["accent"])
        ui.text(W // 2, 130, self.game["title"], 21, C["text"], align="center", max_w=W - 140)
        if not t.done:
            ui.text(W // 2, 180, tr(t.status) or _("Starting…"), 17, C["dim"], align="center", max_w=W - 140)
            if t.progress is None:
                ui.spinner(W // 2, 240, 14)
            else:
                ui.fill(100, 234, W - 200, 10, C["row"])
                ui.fill(100, 234, int((W - 200) * min(1.0, t.progress)), 10, C["accent"])
                ui.text(W // 2, 252, f"{int(t.progress * 100)}%", 14, C["dim"], align="center")
            ui.text(W // 2, 340, _("B to cancel"), 14, C["faint"], align="center")
            return
        if t.cancelled and not t.error:
            lines, foot = [_("Cancelled. Nothing was installed.")], _("Press A to continue")
        elif t.error:
            lines, foot = [_("Couldn't install:")] + ui.wrap(tr(t.error), 16, W - 150, 6), _("Press A to continue")
        else:
            r = t.result
            where = os.path.relpath(r["path"], os.path.dirname(self.app.device.roms_path))
            lines = [_("Installed ✓"), where] + [f"• {tr(n)}" for n in r.get("notes") or []][:4]
            lines.append(_("It is in the muOS game list now."))
            foot = _("Y = Try it now · A = Done")
        y = 172
        for i, line in enumerate(lines):
            for part in ui.wrap(line, 16, W - 150, 2):
                if y > 320:
                    break
                ui.text(W // 2, y, part, 17 if i == 0 else 15, C["text"] if i == 0 else C["dim"], align="center")
                y += 23
        ui.text(W // 2, 340, foot, 15, C["faint"], align="center")


class TryResultScreen(Screen):
    def __init__(self, app, rec, secs, rc, core_name):
        super().__init__(app)
        self.rec, self.secs, self.rc, self.core = rec, secs, rc, core_name
        self.quick = secs < 4

    def handle(self, b):
        lib = self.app.library
        if b == "A":
            lib.set_status(self.rec["key"], "works")
            self.app.pop()
        elif b == "X":
            lib.set_status(self.rec["key"], "broken")
            self.app.pop()
        elif b in ("B", "SELECT"):
            self.app.pop()

    def draw(self):
        ui = self.ui
        self.app.draw_below(self)
        ui.fill(0, 0, W, H, C["black"], 170)
        ui.rounded(50, 120, W - 100, 240, 14, C["bar"])
        ui.fill(50, 120, W - 100, 5, C["warn"] if self.quick else C["accent"])
        ui.text(W // 2, 140, _("Did it work?"), 22, C["text"], align="center")
        if self.quick:
            body = _("The game closed after {s} s (core: {core}). It may not run on this device. Check "
                     "logs/tryit.log in the app folder.", s=f"{self.secs:.0f}", core=self.core)
        else:
            body = _("You played for {m} min {s} s with the {core} core.", m=int(self.secs // 60),
                     s=int(self.secs % 60), core=self.core)
        y = 186
        for line in ui.wrap(body, 16, W - 150, 4):
            ui.text(W // 2, y, line, 16, C["dim"], align="center")
            y += 23
        draw_footer(ui, [("A", _("It works")), ("X", _("It doesn't")), ("B", _("Not sure"))])


class MyGamesScreen(MenuList):
    """Shelves (recently played, favourites), then every system that has games."""
    TITLE = "My games"

    def __init__(self, app):
        super().__init__(app)
        self.task = None
        self.rescan()

    def rescan(self):
        app = self.app
        app.playtime.reload()
        self.task = Task(lambda t: romscan.scan(app.device, app.rom_roots()))

    def library(self):
        t = self.task
        return t.result if t and t.done and t.result else {}

    def all_games(self):
        return [g for games in self.library().values() for g in games]

    def play(self, g):
        return self.app.playtime.get(mygames.union_path(g["path"], self.app.rom_roots()))

    def recent(self):
        played = [(self.play(g), g) for g in self.all_games()]
        played = [(p, g) for p, g in played if p and p["last"]]
        return [g for p, g in sorted(played, key=lambda x: -x[0]["last"])][:20]

    def favourites(self):
        return sorted((g for g in self.all_games() if g["path"] in self.app.favs), key=lambda g: g["name"].lower())

    def rows(self):
        lib = self.library()
        if not lib:
            return []
        rows = []
        recent = self.recent()
        if recent:
            rows.append(("recent", _("Recently played"), " · ".join(g["name"] for g in recent[:3]), None))
        favs = self.favourites()
        if favs:
            rows.append(("fav", _("★ Favourites"), _("1 game") if len(favs) == 1 else _("{n} games", n=len(favs)), None))
        for system in sorted(lib, key=str.lower):
            games = lib[system]
            n = len(games)
            played = sum((self.play(g) or {}).get("total", 0) for g in games)
            sub = (_("1 game") if n == 1 else _("{n} games", n=n)) + f" · {fmt_size(sum(g['size'] for g in games))}"
            if played:
                sub += _(" · played {time}", time=tr(mygames.fmt_duration(played)))
            emu = self.app.device.cores_for(system)
            if not emu:
                sub += _(" · no emulator set up")
            rows.append((f"sys:{system}", system, sub, None if emu else "bad"))
        missing = sum(1 for g in self.all_games() if not mygames.box_path(self.app.device, g))
        rows.append(("art", _("Get box art for all games"),
                     (_("1 game without a cover · from the libretro thumbnail library") if missing == 1 else
                      _("{n} games without a cover · from the libretro thumbnail library", n=missing))
                     if missing else _("Every game has a cover"), None))
        return rows

    def right_text(self):
        n = len(self.all_games())
        return _("{n} games", n=n) if n else ""

    def choose(self, key):
        app = self.app
        if key == "recent":
            app.push(ShelfScreen(app, self, _("Recently played"), self.recent, sort="recent"))
        elif key == "fav":
            app.push(ShelfScreen(app, self, _("★ Favourites"), self.favourites))
        elif key == "art":
            games = self.all_games()
            if any(not mygames.box_path(app.device, g) for g in games):
                app.push(BoxArtScreen(app, games))
        elif key.startswith("sys:"):
            system = key[4:]
            app.push(ShelfScreen(app, self, system, lambda: self.library().get(system, [])))

    def draw(self):
        super().draw()
        t = self.task
        if not t.done:
            self.ui.spinner(W // 2, 220)
            self.ui.text(W // 2, 250, _("Looking for games…"), 18, C["dim"], align="center")
        elif t.error:
            for i, line in enumerate(self.ui.wrap(tr(t.error), 17, W - 120, 4)):
                self.ui.text(W // 2, 200 + i * 24, line, 17, C["dim"], align="center")
        elif not self.library():
            self.ui.text(W // 2, 220, _("No games found in the ROMS folders."), 17, C["faint"], align="center")


class ShelfScreen(Screen):
    """A list of games from the SD card: start, uninstall, favourite."""
    ROW_H = 70
    THUMB = 58
    SORTS = [("az", "A to Z"), ("played", "Most played"), ("recent", "Recently played")]

    def __init__(self, app, parent, title, provider, sort="az"):
        super().__init__(app)
        self.parent = parent
        self.title = title
        self.provider = provider
        self.sort = sort
        self.sel = 0
        self.top = 0
        self.thumbs = {}

    def play(self, g):
        return self.parent.play(g)

    def games(self):
        games = list(self.provider())
        if self.sort == "played":
            games.sort(key=lambda g: -(self.play(g) or {}).get("total", 0))
        elif self.sort == "recent":
            games.sort(key=lambda g: -(self.play(g) or {}).get("last", 0))
        return games

    def record_for(self, g):
        for rec in self.app.library.installed():
            if os.path.realpath(rec["path"]) == os.path.realpath(g["path"]):
                return rec
        return None

    def thumb(self, g):
        path = mygames.box_path(self.app.device, g)
        if not path:
            return None, None
        if path not in self.thumbs:
            try:
                with open(path, "rb") as f:
                    self.thumbs[path] = f.read()
            except OSError:
                self.thumbs[path] = b""
        return path, self.thumbs[path]

    def handle(self, b):
        games = self.games()
        n = len(games)
        if b in ("B", "SELECT"):
            self.app.pop()
        elif b == "START":
            keys = [k for k, _label in self.SORTS]
            self.sort = keys[(keys.index(self.sort) + 1) % len(keys)]
            self.sel = 0
        elif not n:
            return
        elif b == "UP":
            self.sel = (self.sel - 1) % n
        elif b == "DOWN":
            self.sel = (self.sel + 1) % n
        elif b == "L1":
            self.sel = max(0, self.sel - 5)
        elif b == "R1":
            self.sel = min(n - 1, self.sel + 5)
        elif b == "A":
            g = games[self.sel]
            rec = self.record_for(g)
            if rec:
                self.app.try_game(rec)
            else:
                self.app.start_game(g["system"], g["path"], g["name"])
            self.app.playtime.reload()
        elif b == "Y":
            self.app.favs.toggle(games[self.sel]["path"])
        elif b == "X":
            self.confirm_uninstall(games[self.sel])

    def confirm_uninstall(self, g):
        where = os.path.relpath(g["folder"] or g["path"], os.path.dirname(self.app.device.roms_path))
        what = (_("the folder {where} ({n} files, {size})", where=where, n=len(g['files']), size=fmt_size(g['size']))
                if g["folder"] else f"{where}" + (_(" and {n} more file(s)", n=len(g['files']) - 1)
                                                  if len(g["files"]) > 1 else "") + f" ({fmt_size(g['size'])})")
        self.app.push(ConfirmScreen(self.app, _("Uninstall game?"),
                                    _("Delete “{name}”: {what}, plus its box art?\nYour save files are kept.",
                                      name=g['name'], what=what), lambda: self.uninstall(g)))

    def uninstall(self, g):
        app = self.app
        try:
            romscan.remove(g, app.rom_roots(), app.device)
        except Exception as e:  # noqa: BLE001
            app.push(MessageScreen(app, _("Couldn't uninstall"), tr(str(e))))
            return
        rec = self.record_for(g)
        if rec:
            app.library.remove(rec["key"])
        app.favs.forget(g["path"])
        lib = self.parent.library()
        if g in lib.get(g["system"], []):
            lib[g["system"]].remove(g)
            if not lib[g["system"]]:
                lib.pop(g["system"], None)
        if not self.games():
            app.pop()
        app.push(MessageScreen(app, _("Uninstalled"), _("“{name}” was removed.", name=g['name'])))

    def draw(self):
        ui = self.ui
        games = self.games()
        draw_header(ui, self.title, f"{_(dict(self.SORTS)[self.sort])} · {len(games)}")
        area_top = HEADER_H + 6
        visible = (H - FOOTER_H - area_top - 2) // self.ROW_H
        self.sel = min(self.sel, max(0, len(games) - 1))
        if self.sel < self.top:
            self.top = self.sel
        elif self.sel >= self.top + visible:
            self.top = self.sel - visible + 1
        if not games:
            ui.text(W // 2, 220, _("No games here yet."), 17, C["faint"], align="center")
        for i in range(self.top, min(len(games), self.top + visible)):
            g = games[i]
            y = area_top + (i - self.top) * self.ROW_H
            if i == self.sel:
                ui.rounded(8, y, W - 20, self.ROW_H - 4, 8, C["sel"])
            tx, ty = 16, y + (self.ROW_H - 4 - self.THUMB) // 2
            key, data = self.thumb(g)
            ui.fill(tx, ty, self.THUMB, self.THUMB, C["row"])
            if key:
                ui.image(key, data, tx, ty, self.THUMB, self.THUMB, fit=True)
            else:
                ext = os.path.splitext(g["path"])[1].lstrip(".").upper()[:4]
                ui.text(tx + self.THUMB // 2, ty + self.THUMB // 2 - 9, ext, 14, C["faint"], align="center")
            lx = tx + self.THUMB + 12
            bw = 0
            rec = self.record_for(g)
            if rec:
                level, label = {"works": ("ok", "WORKS"), "broken": ("bad", "DIDN'T RUN")}.get(
                    rec.get("status"), ("faint", "NOT TRIED"))
                bw = draw_badge(ui, W - 22, y + 8, level, label) + 8
            fav = g["path"] in self.app.favs
            name_x = lx
            if fav:
                name_x += ui.text(lx, y + 8, "★ ", 18, C["star"])
            ui.text(name_x, y + 8, g["name"], 18, C["text"], max_w=W - name_x - 30 - bw)
            ext = os.path.splitext(g["path"])[1].lstrip(".").upper()
            if g["folder"]:
                kind = _("Folder · {n} files · {size}", n=len(g['files']), size=fmt_size(g['size']))
            elif len(g["files"]) > 1:
                kind = _("{ext} + {n} more files · {size}", ext=ext, n=len(g['files']) - 1, size=fmt_size(g['size']))
            else:
                kind = f"{ext} · {fmt_size(g['size'])}"
            if self.title != g["system"]:
                kind = f"{g['system']} · {kind}"
            ui.text(lx, y + 32, kind, 13, C["dim"], max_w=W - lx - 30)
            p = self.play(g)
            if p and p["launches"]:
                played = (_("Played {time} · {n}×", time=tr(mygames.fmt_duration(p['total'])), n=p['launches'])
                          + (_(" · last {ago}", ago=tr(mygames.fmt_ago(p['last']))) if p["last"] else ""))
                ui.text(lx, y + 49, played, 13, C["faint"], max_w=W - lx - 30)
            else:
                ui.text(lx, y + 49, _("Not played yet"), 13, C["faint"])
        draw_scrollbar(ui, area_top, visible * self.ROW_H, self.top, visible, len(games))
        if games:
            fav_label = _("Unfavourite") if games[self.sel]["path"] in self.app.favs else _("Favourite")
            draw_footer(ui, [("A", _("Start")), ("Y", fav_label), ("X", _("Uninstall")), ("START", _("Sort")), ("B", _("Back"))])
        else:
            draw_footer(ui, [("B", _("Back"))])


class BoxArtScreen(Screen):
    """Downloads missing box art for a list of games, with progress."""

    def __init__(self, app, games):
        super().__init__(app)
        self.task = Task(lambda t: mygames.fetch_box_art(app.device, app.boxart, games, t, art.save_png))

    def handle(self, b):
        if not self.task.done:
            if b in ("B", "SELECT"):
                self.task.cancel()
            return
        if b in ("A", "B", "START", "SELECT"):
            self.app.pop()

    def draw(self):
        ui, t = self.ui, self.task
        self.app.draw_below(self)
        ui.fill(0, 0, W, H, C["black"], 170)
        ui.rounded(50, 110, W - 100, 260, 14, C["bar"])
        ui.fill(50, 110, W - 100, 5, C["accent"])
        ui.text(W // 2, 130, _("Getting box art"), 21, C["text"], align="center")
        if not t.done:
            ui.text(W // 2, 180, tr(t.status) or _("Loading the thumbnail list…"), 16, C["dim"], align="center",
                    max_w=W - 140)
            if t.progress is None:
                ui.spinner(W // 2, 240, 14)
            else:
                ui.fill(100, 234, W - 200, 10, C["row"])
                ui.fill(100, 234, int((W - 200) * min(1.0, t.progress)), 10, C["accent"])
                ui.text(W // 2, 252, f"{int(t.progress * 100)}%", 14, C["dim"], align="center")
            ui.text(W // 2, 340, _("B to stop"), 14, C["faint"], align="center")
            return
        if t.error:
            lines = [_("Couldn't get box art:")] + ui.wrap(tr(t.error), 16, W - 150, 5)
        elif t.cancelled:
            lines = [_("Stopped. Covers found so far were kept.")]
        else:
            added, missing, had = t.result
            lines = [_("Added 1 cover ✓") if len(added) == 1 else _("Added {n} covers ✓", n=len(added))]
            if missing:
                lines.append(_("No cover found for {n}: {names}", n=len(missing), names=", ".join(missing[:3])
                               + ("…" if len(missing) > 3 else "")))
            lines.append(_("They show in muOS and in My games now."))
        y = 176
        for i, line in enumerate(lines):
            for part in ui.wrap(line, 16, W - 150, 3):
                ui.text(W // 2, y, part, 17 if i == 0 else 15, C["text"] if i == 0 else C["dim"], align="center")
                y += 23
        ui.text(W // 2, 340, _("Press A to continue"), 15, C["faint"], align="center")


class SettingsScreen(MenuList):
    TITLE = "Settings"

    def rows(self):
        app = self.app
        key = app.itch.api_key
        shown = (key[:4] + "…" + key[-4:]) if len(key) > 10 else (_("set") if key else _("not set"))
        return [("language", _("Language"), i18n.label() + _(" · A to change"), None),
                ("key", _("itch.io API key"), _("Needed to download from itch.io · {shown}", shown=shown), None),
                ("folder", _("Games folder"), app.device.roms_path, None),
                ("hide", _("Hide PC-only itch.io games"), _("On") if app.hide_pc_only else _("Off"), None),
                ("updates", _("Check for updates"), _("On · a notice when a new version is out") if app.updater.enabled
                 else _("Off"), None),
                ("checknow", _("Check for updates now"), _("You have version {v}", v=APP_VERSION), None),
                ("privacy", _("Privacy"), _("What Game Manager sends, and what it never does"), None),
                ("about", _("About Game Manager"), _("Version {v}", v=APP_VERSION), None)]

    def choose(self, key):
        app = self.app
        if key == "language":
            app.change_language()
        elif key == "key":
            app.push(KeyboardScreen(app, app.itch.api_key, _("Paste or type the key from {url}", url=ITCH_KEY_URL),
                                    app.set_api_key, action=_("Save"), max_len=80))
        elif key == "folder":
            app.push(MessageScreen(app, _("Games folder"),
                                   _("Games are installed to {path}/<system>, the folders muOS already uses. Change "
                                     "roms_path in config.ini to use SD card 2 (/mnt/sdcard/ROMS).",
                                     path=app.device.roms_path)))
        elif key == "hide":
            app.hide_pc_only = not app.hide_pc_only
        elif key == "updates":
            app.updater.set_enabled(not app.updater.enabled)
        elif key == "checknow":
            if app.updater.state not in ("available", "downloading", "ready"):
                app.updater.state, app.updater.info = None, None
            app.push(UpdateScreen(app))
        elif key == "privacy":
            app.push(MessageScreen(app, _("Privacy"), _("Game Manager never reads or sends your saves, screenshots or files, and doesn't track what you play.\nFor updates it reads the latest release on GitHub (you can turn this off in Settings). It sends nothing about you or the handheld.\nTo list and download games it connects to Homebrew Hub, itch.io, PortMaster (GitHub) and the libretro box-art library; your searches go to those sites, and your itch.io API key only to itch.io.\nNo ads, no analytics, no tracking.")))
        elif key == "about":
            app.push(MessageScreen(app, f"Game Manager {APP_VERSION}",
                                   _("Free and open source (MIT License). Games come from Homebrew Hub, itch.io and "
                                     "PortMaster; box art for your own games from the libretro thumbnail library. "
                                     "Nothing is hosted by this app, and it isn't affiliated with any of them. Games "
                                     "belong to their creators.\nTested on Anbernic RG40XXH with muOS 2601.0 Jacaranda.\n"
                                     "© 2026 PocketKode · see LICENSE and the licenses/ folder.\n{url}",
                                     url="github.com/" + updater.REPO)))


class KeyboardScreen(Screen):
    LOWER = ["1234567890", "qwertyuiop", "asdfghjkl'", "zxcvbnm,.-"]
    UPPER = ["!?&#()@:/+", "QWERTYUIOP", "ASDFGHJKL\"", "ZXCVBNM;_="]
    KW, KH, GAP = 58, 50, 4

    def __init__(self, app, text, placeholder, on_submit, action=None, max_len=60):
        super().__init__(app)
        self.text = text or ""
        self.placeholder = placeholder
        self.on_submit = on_submit
        self.action = action = action or _("Search")
        self.max_len = max_len
        self.special = [(_("Shift"), "SHIFT", 2), (_("Space"), " ", 4), (_("Del"), "DEL", 2), (action, "GO", 2)]
        self.row, self.col = 1, 0
        self.shift = False

    def key_at(self, row, col):
        if row < 4:
            ch = (self.UPPER if self.shift else self.LOWER)[row][col]
            return ch, ch, col, 1
        start = 0
        for label, val, span in self.special:
            if col < start + span:
                return label, val, start, span
            start += span
        return self.special[-1][0], self.special[-1][1], 8, 2

    def press(self, val):
        if val == "SHIFT":
            self.shift = not self.shift
        elif val == "DEL":
            self.text = self.text[:-1]
        elif val == "GO":
            self.submit()
        elif len(self.text) < self.max_len:
            self.text += val
            if self.shift and self.row < 4:
                self.shift = False

    def submit(self):
        self.app.pop()
        self.on_submit(self.text.strip())

    def handle(self, b):
        if b == "UP":
            self.row = (self.row - 1) % 5
        elif b == "DOWN":
            self.row = (self.row + 1) % 5
        elif b == "LEFT":
            self.col = (self.key_at(self.row, self.col)[2] - 1) % 10
        elif b == "RIGHT":
            _label, _val, start, span = self.key_at(self.row, self.col)
            self.col = (start + span) % 10
        elif b == "A":
            self.press(self.key_at(self.row, self.col)[1])
        elif b == "B":
            if self.text:
                self.text = self.text[:-1]
            else:
                self.app.pop()
        elif b == "Y":
            self.press(" ")
        elif b == "X":
            self.shift = not self.shift
        elif b == "START":
            self.submit()
        elif b == "SELECT":
            self.app.pop()
        elif b == "L1":
            self.text = ""

    def draw(self):
        ui = self.ui
        draw_header(ui, self.action)
        box_y = HEADER_H + 20
        ui.rounded(20, box_y, W - 40, 50, 10, C["row"])
        shown = ui.ellipsize(self.text[::-1], 24, W - 90)[::-1] if self.text else ""
        tw = ui.text(36, box_y + 11, shown, 24, C["text"]) if shown else 0
        if not self.text:
            ui.text(36, box_y + 14, self.placeholder, 17, C["faint"], max_w=W - 90)
        if int(time.monotonic() * 2) % 2 == 0:
            ui.fill(36 + tw + 2, box_y + 12, 2, 28, C["accent"])
        kx0 = (W - (10 * self.KW + 9 * self.GAP)) // 2
        ky0 = box_y + 72
        cur_start = self.key_at(self.row, self.col)[2]
        for r in range(5):
            cols = range(10) if r < 4 else [0, 2, 6, 8]
            for c in cols:
                label, val, start, span = self.key_at(r, c)
                x = kx0 + start * (self.KW + self.GAP)
                y = ky0 + r * (self.KH + self.GAP)
                w = span * self.KW + (span - 1) * self.GAP
                sel = r == self.row and start == cur_start
                bg = C["accent"] if sel else C["key"]
                if val == "SHIFT" and self.shift and not sel:
                    bg = C["sel"]
                ui.rounded(x, y, w, self.KH, 8, bg)
                size = 22 if len(label) == 1 else 17
                ui.text(x + w // 2, y + (self.KH - size) // 2 - 2, label, size, C["text"], align="center")
        draw_footer(ui, [("A", _("Type")), ("B", _("Del")), ("Y", _("Space")), ("X", _("Shift")), ("START", self.action)])


class UpdateScreen(Screen):
    """A new version from GitHub: what's new, then download (checked: SHA-256 and PocketKode's signature)
    and restart to finish. Nothing is installed unless the person chooses Update."""

    def __init__(self, app):
        super().__init__(app)
        self.up = app.updater
        if self.up.state is None and not self.up.info:
            self.up.maybe_check(force=True)

    def handle(self, b):
        st = self.up.state
        if b == "A" and st == "available":
            self.up.download()
        elif b == "A" and st == "ready":
            self.app.restart = True  # mux_launch.sh starts the app again; main.py finishes the update first
            self.app.running = False
        elif b == "A" and st == "error":
            if self.up.info:
                self.up.state = "available"
                self.up.download()
            else:
                self.up.maybe_check(force=True)
        elif b in ("B", "SELECT"):
            self.app.pop()

    def draw(self):
        ui, u = self.ui, self.up
        info = u.info or {}
        new = info.get("version", "")
        draw_header(ui, _("Update"), f"Game Manager {u.version}")
        top = HEADER_H + 18
        if u.state == "checking":
            ui.text(24, top, _("Checking for updates") + "…", 20, C["dim"])
            ui.spinner(W // 2, top + 90, 12)
            draw_footer(ui, [("B", _("Back"))])
            return
        if u.state == "uptodate" or (u.state is None and not info):
            ui.text(24, top, _("You have the latest version."), 24, C["ok"], max_w=W - 48)
            draw_footer(ui, [("B", _("Back"))])
            return
        if u.state == "ready":
            ui.text(24, top, _("✓ Version {v} is ready", v=new), 26, C["ok"], max_w=W - 48)
            body = _("Downloaded and checked. Restart Game Manager to finish; your games and settings stay. "
                     "If you choose Later, it's installed the next time you open the app.")
            for i, line in enumerate(ui.wrap(body, 17, W - 48, 4)):
                ui.text(24, top + 48 + i * 26, line, 17, C["dim"])
            draw_footer(ui, [("A", _("Restart now")), ("B", _("Later"))])
            return
        if u.state == "error" and not info:  # the check itself failed: there is no new version to show
            for i, line in enumerate(ui.wrap(tr(u.error) or _("Something went wrong."), 18, W - 48, 4)):
                ui.text(24, top + i * 28, line, 18, C["warn"])
            draw_footer(ui, [("A", _("Try again")), ("B", _("Back"))])
            return
        ui.text(24, top, _("Version {v} is available", v=new), 24, C["text"], max_w=W - 48)
        size = f" · {info['size'] / 2**20:.1f} MB" if info.get("size") else ""
        ui.text(24, top + 36, _("You have {v}", v=u.version) + size, 16, C["dim"])
        y = top + 72
        ui.text(24, y, _("What's new:"), 17, C["text"])
        y += 28
        for note in (info.get("notes") or [])[:5]:
            for line in ui.wrap("· " + str(note), 16, W - 64, 2):
                ui.text(40, y, line, 16, C["dim"])
                y += 24
        if u.state == "downloading":
            ui.text(24, H - 110, _("Downloading {n}%", n=int(u.progress * 100)), 17, C["accent"])
            ui.fill(24, H - 82, W - 48, 10, C["key"] if "key" in C else (60, 60, 60))
            ui.fill(24, H - 82, int((W - 48) * u.progress), 10, C["accent"])
            draw_footer(ui, [("B", _("Back (keeps downloading)"))])
        elif u.state == "error":
            for i, line in enumerate(ui.wrap(tr(u.error) or _("Something went wrong."), 16, W - 48, 3)):
                ui.text(24, H - 120 + i * 24, line, 16, C["warn"])
            draw_footer(ui, [("A", _("Try again")), ("B", _("Back"))])
        else:
            ui.text(24, H - 100, _("Your games and settings are kept. Needs Wi-Fi."), 15, C["dim"], max_w=W - 48)
            draw_footer(ui, [("A", _("Update")), ("B", _("Later"))])


class MessageScreen(Screen):
    def __init__(self, app, title, body):
        super().__init__(app)
        self.title, self.body = title, body

    def handle(self, b):
        if b in ("A", "B", "START", "SELECT"):
            self.app.pop()

    def layout(self):
        """The box grows with the text (translations are longer); the font gets smaller only if it wouldn't fit."""
        key = i18n.current()
        if getattr(self, "_layout", (None,))[0] != key:
            for size in (16, 15, 14, 13):
                lines = [ln for para in self.body.split("\n") for ln in self.ui.wrap(para, size, W - 150, 20)]
                h = max(280, 110 + len(lines) * (size + 7))
                if h <= H - 24:
                    break
            h = min(h, H - 24)
            self._layout = (key, size, lines, h, (H - h) // 2)
        return self._layout[1:]

    def draw(self):
        ui = self.ui
        self.app.draw_below(self)
        ui.fill(0, 0, W, H, C["black"], 170)
        size, lines, h, top = self.layout()
        ui.rounded(50, top, W - 100, h, 14, C["bar"])
        ui.fill(50, top, W - 100, 5, C["accent"])
        ui.text(W // 2, top + 22, self.title, 22, C["text"], align="center", max_w=W - 140)
        y = top + 66
        for line in lines:
            if y > top + h - 50:
                break
            ui.text(75, y, line, size, C["dim"])
            y += size + 7
        ui.text(W // 2, top + h - 32, _("Press A to continue"), 15, C["faint"], align="center")


class ConfirmScreen(MessageScreen):
    def __init__(self, app, title, body, on_yes):
        super().__init__(app, title, body)
        self.on_yes = on_yes

    def handle(self, b):
        if b == "A":
            self.app.pop()
            self.on_yes()
        elif b in ("B", "SELECT"):
            self.app.pop()

    def draw(self):
        super().draw()
        _size, _lines, h, top = self.layout()
        self.ui.fill(60, top + h - 44, W - 120, 36, C["bar"])
        self.ui.text(W // 2, top + h - 34, _("A = Yes · B = No"), 15, C["text"], align="center")


# --------------------------------------------------------------------- app
def _forget_licence(app_dir):
    """Versions before 1.3.0 had an activation: its saved licence (with a handheld ID) isn't needed any more."""
    try:
        os.remove(os.path.join(app_dir, "data", "license.json"))
    except OSError:
        pass


class App:
    def __init__(self, app_dir):
        self.app_dir = app_dir
        self.cfg = load_config(app_dir)
        self.hide_pc_only = yes(self.cfg.get("hide_pc_only", "yes"))
        i18n.setup(app_dir)  # the app's choice, else muOS's language (see i18n.py)
        self.ui = UI(app_dir)
        self.input = Input(swap_ab=yes(self.cfg.get("swap_ab", "no")))
        test_root = os.environ.get("GM_TEST_ROOT")
        if test_root:  # run on a PC against a fake SD card
            self.device = Device(os.path.join(test_root, "share"), os.path.join(test_root, "ROMS"),
                                 os.path.join(test_root, "catalogue"), os.path.join(test_root, "bios"))
        else:
            self.device = Device(roms_path=self.cfg.get("roms_path") or "/mnt/mmc/ROMS")
        self.hbh = HomebrewHub(include_demos=yes(self.cfg.get("include_demos", "no")))
        self.itch = Itch(self.cfg.get("api_key", ""))
        cache = os.path.join(app_dir, "data", "cache")
        if test_root:
            self.pm = PortMaster(cache, os.path.join(test_root, "ports"), os.path.join(test_root, "ROMS", "Ports"),
                                 os.path.join(test_root, "PortMaster"))
        else:
            roms = self.device.roms_path
            self.pm = PortMaster(cache, os.path.join(os.path.dirname(roms), "ports"), os.path.join(roms, "Ports"))
        self.sources = {"hbh": self.hbh, "itch": self.itch, "pm": self.pm}
        self.library = installer.Library(os.path.join(app_dir, "data", "installed.json"))
        self.playtime = mygames.Playtime(self.device.info_dir)
        self.favs = mygames.Favourites(os.path.join(app_dir, "data", "favourites.json"))
        self.boxart = boxart.Library()
        self.images = Images()
        self.inspector = Inspector(self)
        self.last_search = ""
        _forget_licence(app_dir)
        self.stack = [HomeScreen(self)]
        self.running = True
        self.restart = False  # "Restart now" after an update: main.py returns 42, mux_launch.sh starts again
        # Updates from GitHub releases (checked at start and daily when online; nothing installs by itself)
        self.updater = updater.Updater(app_dir, "game-manager", APP_VERSION)
        self._confirmed = False
        if self.updater.just_updated:
            self.push(MessageScreen(self, _("Updated"), _("Game Manager is now version {v}. Your games and settings "
                                                          "were kept.", v=APP_VERSION)))
        elif self.updater.rolled_back:
            self.push(MessageScreen(self, _("Update undone"), _("Version {bad} didn't start, so Game Manager went back "
                                                                "to {v}. Please email feedback@pocketkode.com.",
                                                                bad=self.updater.rolled_back.get('version'), v=APP_VERSION)))

    def change_language(self):
        i18n.cycle()
        self.ui.language_changed()

    def rom_roots(self):
        roots = [self.device.roms_path]
        if not os.environ.get("GM_TEST_ROOT"):
            roots += [r for r in ("/mnt/mmc/ROMS", "/mnt/sdcard/ROMS") if r not in roots]
        return [r for r in roots if os.path.isdir(r)]

    def source_for(self, game):
        return self.sources[game["src"]]

    def push(self, s):
        self.stack.append(s)

    def pop(self):
        if len(self.stack) > 1:
            self.stack.pop()

    def draw_below(self, screen):
        i = self.stack.index(screen)
        if i > 0:
            self.stack[i - 1].draw()

    # actions
    def search(self, text):
        if not text:
            return
        self.last_search = text
        hbh, itch = self.hbh, self.itch

        extra = [self.pm] + ([itch] if itch.has_key else [])

        def loader(page, sort, genre):
            games, more, total = hbh.search(text, page)
            for src in extra:
                try:
                    g2, more2, _total = src.search(text, page)
                    games, more, total = games + g2, more or more2, None
                except net.NetError:
                    pass
            return games, more, total

        self.push(GameListScreen(self, _("Search: {text}", text=text), loader, sorts=SORT_OPTIONS["search"],
                                 server_sorts={"relevance"}))

    def set_api_key(self, key):
        key = key.strip()
        try:
            save_api_key(self.app_dir, key)
        except OSError as e:
            self.push(MessageScreen(self, _("Couldn't save"), str(e)))
            return
        self.itch.api_key = key
        # Re-check itch.io games with the key, so downloads become available
        self.inspector.queued.clear()
        self.push(MessageScreen(self, _("Saved"), _("The API key is saved in config.ini.") if key else
                                _("The API key was removed.")))

    def confirm_uninstall(self, rec):
        where = os.path.relpath(rec["path"], os.path.dirname(self.device.roms_path))
        self.push(ConfirmScreen(self, _("Uninstall game?"),
                                _("Remove “{name}” ({where}) and its box art from the SD card?\nYour save files are "
                                  "kept, so you can reinstall later and carry on.", name=rec['title'], where=where),
                                lambda: self._uninstall(rec)))

    def _uninstall(self, rec):
        self.library.remove(rec["key"])
        self.push(MessageScreen(self, _("Uninstalled"), _("“{name}” was removed.", name=rec['title'])))

    def _launch(self, system, path, preferred_so=None):
        """Hand the screen to the emulator and wait. Returns (secs, rc, core) or None on error."""
        if not self.device.is_muos:
            self.push(MessageScreen(self, _("Test mode"), _("Starting games only works on the handheld.")))
            return None
        self.ui.close()
        sdl2.SDL_QuitSubSystem(sdl2.SDL_INIT_VIDEO)
        try:
            result = launcher.run(self.device, system, path,
                                  os.path.join(self.app_dir, "logs", "tryit.log"), preferred_so)
            err = None
        except Exception as e:  # noqa: BLE001
            result, err = None, str(e)
        sdl2.SDL_InitSubSystem(sdl2.SDL_INIT_VIDEO)
        self.ui.open()
        self.input.clear()
        self.input.poll()  # drop button presses made inside the game
        self.input.clear()
        if err:
            self.push(MessageScreen(self, _("Couldn't start the game"), tr(err)))
        return result

    def try_game(self, rec):
        """Start a game installed by this app, then ask whether it worked."""
        result = self._launch(BY_ID[rec["system"]].muos, rec["path"], rec.get("core"))
        if result:
            self.push(TryResultScreen(self, rec, *result))

    def start_game(self, system, path, name):
        """Start any game from the SD card, with the core chosen for it in muOS if there is one."""
        result = self._launch(system, path, mygames.muos_core(self.device, self.rom_roots(), path))
        if result and result[0] < 4:
            self.push(MessageScreen(self, _("The game closed straight away"),
                                    _("“{name}” closed after {s} s ({core}). See logs/tryit.log in the app folder for "
                                      "details.", name=name, s=f"{result[0]:.0f}", core=result[2])))

    def run(self):
        while self.running and not self.input.quit:
            buttons = self.input.poll()
            top = self.stack[-1]
            for b in buttons:
                top.handle(b)
                top = self.stack[-1]
            self.updater.maybe_check()
            self.ui.clear()
            self.stack[-1].draw()
            self.ui.present()
            if not self._confirmed:  # the first screen is up: an update that was just installed works
                self._confirmed = True
                updater.confirm(self.app_dir)
            time.sleep(1 / 30)
        self.ui.close()
