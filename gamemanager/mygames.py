# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PocketKode
"""Extra information about the games on the SD card: play time (from muOS's tracker),
favourites, the core chosen in muOS, and box art downloads."""
import json
import os
import time

from . import net

UNION_ROMS = "/mnt/union/ROMS"


def union_path(path, roots):
    """muOS keys its play-time data by /mnt/union/ROMS/<relative path>."""
    for r in roots:
        rel = os.path.relpath(path, r)
        if not rel.startswith(".."):
            return f"{UNION_ROMS}/{rel}"
    return path


def fmt_duration(secs):
    secs = int(secs or 0)
    if secs < 60:
        return "under 1 min"
    h, m = divmod(secs // 60, 60)
    return f"{h} h {m} min" if h else f"{m} min"


def fmt_ago(ts):
    if not ts:
        return ""
    days = int((time.time() - ts) // 86400)
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days} days ago"
    from . import i18n
    return i18n.date(ts)


class Playtime:
    """Reads muOS's MUOS/info/track/playtime_data.json (read-only)."""

    def __init__(self, info_dir):
        self.path = os.path.join(info_dir, "track", "playtime_data.json") if info_dir else None
        self.data = {}
        self.reload()

    def reload(self):
        try:
            with open(self.path) as f:
                d = json.load(f)
            self.data = d if isinstance(d, dict) else {}
        except (OSError, ValueError, TypeError):
            self.data = {}

    def get(self, union):
        """{'total': secs, 'launches': n, 'last': unix time} or None."""
        e = self.data.get(union)
        if not isinstance(e, dict):
            return None
        return {"total": int(e.get("total_time") or 0), "launches": int(e.get("launches") or 0),
                "last": int(e.get("start_time") or 0)}


class Favourites:
    """Paths of favourite games (data/favourites.json)."""

    def __init__(self, path):
        self.path = path
        try:
            with open(path) as f:
                self.items = [p for p in json.load(f) if isinstance(p, str)]
        except (OSError, ValueError, TypeError):
            self.items = []

    def __contains__(self, p):
        return p in self.items

    def toggle(self, p):
        if p in self.items:
            self.items.remove(p)
        else:
            self.items.append(p)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w") as f:
            json.dump(self.items, f, ensure_ascii=False, indent=1)
        return p in self.items

    def forget(self, p):
        if p in self.items:
            self.toggle(p)


def muos_core(device, roots, path):
    """The core file muOS would use for this game: per-game choice, then per-folder choice.
    muOS stores these in /opt/muos/share/info/content/<system folder>/<game>.cfg and core.cfg."""
    for r in roots:
        rel = os.path.relpath(path, r)
        if rel.startswith(".."):
            continue
        top = rel.split(os.sep)[0]
        cdir = os.path.join(device.share_dir, "info", "content", top)
        stem = os.path.splitext(os.path.basename(path))[0]
        for fn, line in ((stem + ".cfg", 1), ("core.cfg", 0)):
            try:
                with open(os.path.join(cdir, fn), encoding="utf-8", errors="replace") as f:
                    lines = f.read().splitlines()
                if len(lines) > line and lines[line].strip():
                    return lines[line].strip()
            except OSError:
                pass
    return None


def art_stems(game):
    """Names muOS may use for a game's art: the folder name and the main file's name."""
    stems = [game["name"]]
    main = os.path.splitext(os.path.basename(game["path"]))[0]
    if main not in stems:
        stems.append(main)
    return stems


def box_path(device, game):
    """Existing box art for a scanned game, or None."""
    if not device.catalogue_dir:
        return None
    for stem in art_stems(game):
        p = os.path.join(device.catalogue_dir, game["system"], "box", stem + ".png")
        if os.path.isfile(p):
            return p
    return None


def fetch_box_art(device, library, games, task, save_png):
    """Download missing box art for `games`. Returns (added, not_found, already_had)."""
    added, missing, had = [], [], 0
    todo = [g for g in games if not box_path(device, g)]
    had = len(games) - len(todo)
    for i, g in enumerate(todo):
        task.check()
        task.status = f"{g['name']}"
        task.progress = i / max(1, len(todo))
        name = library.match(g["system"], g["name"])
        if not name:
            missing.append(g["name"])
            continue
        try:
            data = net.get_bytes(library.url(g["system"], name), timeout=30, limit=8 * 1024 * 1024)
        except net.NetError:
            missing.append(g["name"])
            continue
        saved = False
        for stem in art_stems(g):
            p = os.path.join(device.catalogue_dir, g["system"], "box", stem + ".png")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            saved = save_png(data, p) or saved
        (added if saved else missing).append(g["name"])
    task.progress = 1.0
    return added, missing, had
