# © 2026 PocketKode · SPDX-License-Identifier: GPL-3.0-only
"""Saved copies of game lists, shown when a site can't be reached.

Every page of a list that loads is saved in data/cache/lists (one small JSON file per page). When the same
page can't be loaded later (no Wi-Fi, the site is down), the saved copy is shown instead, marked with the
time it was saved. Only the newest MAX_FILES pages are kept.
"""
import hashlib
import json
import os
import time

from . import net

MAX_FILES = 300


class ListCache:
    def __init__(self, folder):
        self.folder = folder

    def _path(self, key):
        return os.path.join(self.folder, hashlib.sha1(key.encode("utf-8")).hexdigest()[:20] + ".json")

    def save(self, key, games, more, total):
        path = self._path(key)
        try:
            os.makedirs(self.folder, exist_ok=True)
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump({"key": key, "saved": time.time(), "games": games, "more": more, "total": total}, f)
            os.replace(path + ".tmp", path)
            self._prune()
        except (OSError, TypeError, ValueError):  # a full or read-only SD card only means there is no saved copy
            try:
                os.remove(path + ".tmp")
            except OSError:
                pass

    def load(self, key):
        """(games, more, total, saved_time) or None."""
        try:
            with open(self._path(key), encoding="utf-8") as f:
                d = json.load(f)
            if d.get("key") == key and isinstance(d.get("games"), list):
                return d["games"], bool(d.get("more")), d.get("total"), float(d.get("saved") or 0)
        except (OSError, ValueError, TypeError):
            pass
        return None

    def _prune(self):
        try:
            files = [os.path.join(self.folder, n) for n in os.listdir(self.folder) if n.endswith(".json")]
        except OSError:
            return
        if len(files) <= MAX_FILES:
            return
        files.sort(key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0)
        for p in files[:len(files) - MAX_FILES]:
            try:
                os.remove(p)
            except OSError:
                pass

    def wrap(self, name, loader):
        """loader(page, sort, genre) → (games, more, total), saved after each load. When it fails with a
        network error and a saved copy exists, that copy is returned instead, with a 4th item:
        {"saved": the time it was saved}."""
        def load(page, sort, genre):
            key = f"{name}|{page}|{sort}|{genre or ''}"
            try:
                games, more, total = loader(page, sort, genre)[:3]
            except net.NetError:
                saved = self.load(key)
                if saved is None:
                    raise
                games, more, total, when = saved
                return games, more, total, {"saved": when}
            self.save(key, games, more, total)
            return games, more, total
        return load
