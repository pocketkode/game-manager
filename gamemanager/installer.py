# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PocketKode
"""Download → unpack → check → install into ROMS, plus the list of installed games."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import zipfile

from . import net
from . import romcheck
from .systems import BY_ID, ROM_EXTS, system_for_file


class InstallError(Exception):
    pass


def safe_name(title):
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", title or "")
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:80].rstrip(" .") or "Game"


class Library:
    """Games installed by this app (data/installed.json)."""

    def __init__(self, path):
        self.path = path
        try:
            with open(path) as f:
                self.items = {k: v for k, v in json.load(f).items() if isinstance(v, dict)}
        except (OSError, ValueError, AttributeError):
            self.items = {}

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.items, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def get(self, key):
        rec = self.items.get(key)
        return rec if rec and os.path.exists(rec.get("path", "")) else None

    def installed(self):
        """Records whose ROM file still exists, newest first."""
        recs = [r for r in self.items.values() if os.path.exists(r.get("path", ""))]
        return sorted(recs, key=lambda r: r.get("installed", ""), reverse=True)

    def add(self, rec):
        self.items[rec["key"]] = rec
        self.save()

    def set_status(self, key, status):
        if key in self.items:
            self.items[key]["status"] = status
            self.save()

    def remove(self, key):
        rec = self.items.pop(key, None)
        if rec:
            for p in [rec.get("path")] + list(rec.get("extra_paths") or []) + list(rec.get("art") or []):
                try:
                    if p and os.path.isdir(p) and rec.get("source") == "pm":
                        shutil.rmtree(p)  # a PortMaster port's own folder
                    elif p and os.path.isfile(p):
                        os.remove(p)
                except OSError:
                    pass
            self.save()


def _filename_for(f, url):
    name = os.path.basename(f.get("filename") or "")
    if not os.path.splitext(name)[1]:
        path_name = os.path.basename(urllib.parse.urlparse(url).path)
        if os.path.splitext(path_name)[1]:
            name = urllib.parse.unquote(path_name)
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip() or "download.bin"
    return name


def _extract_roms(path, hint, out_dir, depth=0):
    """ROM files from a download: the file itself, or ROMs inside a .zip (one nested zip allowed)."""
    ext = os.path.splitext(path.lower())[1]
    if ext in ROM_EXTS:
        return [path] if system_for_file(path, hint) else []
    if not zipfile.is_zipfile(path):
        if ext in (".7z", ".rar"):
            raise InstallError(f"{ext} archives aren't supported. Only .zip and plain ROM files are.")
        return []
    found = []
    try:
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                name = info.filename
                base = os.path.basename(name)
                if info.is_dir() or not base or name.startswith("__MACOSX/") or base.startswith("._"):
                    continue
                mext = os.path.splitext(base.lower())[1]
                wanted = mext in ROM_EXTS and system_for_file(base, hint)
                if not wanted and not (mext == ".zip" and depth == 0):
                    continue
                if info.file_size > 64 * 1024 * 1024:
                    continue
                target = os.path.join(out_dir, f"{len(found)}_{base}")
                with z.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                if mext == ".zip":
                    found += _extract_roms(target, hint, out_dir, depth + 1)
                else:
                    found.append(target)
    except (zipfile.BadZipFile, OSError, RuntimeError) as e:
        raise InstallError(f"The downloaded archive is damaged ({e}).") from e
    return found


def _rom_ext(path):
    low = path.lower()
    return ".p8.png" if low.endswith(".p8.png") else os.path.splitext(low)[1]


def _save_art(device, sid, stem, game, save_art, cover_data):
    """Box art and description for muOS's catalogue. Returns the files written."""
    written = []
    box, txt = device.art_paths(sid, stem)
    if box and cover_data and save_art:
        try:
            os.makedirs(os.path.dirname(box), exist_ok=True)
            if save_art(cover_data, box):
                written.append(box)
        except OSError:
            pass
    text = (game.get("description") or game.get("summary") or "").strip()
    if txt and text:
        try:
            os.makedirs(os.path.dirname(txt), exist_ok=True)
            with open(txt, "w", encoding="utf-8") as fh:
                fh.write(text + "\n")
            written.append(txt)
        except OSError:
            pass
    return written


def _record(game, sid, paths, art, notes, level="ok", core=None, status="untested"):
    return {"key": game["key"], "title": game["title"], "system": sid, "path": paths[0],
            "extra_paths": paths[1:], "art": art, "source": game["src"], "page": game.get("page"),
            "installed": time.strftime("%Y-%m-%d %H:%M"), "status": status,
            "core": core, "level": level, "notes": notes}


def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def install(game, f, device, source, library, tmp_root, task, save_art=None, cover_data=None):
    """Install one file of `game`. Returns the new library record."""
    task.status, task.progress = "Getting the download link…", None
    url = f.get("url") or source.download_url(game, f)
    task.check()

    tmp = os.path.join(tmp_root, "install")
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        fname = _filename_for(f, url)
        dl_path = os.path.join(tmp, fname)
        task.status = f"Downloading {fname}"
        net.download(url, dl_path, task)
        task.status, task.progress = "Checking the file…", None
        task.check()
        if f.get("md5") and _md5(dl_path) != f["md5"].lower():
            raise InstallError("The download is damaged (checksum mismatch). Try again.")

        if f.get("install") == "port":
            rec = _install_port(game, f, dl_path, source, device, tmp_root, task, save_art, cover_data)
        else:
            rec = _install_roms(game, f, dl_path, device, tmp, task, save_art, cover_data)
        library.add(rec)
        return rec
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _install_roms(game, f, dl_path, device, tmp, task, save_art, cover_data):
    hint = f.get("system") or game.get("system")
    roms = _extract_roms(dl_path, hint, tmp)
    if not roms:
        raise InstallError("This download has no console ROM in it. It may be a PC or phone "
                           "version. Try another file (◀ ▶ on the game page) if there is one.")
    checked, rejected = [], []
    for p in roms:
        v = romcheck.check(p, hint)
        (rejected if v.level == "bad" else checked).append((p, v))
    if not checked:
        raise InstallError(rejected[0][1].notes[-1] if rejected else "The ROM didn't pass the check.")

    task.status = "Installing…"
    paths, art, notes, level, core_pref = [], [], [], "ok", None
    for p, v in checked:
        if not device.cores(v.system):
            raise InstallError(f"This device has no emulator for {BY_ID[v.system].name}.")
        folder = device.rom_folder(v.system)
        os.makedirs(folder, exist_ok=True)
        stem = safe_name(game["title"])
        if len(checked) > 1:
            base = os.path.basename(p)
            stem = safe_name(f"{game['title']} - {base[:-len(_rom_ext(base))].split('_', 1)[-1]}")
        dest = os.path.join(folder, stem + _rom_ext(p))
        shutil.move(p, dest)
        paths.append(dest)
        notes += v.notes
        if v.level == "warn":
            level = "warn"
        core_pref = core_pref or v.core
        art += _save_art(device, v.system, stem, game, save_art, cover_data)
    sid = checked[0][1].system
    core = device.core_by_so_prefix(sid, core_pref) if core_pref else None
    return _record(game, sid, paths, art, notes, level, core.so if core else None)


def _install_port(game, f, zip_path, pm, device, tmp_root, task, save_art, cover_data):
    """Install with PortMaster's own installer; if that can't run, queue it for PortMaster."""
    if not pm.pm_dir:
        raise InstallError("PortMaster isn't installed on this device.")
    task.status, task.progress = "Installing with PortMaster…", None
    items = game.get("items") or []
    log_path = os.path.join(os.path.dirname(tmp_root), "logs", "portmaster.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    ok = False
    try:
        with open(log_path, "w") as log:
            rc = subprocess.call(["python3", "harbourmaster", "--quiet", "--no-check", "install", zip_path],
                                 cwd=pm.pm_dir, stdout=log, stderr=subprocess.STDOUT, timeout=900)
        ok = rc == 0 and pm.is_installed(game)
    except (OSError, subprocess.SubprocessError):
        ok = False
    notes, status = [], "untested"
    if not ok:
        auto = os.path.join(pm.pm_dir, "autoinstall")
        os.makedirs(auto, exist_ok=True)
        queued = os.path.join(auto, f["filename"])
        shutil.copyfile(zip_path, queued)
        notes.append("Queued for PortMaster: open PortMaster once and it finishes the install. "
                     "Details in logs/portmaster.log.")
        status = "queued"
    script = next((os.path.join(pm.scripts_dir, i) for i in items if i.endswith(".sh")), None)
    dirs = [os.path.join(pm.ports_dir, i.rstrip("/")) for i in items if i.endswith("/")]
    main = script if script and os.path.exists(script) else os.path.join(pm.pm_dir, "autoinstall", f["filename"])
    stem = os.path.splitext(os.path.basename(script or f["filename"]))[0]
    art = _save_art(device, "ports", stem, game, save_art, cover_data) if ok else []
    return _record(game, "ports", [main] + dirs, art, notes, status=status)
