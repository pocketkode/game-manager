# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PocketKode
"""Box art for any game on the SD card, from the libretro thumbnail library
(thumbnails.libretro.com, the same images RetroArch uses).

Names there follow No-Intro/Redump ("F-Zero (USA)"). Older names like "Sky Blazer (E)"
or "Harvest Moon (E) [!]" are matched by title plus region.
"""
import html
import re
import urllib.parse

from . import net

BASE = "https://thumbnails.libretro.com"

# muOS system name -> libretro thumbnail folder
LIBRETRO = {
    "Nintendo Game Boy": "Nintendo - Game Boy",
    "Nintendo Game Boy Color": "Nintendo - Game Boy Color",
    "Nintendo Game Boy Advance": "Nintendo - Game Boy Advance",
    "Nintendo NES - Famicom": "Nintendo - Nintendo Entertainment System",
    "Nintendo SNES - SFC": "Nintendo - Super Nintendo Entertainment System",
    "Nintendo N64": "Nintendo - Nintendo 64",
    "Sega Mega Drive - Genesis": "Sega - Mega Drive - Genesis",
    "Sega Master System": "Sega - Master System - Mark III",
    "Sega Game Gear": "Sega - Game Gear",
    "Sega 32X": "Sega - 32X",
    "Sega Mega CD - Sega CD": "Sega - Mega-CD - Sega CD",
    "Sega Dreamcast": "Sega - Dreamcast",
    "Sony PlayStation": "Sony - PlayStation",
    "Sony PlayStation Portable": "Sony - PlayStation Portable",
    "NEC PC Engine": "NEC - PC Engine - TurboGrafx 16",
    "NEC PC Engine CD": "NEC - PC Engine CD - TurboGrafx-CD",
    "NEC PC Engine SuperGrafx": "NEC - PC Engine SuperGrafx",
    "SNK Neo Geo Pocket - Color": "SNK - Neo Geo Pocket Color",
    "SNK Neo Geo CD": "SNK - Neo Geo CD",
    "Atari 2600": "Atari - 2600",
    "Atari Lynx": "Atari - Lynx",
}

# Old GoodTools region codes -> No-Intro words
REGIONS = {"u": "usa", "us": "usa", "e": "europe", "eu": "europe", "j": "japan", "jp": "japan",
           "ue": "usa europe", "ju": "japan usa", "jue": "japan usa europe", "w": "world",
           "f": "france", "g": "germany", "s": "spain", "i": "italy", "k": "korea", "b": "brazil",
           "a": "australia", "c": "china", "uk": "united kingdom"}
REGION_PREF = ["usa", "world", "europe", "japan"]


def _norm(title):
    t = title.lower().replace("&", " and ")
    t = re.sub(r"^the\s+|,\s*the(?=\s|$)", " ", t)
    return re.sub(r"[^a-z0-9]+", "", t)


def short_key(name):
    """Title key without the subtitle: "Street Fighter II - The World Warrior" -> "streetfighterii"."""
    return _norm(re.sub(r"\[[^\]]*\]", "", name).split("(")[0].split(" - ")[0])


def split_name(name):
    """('title key', {region words}) from a ROM or thumbnail name."""
    base = re.sub(r"\[[^\]]*\]", "", name)
    title = base.split("(")[0]
    regions = set()
    for tag in re.findall(r"\(([^)]*)\)", base):
        for part in re.split(r"[,\s]+", tag.lower()):
            part = part.strip()
            if part in REGIONS:
                regions.update(REGIONS[part].split())
            elif part in ("usa", "europe", "japan", "world", "france", "germany", "spain", "italy",
                          "korea", "brazil", "australia", "china"):
                regions.add(part)
    return _norm(title), regions


def _is_variant(name):
    low = name.lower()
    return any(w in low for w in ("(beta", "(proto", "(sample", "(demo", "(kiosk", "(pirate", "(hack"))


class Library:
    """Thumbnail listing for one system, fetched once and cached in memory."""

    def __init__(self):
        self.lists = {}

    def names(self, muos_system):
        folder = LIBRETRO.get(muos_system)
        if not folder:
            return None
        if folder not in self.lists:
            url = f"{BASE}/{urllib.parse.quote(folder)}/Named_Boxarts/"
            page = net.get_bytes(url, timeout=40, limit=16 * 1024 * 1024).decode("utf-8", "replace")
            names = [html.unescape(urllib.parse.unquote(h))[:-4]
                     for h in re.findall(r'href="([^"?/][^"]*\.png)"', page)]
            index, short = {}, {}
            for n in names:
                index.setdefault(split_name(n)[0], []).append(n)
                short.setdefault(short_key(n), []).append(n)
            self.lists[folder] = (set(names), index, short)
        return self.lists[folder]

    def match(self, muos_system, rom_name):
        """Best thumbnail name for a ROM name, or None."""
        data = self.names(muos_system)
        if not data:
            return None
        exact, index, short = data
        clean = re.sub(r'[&*/:`<>?\\|"]', "_", rom_name)
        if clean in exact:
            return clean
        key, regions = split_name(rom_name)
        skey = short_key(rom_name)
        # Full title first, then without the subtitle on either side
        candidates = (index.get(key) or index.get(skey) or short.get(key) or short.get(skey) or [])
        if not candidates:
            return None

        def score(n):
            _, r = split_name(n)
            s = 0
            if regions and regions & r:
                s += 10
            for i, pref in enumerate(REGION_PREF):
                if pref in r:
                    s += 4 - i
                    break
            if _is_variant(n):
                s -= 20
            if "(rev" in n.lower() or "(virtual console" in n.lower():
                s -= 1
            return s

        return max(candidates, key=score)

    def url(self, muos_system, thumb_name):
        folder = LIBRETRO[muos_system]
        return f"{BASE}/{urllib.parse.quote(folder)}/Named_Boxarts/{urllib.parse.quote(thumb_name)}.png"
