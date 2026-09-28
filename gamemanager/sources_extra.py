# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PocketKode
"""PortMaster ports as a game catalogue.

Games use the same dict shape as sources.py. Port files carry "install": "port", so the
installer hands them to PortMaster's own harbourmaster instead of treating them as ROMs.
"""
import json
import os
import subprocess
import time
import urllib.parse

from . import net

GITHUB_RAW = "https://raw.githubusercontent.com"


def _cached_json(path, url, max_age, timeout=60, limit=32 * 1024 * 1024):
    """JSON from `url`, cached in `path` for `max_age` seconds (stale copy used if offline)."""
    try:
        if time.time() - os.path.getmtime(path) < max_age:
            with open(path) as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    try:
        data = net.get_bytes(url, timeout=timeout, limit=limit)
        parsed = json.loads(data.decode("utf-8"))
    except (net.NetError, ValueError):
        try:
            with open(path) as f:
                return json.load(f)
        except (OSError, ValueError):
            raise net.NetError("Couldn't load the list. Check Wi-Fi and try again.")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return parsed


def _page(items, page, per=30):
    start = (page - 1) * per
    return items[start:start + per], start + per < len(items), len(items)


# ---------------------------------------------------------------- PortMaster
class PortMaster:
    NAME = "PortMaster"
    KEY = "pm"
    URL = "https://github.com/PortsMaster/PortMaster-New/releases/latest/download/ports.json"
    IMG = f"{GITHUB_RAW}/PortsMaster/PortMaster-New/main/ports"
    SERVER_SORTS = {"new", "updated", "az"}
    SORTS = [("new", "Newest first"), ("updated", "Recently updated"), ("az", "A to Z")]
    GENRES = [("action", "Action"), ("adventure", "Adventure"), ("arcade", "Arcade"), ("platformer", "Platformer"),
              ("puzzle", "Puzzle"), ("rpg", "RPG"), ("fps", "Shooter (FPS)"), ("racing", "Racing"),
              ("sports", "Sports"), ("strategy", "Strategy"), ("simulation", "Simulation")]
    PM_DIRS = ("/mnt/mmc/MUOS/PortMaster", "/mnt/sdcard/MUOS/PortMaster", "/opt/muos/PortMaster")

    def __init__(self, cache_dir, ports_dir="/mnt/mmc/ports", scripts_dir="/mnt/mmc/ROMS/Ports", pm_dir=None):
        self.cache = os.path.join(cache_dir, "portmaster_ports.json")
        self.info_cache = os.path.join(cache_dir, "portmaster_device.json")
        self.ports_dir = ports_dir
        self.scripts_dir = scripts_dir
        self.pm_dir = pm_dir or next((d for d in self.PM_DIRS if os.path.isfile(os.path.join(d, "harbourmaster"))), None)
        self._ports = None
        self._device = None

    # --- what this device can run
    def device(self):
        """Capabilities as PortMaster sees them (asks harbourmaster once, then caches)."""
        if self._device is not None:
            return self._device
        info = None
        try:
            with open(self.info_cache) as f:
                info = json.load(f)
        except (OSError, ValueError):
            pass
        if info is None and self.pm_dir:
            try:
                subprocess.run(["python3", "harbourmaster", "--quiet", "--no-check", "device_info", self.info_cache],
                               cwd=self.pm_dir, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                with open(self.info_cache) as f:
                    info = json.load(f)
            except (OSError, ValueError, subprocess.SubprocessError):
                info = None
        if not isinstance(info, dict):  # sensible defaults for this family of handhelds
            info = {"capabilities": ["power", "aarch64", "armhf", "muOS", "analog_0", "analog_1", "analog_2", "1gb"],
                    "glibc": "2.38"}
        self._device = info
        return info

    def runs_here(self, port):
        a = port.get("attr") or {}
        if not a.get("rtr"):
            return False
        caps = set(self.device().get("capabilities") or [])
        device_archs = ({"aarch64", "armhf", "x86_64"} & caps) or {"aarch64"}
        if not set(a.get("arch") or ["aarch64"]) & device_archs:
            return False
        for req in a.get("reqs") or []:
            req = (req or "").strip()
            if not req:
                continue
            ok = False
            for alt in req.split("|"):
                alt = alt.strip()
                ok = ok or ((alt[1:] not in caps) if alt.startswith("!") else (alt in caps))
            if not ok:
                return False
        need = a.get("min_glibc") or ""
        have = str(self.device().get("glibc") or "")
        if need and have:
            try:
                if tuple(map(int, need.split("."))) > tuple(map(int, have.split("."))):
                    return False
            except ValueError:
                pass
        return True

    def ports(self):
        if self._ports is None:
            data = _cached_json(self.cache, self.URL, 12 * 3600)
            self._ports = [p for p in (data.get("ports") or {}).values() if isinstance(p, dict) and self.runs_here(p)]
        return self._ports

    # --- lists
    def systems(self):
        return ["ports"]

    def browse(self, sid, page, sort="new", genre=None):
        ports = self.ports()
        if genre:
            ports = [p for p in ports if genre in ((p.get("attr") or {}).get("genres") or [])]
        if sort == "az":
            ports = sorted(ports, key=lambda p: ((p.get("attr") or {}).get("title") or p["name"]).lower())
        else:
            field = "date_updated" if sort == "updated" else "date_added"
            ports = sorted(ports, key=lambda p: (p.get("source") or {}).get(field) or "", reverse=True)
        items, more, total = _page(ports, page)
        return [self._game(p) for p in items], more, total

    def search(self, text, page):
        t = text.lower()
        hits = [p for p in self.ports() if t in ((p.get("attr") or {}).get("title") or p["name"]).lower()]
        items, more, total = _page(hits, page)
        return [self._game(p) for p in items], more, total

    def _game(self, p):
        a = p.get("attr") or {}
        src = p.get("source") or {}
        folder = p["name"][:-4] if p["name"].endswith(".zip") else p["name"]
        img = a.get("image") or {}
        pics = [x for x in ([img.get("screenshot")] + list(img.get("covers") or [])) if isinstance(x, str) and x]
        pics = [f"{self.IMG}/{urllib.parse.quote(folder)}/{urllib.parse.quote(x)}" for x in pics]
        desc = (a.get("desc") or "").strip()
        inst = (a.get("inst") or "").strip()
        if inst and inst.lower() != "ready to run.":
            desc += "\n\n" + inst
        runtime = a.get("runtime") or []
        if isinstance(runtime, str):
            runtime = [runtime]
        if runtime:
            desc += "\n\nNeeds PortMaster runtime: " + ", ".join(r.replace(".squashfs", "") for r in runtime)
        return {"key": f"pm:{p['name']}", "src": "pm", "title": a.get("title") or folder,
                "author": "Ported by " + ", ".join(a.get("porter") or []) if a.get("porter") else "",
                "system": "ports", "cover": pics[-1] if len(pics) > 1 else (pics[0] if pics else None),
                "screenshots": pics, "summary": desc.split("\n")[0][:200], "description": desc,
                "license": "", "tags": a.get("genres") or [], "page": "https://portmaster.games/detail.html?name=" + folder,
                "rating_page": None, "rating": None, "check": None, "items": p.get("items") or [],
                "files": [{"name": p["name"], "filename": p["name"], "size": src.get("size"), "system": "ports",
                           "kind": "rom", "install": "port", "url": src.get("url"), "md5": src.get("md5")}]}

    def is_installed(self, game):
        items = game.get("items") or []
        return bool(items) and all(
            os.path.exists(os.path.join(self.ports_dir if i.endswith("/") else self.scripts_dir, i.rstrip("/")))
            for i in items)
