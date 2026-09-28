# SPDX-License-Identifier: MIT
# Copyright (c) 2026 PocketKode
"""HTTP helpers (standard library only) with plain-English errors."""
import json
import os
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "Mozilla/5.0 (X11; Linux aarch64) GameManager/1.3 (muOS; +https://github.com/pocketkode/game-manager)"


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
            raise NetError(friendly(e), e.code) from e
        except (urllib.error.URLError, OSError) as e:
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


def download(url, dest, task, headers=None, timeout=30, max_size=512 * 1024 * 1024):
    """Stream `url` to `dest` (via dest.part), updating task.progress. Returns bytes written."""
    part = dest + ".part"
    done = 0
    try:
        with open_url(url, headers, timeout) as r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            if total > max_size:
                raise NetError(f"The file is too big ({total // (1024 * 1024)} MB).")
            while True:
                task.check()
                try:
                    chunk = r.read(64 * 1024)
                except (OSError, ValueError) as e:
                    raise NetError(friendly(e)) from e
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if done > max_size:
                    raise NetError("The file is bigger than this app allows (512 MB).")
                task.progress = done / total if total else None
        if total and done < total:
            raise NetError("The download stopped early. Check Wi-Fi and try again.")
        os.replace(part, dest)
        return done
    finally:
        if os.path.exists(part):
            os.remove(part)
