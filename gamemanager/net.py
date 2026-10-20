# © 2026 PocketKode · SPDX-License-Identifier: GPL-3.0-only
"""HTTP helpers (standard library only) with plain-English errors."""
import http.client
import json
import os
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

def user_agent(version):
    """How the app introduces itself to the sites it reads (set from APP_VERSION when the app starts)."""
    return f"Mozilla/5.0 (X11; Linux aarch64) GameManager/{version} (muOS; +https://pocketkode.com/apps/game-manager/)"


USER_AGENT = user_agent("2")


class NetError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def friendly(e):
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (401, 403):
            return f"The server refused access (HTTP {e.code})."
        if e.code == 404:
            return "Not found on the server (HTTP 404). It may have been removed."
        if e.code == 429:
            return "The server says there were too many requests. Wait a minute and try again."
        return f"The server had a problem (HTTP {e.code}). Try again later."
    reason = getattr(e, "reason", e)
    low = str(reason).lower()
    if isinstance(reason, socket.gaierror) or any(k in low for k in (
            "name or service", "temporary failure in name", "network is unreachable",
            "no address associated", "nodename nor servname", "no route to host")):
        return "No internet connection. Turn on Wi-Fi in muOS (Configuration → Network) and try again."
    if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in low:
        return "The connection timed out. Check Wi-Fi and try again."
    if isinstance(reason, ssl.SSLError) or "certificate" in low:
        return ("Secure connection failed. Make sure the date and time are correct in muOS "
                "(Configuration → Date and Time), then try again.")
    return f"Network error: {reason}"


# itch.io answers "429 Too Many Requests" to bursts, so its pages and API are fetched
# one at a time with a short gap, and a 429 is retried after a pause.
_THROTTLED = ("itch.io", "api.itch.io")
_throttle_lock = threading.Lock()
_last_request = [0.0]
MIN_GAP = 0.8


def _throttle(url):
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host in _THROTTLED or host.endswith(".itch.io"):
        with _throttle_lock:
            wait = _last_request[0] + MIN_GAP - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _last_request[0] = time.monotonic()


def _retry_after(e, attempt):
    try:
        return min(20.0, float(e.headers.get("Retry-After")))
    except (TypeError, ValueError, AttributeError):
        return 3.0 * (attempt + 1)


def _request(url, headers):
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    return urllib.request.Request(url, headers=h)


RETRY_HTTP = (500, 502, 503, 504)  # a busy or restarting server: usually fine a moment later


def _glitch(e, attempt):
    """A short network glitch worth trying again: a dropped connection (up to twice) or a time-out (once).
    No internet at all, or a certificate problem, is reported at once."""
    reason = getattr(e, "reason", e)
    if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(reason).lower():
        return attempt == 0
    return isinstance(reason, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError))


def open_url(url, headers=None, timeout=20, opener=None):
    for attempt in range(3):
        _throttle(url)
        try:
            req = _request(url, headers)
            return opener.open(req, timeout=timeout) if opener else urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(_retry_after(e, attempt))
                continue
            if e.code in RETRY_HTTP and attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise NetError(friendly(e), e.code) from e
        except (urllib.error.URLError, OSError) as e:
            if attempt < 2 and _glitch(e, attempt):
                time.sleep(1.0 * (attempt + 1))
                continue
            raise NetError(friendly(e)) from e


def get_bytes(url, headers=None, timeout=20, limit=20 * 1024 * 1024):
    with open_url(url, headers, timeout) as r:
        try:
            data = r.read(limit + 1)
        except (OSError, ValueError) as e:
            raise NetError(friendly(e)) from e
    if len(data) > limit:
        raise NetError("The server sent more data than expected.")
    return data


def head(url, headers=None, timeout=15):
    """The size (Content-Length) of a URL in bytes, or None. Opens the URL but reads none of the body."""
    try:
        with open_url(url, headers, timeout) as r:
            n = int(r.headers.get("Content-Length") or 0)
            return n or None
    except (NetError, OSError, ValueError):
        return None


def get_json(url, headers=None, timeout=20):
    data = get_bytes(url, headers, timeout)
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise NetError("The server sent a reply the app couldn't read.") from e


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def redirect_target(url, headers=None, timeout=20):
    """Request `url` without following redirects.
    Returns ("redirect", location) or ("body", bytes, content_type)."""
    opener = urllib.request.build_opener(_NoRedirect)
    _throttle(url)
    try:
        with opener.open(_request(url, headers), timeout=timeout) as r:
            return "body", r.read(2 * 1024 * 1024), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
            return "redirect", e.headers["Location"]
        raise NetError(friendly(e), e.code) from e
    except (urllib.error.URLError, OSError) as e:
        raise NetError(friendly(e)) from e


RESUMES = 3  # how many times a download that stopped part-way carries on from where it stopped


def _resumed_at(r):
    """Where a 206 (partial) reply starts, from Content-Range: bytes 1000-1999/2000 → 1000; else None."""
    if getattr(r, "status", 200) != 206:
        return None
    m = re.match(r"bytes (\d+)-", r.headers.get("Content-Range") or "")
    return int(m.group(1)) if m else None


def download(url, dest, task, headers=None, timeout=30, max_size=512 * 1024 * 1024):
    """Stream `url` to `dest` (via dest.part), updating task.progress. Returns bytes written.
    If the connection drops part-way, the download carries on from where it stopped (asking the server
    for the rest with a Range request), or starts again if the server can't do that."""
    part = dest + ".part"
    done, total, resumes = 0, 0, 0
    try:
        with open(part, "wb") as f:
            while True:
                h = dict(headers or {})
                if done:
                    h["Range"] = f"bytes={done}-"
                with open_url(url, h, timeout) as r:
                    if done and _resumed_at(r) != done:  # the server sent the whole file again
                        f.seek(0)
                        f.truncate()
                        done = 0
                    if not done:
                        total = int(r.headers.get("Content-Length") or 0)
                        if total > max_size:
                            raise NetError(f"The file is too big ({total // (1024 * 1024)} MB).")
                    try:
                        while True:
                            task.check()
                            try:
                                chunk = r.read(64 * 1024)
                            except (OSError, ValueError, http.client.HTTPException) as e:
                                raise NetError(friendly(e)) from e
                            if not chunk:
                                break
                            f.write(chunk)
                            done += len(chunk)
                            if done > max_size:
                                raise NetError("The file is bigger than this app allows (512 MB).")
                            task.progress = done / total if total else None
                    except NetError:
                        if total and done == total:
                            pass  # it dropped after the last byte: the file is complete
                        elif not (done and resumes < RESUMES) or done > max_size:
                            raise
                        else:
                            resumes += 1
                            time.sleep(1.0)
                            continue
                if total and done < total:
                    if resumes < RESUMES:
                        resumes += 1
                        continue
                    raise NetError("The download stopped early. Check Wi-Fi and try again.")
                break
        os.replace(part, dest)
        return done
    finally:
        if os.path.exists(part):
            os.remove(part)
