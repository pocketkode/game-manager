# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PocketKode
"""Finds every game in the ROMS folders, grouped by muOS system, and can remove one safely.

A game is either
  - a file in a system folder (plus any files it references: .cue → .bin tracks,
    .gdi → tracks, .m3u → discs, .ccd → .img/.sub), or
  - a sub-folder of a system folder (how muOS users usually keep disc games),
    started from its best "main" file.
"""
import os
import re
import shutil

# muOS systems that aren't games you'd start/uninstall from here
SKIP_SYSTEMS = {"External - Ports", "Media Player", "Book Reader", "Application", "Archive",
                "Task", "Folder", "Collection", "Root"}

NOT_GAMES = {".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".xml",
             ".json", ".ini", ".cfg", ".srm", ".sav", ".state", ".auto", ".cht", ".nfo", ".log",
             ".bak", ".part", ".ips", ".bps", ".ups", ".db", ".sh", ".html", ".url", ".ds_store"}

# Which file inside a game folder to start, best first
MAIN_ORDER = [".m3u", ".cue", ".gdi", ".cdi", ".chd", ".pbp", ".iso", ".cso", ".ccd", ".zip"]


def _hidden(name):
    return name.startswith(".") or name.startswith("._")


def _is_game_file(name):
    low = name.lower()
    if low.endswith(".p8.png"):
        return not _hidden(name)  # PICO-8 carts are PNG files
    return not _hidden(name) and os.path.splitext(low)[1] not in NOT_GAMES


def referenced_files(path):
    """Other files that belong to `path` (same folder), e.g. the .bin tracks of a .cue."""
    folder, name = os.path.split(path)
    ext = os.path.splitext(name.lower())[1]
    refs = []
    try:
        if ext in (".cue", ".gdi", ".m3u"):
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read(256 * 1024)
            if ext == ".cue":
                refs = re.findall(r'^\s*FILE\s+"?(.+?)"?\s+\w+\s*$', text, re.M | re.I)
            elif ext == ".gdi":
                for line in text.splitlines()[1:]:
                    m = re.match(r'\s*\d+\s+\d+\s+\d+\s+\d+\s+("([^"]+)"|(\S+))', line)
                    if m:
                        refs.append(m.group(2) or m.group(3))
            else:
                refs = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
        elif ext == ".ccd":
            stem = os.path.splitext(name)[0]
            refs = [stem + e for e in (".img", ".sub")]
    except OSError:
        return []
    out = []
    for r in refs:
        p = os.path.normpath(os.path.join(folder, r))
        if os.path.dirname(p) == os.path.normpath(folder) and os.path.isfile(p) and p != path:
            out.append(p)
            out += [q for q in referenced_files(p) if q not in out]  # m3u → cue → bin
    return out


def _size(paths):
    total = 0
    for p in paths:
        try:
            total += os.path.getsize(p)
        except OSError:
            pass
    return total


def _folder_game(system, folder):
    files = sorted(os.path.join(folder, n) for n in os.listdir(folder)
                   if os.path.isfile(os.path.join(folder, n)) and _is_game_file(n))
    if not files:
        return None
    main = None
    for ext in MAIN_ORDER:
        main = next((p for p in files if p.lower().endswith(ext)), None)
        if main:
            break
    main = main or max(files, key=lambda p: os.path.getsize(p))
    all_files = [os.path.join(folder, n) for n in os.listdir(folder)]
    return {"name": os.path.basename(folder), "system": system, "path": main, "folder": folder,
            "files": [p for p in all_files if os.path.isfile(p)],
            "size": _size(p for p in all_files if os.path.isfile(p))}


def scan(device, roots):
    """{muOS system name: [game, ...]} for every mapped system folder under the given ROMS roots."""
    out = {}
    for root in roots:
        if not os.path.isdir(root):
            continue
        for d in sorted(os.listdir(root)):
            sys_dir = os.path.join(root, d)
            system = device.assign.get(d.lower())
            if not os.path.isdir(sys_dir) or _hidden(d) or system in SKIP_SYSTEMS or not system:
                continue
            games, taken = [], set()
            entries = sorted(os.listdir(sys_dir), key=str.lower)
            files = [os.path.join(sys_dir, n) for n in entries
                     if os.path.isfile(os.path.join(sys_dir, n)) and _is_game_file(n)]
            # Files referenced by a .cue/.gdi/.m3u are part of that game, not games of their own
            for p in files:
                if p.lower().endswith((".m3u", ".cue", ".gdi", ".ccd")):
                    taken.update(referenced_files(p))
            for p in files:
                if p in taken:
                    continue
                parts = [p] + referenced_files(p)
                base = os.path.basename(p)
                name = base[:-7] if base.lower().endswith(".p8.png") else os.path.splitext(base)[0]
                games.append({"name": name, "system": system,
                              "path": p, "folder": None, "files": parts, "size": _size(parts)})
            for n in entries:
                sub = os.path.join(sys_dir, n)
                if os.path.isdir(sub) and not _hidden(n):
                    g = _folder_game(system, sub)
                    if g:
                        games.append(g)
            if games:
                games.sort(key=lambda g: g["name"].lower())
                out.setdefault(system, []).extend(games)
    return out


def remove(game, roots, device):
    """Delete a game's files (or its folder), its AppleDouble '._' leftovers and its muOS art.
    Save files are kept. Refuses to touch anything outside a system folder."""
    real_roots = [os.path.realpath(r) for r in roots]

    def inside_system_folder(p):
        rp = os.path.realpath(p)
        for r in real_roots:
            rel = os.path.relpath(rp, r)
            if not rel.startswith("..") and len(rel.split(os.sep)) >= 2:
                return True
        return False

    targets = [game["folder"]] if game.get("folder") else list(game["files"])
    for t in targets:
        if not inside_system_folder(t):
            raise RuntimeError(f"Refusing to delete outside the ROMS folders: {t}")
    for t in targets:
        if os.path.isdir(t):
            shutil.rmtree(t)
        elif os.path.exists(t):
            os.remove(t)
        apple = os.path.join(os.path.dirname(t), "._" + os.path.basename(t))
        if os.path.isfile(apple):
            os.remove(apple)
    # Box art, screenshots and description that muOS or a scraper made for this game
    if device.catalogue_dir:
        stems = {game["name"], os.path.splitext(os.path.basename(game["path"]))[0]}
        base = os.path.join(device.catalogue_dir, game["system"])
        for stem in stems:
            for sub, ext in (("box", ".png"), ("preview", ".png"), ("splash", ".png"), ("grid", ".png"),
                             ("text", ".txt")):
                p = os.path.join(base, sub, stem + ext)
                if os.path.isfile(p):
                    os.remove(p)
