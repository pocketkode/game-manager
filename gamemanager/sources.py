# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PocketKode
"""Game catalogues: Homebrew Hub (hh3.gbdev.io) and itch.io.

Every game is a dict:
  key, src, title, author, system (system id or None), cover (url), summary, description,
  license, tags, page (url), files (list, None until known), check (level, label) or None

Every file is a dict:
  name, filename, size, system, kind ("rom" | "zip" | "pc" | "other"), url or upload_id
"""
import html
import json
import os
import re
import urllib.parse
import xml.etree.ElementTree as ET

from . import net
from .systems import BY_ID, ROM_EXTS, system_for_file

PC_EXTS = (".exe", ".apk", ".dmg", ".app", ".msi", ".appimage", ".deb", ".x86_64", ".tar.gz", ".pck")
PC_WORDS = ("windows", "win64", "win32", "linux", "macos", "mac os", "osx", "android", "html5",
            "webgl", " pc ", "(pc)", "pc version", "steam deck")
ROM_WORDS = ("rom", "cart", "game boy", "gameboy", "gba", "nes", "snes", "famicom", "genesis",
             "mega drive", "megadrive", "master system", "game gear", "everdrive", "flashcart")
DOC_RE = re.compile(r"readme|changelog|change log|licen[cs]e|manual|notes|instructions|credits", re.I)


# (id, label). Homebrew Hub tags are case-sensitive, so both spellings are queried.
GENRES = [("action", "Action"), ("adventure", "Adventure"), ("platformer", "Platformer"),
          ("puzzle", "Puzzle"), ("rpg", "RPG"), ("shooter", "Shooter"), ("racing", "Racing"),
          ("sports", "Sports"), ("strategy", "Strategy")]
SORTS = [("new", "Newest first"), ("top", "Top rated first"), ("az", "A to Z")]


def file_kind(filename, pc=False, name=""):
    """rom: a ROM file · zip/maybe: may contain one · pc: PC/phone build · other: anything else.
    The upload's platform flags (pc) are only a hint: some developers tick every platform
    even for a ROM, so the file name wins when it clearly says what the file is."""
    low = filename.lower()
    text = f" {name.lower()} {low} "
    ext = os.path.splitext(low)[1]
    if low.endswith(PC_EXTS):
        return "pc"
    if ext in (".md", ".txt") and (ext == ".txt" or DOC_RE.search(low)):
        return "other"
    if ext in ROM_EXTS:
        return "rom"
    if any(w in text for w in PC_WORDS):
        return "pc"
    if any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text) for w in ROM_WORDS):
        return "zip" if ext == ".zip" else "maybe"
    if pc:
        return "pc"
    return "zip" if ext == ".zip" else "other"


def verdict(files, device):
    """(level, label) badge for a list of files."""
    if not files:
        return "bad", "NO FILE"
    playable = [f for f in files if f["kind"] == "rom" and f.get("system") in BY_ID]
    if any(device.cores(f["system"]) for f in playable):
        return "ok", "ROM"
    if playable:
        return "bad", "NO EMU"
    if any(f["kind"] in ("zip", "maybe") for f in files):
        return "warn", "CHECK"
    if any(f["kind"] == "other" for f in files):
        return "warn", "CHECK"
    return "bad", "PC ONLY"


def parse_rating(page):
    """itch.io pages carry schema.org JSON-LD: {"aggregateRating": {"ratingValue": "4.8", "ratingCount": 116}}.
    Returns {"value": 4.8, "count": 116}, or None when the game has no ratings yet."""
    for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', page, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        ar = data.get("aggregateRating") if isinstance(data, dict) else None
        if isinstance(ar, dict):
            try:
                return {"value": float(ar.get("ratingValue")), "count": int(ar.get("ratingCount") or 0)}
            except (TypeError, ValueError):
                return None
    return None


def _first_url(url):
    """Homebrew Hub gives a website as a string or, for a few entries, a list of strings."""
    if isinstance(url, (list, tuple)):
        url = next((u for u in url if isinstance(u, str) and u), None)
    return url if isinstance(url, str) and url else None


def itch_url(url):
    if isinstance(url, (list, tuple)):  # a few Homebrew Hub entries list several websites
        return next((u for u in map(itch_url, url) if u), None)
    if not isinstance(url, str):
        return None
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return url if host.endswith(".itch.io") else None


def fetch_rating(game):
    """Fill game["rating"] from the game's itch.io page (None if it has no ratings or no itch.io page)."""
    page = game.get("rating_page")
    if not page:
        game["rating"] = None
        return None
    html_text = net.get_bytes(page).decode("utf-8", "replace")
    game["rating"] = parse_rating(html_text)
    return html_text


def _strip_html(s):
    s = re.sub(r"<br\s*/?>|</p>", "\n", s or "", flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(s)).strip()


def _people(v):
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        names = [p if isinstance(p, str) else (p.get("name") or p.get("display_name") or "")
                 for p in v if isinstance(p, (str, dict))]
        return ", ".join(n for n in names if n)
    if isinstance(v, dict):
        return v.get("name") or v.get("display_name") or v.get("username") or ""
    return ""


# ------------------------------------------------------------ Homebrew Hub
class HomebrewHub:
    NAME = "Homebrew Hub"
    BASE = "https://hh3.gbdev.io"
    PLATFORMS = {"gb": "GB", "gbc": "GBC", "gba": "GBA", "nes": "NES"}
    SYSTEM_OF = {v: k for k, v in PLATFORMS.items()}

    def __init__(self, include_demos=False):
        self.include_demos = include_demos

    def systems(self):
        return list(self.PLATFORMS)

    SERVER_SORTS = {"new", "az"}  # "top" is sorted in the app (ratings come from itch.io)

    def browse(self, sid, page, sort="new", genre=None):
        q = {"platform": self.PLATFORMS[sid], "page": page}
        if sort == "az":
            q.update(sort="title", order="asc")
        if not genre:
            return self._search(q)
        label = dict(GENRES).get(genre, genre)
        games, more, total = [], False, 0
        for tag in dict.fromkeys((label, genre)):  # "Puzzle" and "puzzle"
            g, m, t = self._search(dict(q, tags=tag))
            known = {x["key"] for x in games}
            games += [x for x in g if x["key"] not in known]
            more, total = more or m, total + (t or 0)
        return games, more, total

    def search(self, text, page):
        return self._search({"q": text, "page": page})

    def _search(self, q):
        if not self.include_demos:
            q["typetag"] = "game"
        d = net.get_json(f"{self.BASE}/api/search?{urllib.parse.urlencode(q)}")
        games = [g for g in (self._game(e) for e in d.get("entries") or []) if g]
        more = int(d.get("page_current") or 1) < int(d.get("page_total") or 1)
        return games, more, d.get("results")

    def _game(self, e):
        slug, basepath = e.get("slug"), e.get("basepath")
        if not slug or not basepath:
            return None
        sid = self.SYSTEM_OF.get(e.get("platform"))
        base = f"{self.BASE}/static/{basepath}/entries/{urllib.parse.quote(slug)}/"
        shots = [s for s in e.get("screenshots") or [] if isinstance(s, str)]
        files = []
        for f in sorted(e.get("files") or [], key=lambda f: not f.get("default")):
            fn = f.get("filename") or ""
            if not fn:
                continue
            files.append({"name": os.path.basename(fn), "filename": os.path.basename(fn), "size": None,
                          "system": system_for_file(fn, sid) or sid, "kind": file_kind(fn),
                          "url": base + urllib.parse.quote(fn)})
        desc = (e.get("description") or "").strip()
        rating_page = itch_url(e.get("website")) or itch_url(e.get("gameWebsite"))
        extra = {"rating_page": rating_page} if rating_page else {"rating_page": None, "rating": None}
        return {**extra, "key": f"hbh:{slug}", "src": "hbh", "title": e.get("title") or slug,
                "author": _people(e.get("developer")), "system": sid,
                "cover": base + urllib.parse.quote(shots[0]) if shots else None,
                "screenshots": [base + urllib.parse.quote(x) for x in shots],
                "summary": desc.split("\n")[0][:200], "description": desc,
                "license": e.get("license") or "", "tags": e.get("tags") or [],
                "page": _first_url(e.get("gameWebsite")) or _first_url(e.get("website")) or f"{self.BASE}/game/{slug}",
                "files": files, "check": None, "typetag": e.get("typetag")}


# ------------------------------------------------------------------- itch
class Itch:
    NAME = "itch.io"
    API = "https://api.itch.io"
    TAGS = {"gb": "tag-game-boy", "gbc": "tag-game-boy-color", "gba": "tag-game-boy-advance",
            "nes": "tag-nes-rom", "snes": "tag-snes", "md": "tag-sega-genesis",
            "sms": "tag-sega-master-system", "gg": "tag-game-gear"}

    def __init__(self, api_key=""):
        self.api_key = (api_key or "").strip()

    @property
    def has_key(self):
        return bool(self.api_key)

    def _auth(self):
        return {"Authorization": f"Bearer {self.api_key}"}

    def systems(self):
        return list(self.TAGS)

    SERVER_SORTS = {"new", "top", "popular"}

    # --- lists
    def browse(self, sid, page, sort="popular", genre=None):
        order = {"new": "/newest", "top": "/top-rated"}.get(sort, "")
        genre_part = f"/genre-{genre}" if genre else ""
        data = net.get_bytes(f"https://itch.io/games{order}/free{genre_part}/{self.TAGS[sid]}.xml?page={page}")
        games = self._parse_rss(data, sid)
        return games, len(games) >= 30, None

    def _parse_rss(self, data, sid):
        try:
            root = ET.fromstring(data)
        except ET.ParseError as e:
            raise net.NetError("itch.io sent a list the app couldn't read.") from e
        out = []
        for it in root.iter("item"):
            link = (it.findtext("link") or "").strip()
            if not link:
                continue
            title = it.findtext("plainTitle") or it.findtext("title") or link
            platforms = (it.findtext("platforms") or "").strip()
            host = urllib.parse.urlparse(link).hostname or ""
            summary = _strip_html(it.findtext("description") or "")
            out.append({"key": f"itch:{link}", "src": "itch", "title": title.strip(),
                        "author": host.split(".")[0], "system": sid,
                        "cover": (it.findtext("imageurl") or "").strip() or None,
                        "summary": summary.split("\n")[0][:200], "description": summary,
                        "license": "", "tags": [], "page": link, "files": None, "check": None,
                        "pc_platforms": platforms, "rating_page": link})
        return out

    def library(self, page):
        d = net.get_json(f"{self.API}/profile/owned-keys?page={page}", self._auth())
        games = []
        for k in d.get("owned_keys") or []:
            g = k.get("game") or {}
            if g.get("classification") not in (None, "game"):
                continue
            games.append(self._api_game(g, k.get("id")))
        return games, len(d.get("owned_keys") or []) >= int(d.get("per_page") or 50), None

    def search(self, text, page):
        q = urllib.parse.urlencode({"query": text, "page": page})
        d = net.get_json(f"{self.API}/search/games?{q}", self._auth())
        games = [self._api_game(g) for g in d.get("games") or []
                 if g.get("classification") in (None, "game")]
        return games, len(games) >= int(d.get("per_page") or 20), None

    def _api_game(self, g, download_key_id=None):
        url = g.get("url") or ""
        return {"key": f"itch:{url or g.get('id')}", "src": "itch", "title": g.get("title") or "?",
                "author": _people(g.get("user")), "system": None,
                "cover": g.get("still_cover_url") or g.get("cover_url"),
                "summary": (g.get("short_text") or "")[:200], "description": g.get("short_text") or "",
                "license": "", "tags": [], "page": url, "files": None, "check": None,
                "game_id": g.get("id"), "download_key_id": download_key_id, "rating_page": itch_url(url)}

    # --- details / files
    def inspect(self, game, device):
        """Fill game['files'] and game['check'] (runs in a background thread)."""
        if self.has_key and not game.get("game_id") and game.get("page"):
            try:
                d = net.get_json(game["page"].rstrip("/") + "/data.json")
                game["game_id"] = d.get("id")
                game["author"] = _people(d.get("authors")) or game["author"]
                game["tags"] = d.get("tags") or []
                if d.get("cover_image"):
                    game["cover"] = d["cover_image"]
            except net.NetError:
                if self.has_key:
                    raise
        page = None
        try:
            page = fetch_rating(game)
        except net.NetError:
            if not (self.has_key and game.get("game_id")):
                raise
        if page:
            shots = re.findall(r'href="(https://img\.itch\.zone/[^"]+)"[^>]*data-image_lightbox', page)
            game["screenshots"] = list(dict.fromkeys(shots))
        if self.has_key and game.get("game_id"):
            files = self._api_files(game)
        else:
            files = self._page_files(page or "", game.get("system"))
        game["files"] = files
        game["check"] = verdict(files, device)
        if not files and not self.has_key:
            # Page layout not recognised: fall back to the platforms listed in the feed
            game["check"] = ("bad", "PC ONLY") if game.get("pc_platforms") else ("warn", "CHECK")
        if not game.get("system"):
            game["system"] = next((f["system"] for f in files if f["kind"] == "rom" and f.get("system")), None)
        return game

    def _api_files(self, game):
        q = f"?download_key_id={game['download_key_id']}" if game.get("download_key_id") else ""
        d = net.get_json(f"{self.API}/games/{game['game_id']}/uploads{q}", self._auth())
        files = []
        for u in d.get("uploads") or []:
            fn = u.get("filename") or u.get("display_name") or ""
            if u.get("type") not in (None, "default", "other"):
                continue  # soundtracks, books, videos…
            pc = any(u.get(p) for p in ("p_windows", "p_osx", "p_linux", "p_android"))
            files.append({"name": u.get("display_name") or fn, "filename": fn, "size": u.get("size"),
                          "system": system_for_file(fn, game.get("system")),
                          "kind": file_kind(fn, pc, u.get("display_name") or ""), "upload_id": u.get("id")})
        return files

    def _page_files(self, page, hint):
        files = []
        for m in re.finditer(r'<div class="upload_name"><strong title="([^"]*)"[^>]*>.*?'
                             r'(?:<span class="file_size"><span>([^<]*)</span></span>)?\s*'
                             r'<span class="download_platforms">(.*?)</span></div>', page, re.S):
            name, size, plats = html.unescape(m.group(1)), m.group(2), m.group(3)
            pc = bool(plats.strip())
            files.append({"name": name, "filename": name, "size_text": size,
                          "system": system_for_file(name, hint), "kind": file_kind(name, pc)})
        # Without an API key the files can't be matched to real file names, so a
        # non-PC file whose name isn't a ROM file name is shown as "CHECK" (warn).
        return files

    def download_url(self, game, f):
        if not self.has_key:
            raise net.NetError("Downloading from itch.io needs your itch.io API key. "
                               "See Menu → itch.io API key.")
        q = f"?download_key_id={game['download_key_id']}" if game.get("download_key_id") else ""
        r = net.redirect_target(f"{self.API}/uploads/{f['upload_id']}/download{q}", self._auth())
        if r[0] == "redirect":
            return r[1]
        body, ctype = r[1], r[2]
        if "json" in ctype:
            try:
                d = json.loads(body.decode("utf-8"))
            except ValueError:
                d = {}
            if d.get("url"):
                return d["url"]
            if d.get("errors"):
                raise net.NetError("itch.io: " + "; ".join(map(str, d["errors"])))
        raise net.NetError("itch.io didn't give a download link for this file.")
