"""
Local server for the tracker.

Serves the page and the agent's output, plus one endpoint that reads and writes
your applications to data/applications.json - so they live on disk rather than
only in one browser's storage, where clearing site data or switching browsers
used to lose everything.

Two security properties matter here, because this directory holds token.json
and credentials.json (which together read your mail) and data/state.json
(cached message text):

1. **Allowlist, not a directory listing.** The previous server handed out the
   whole project folder, secrets included, to anything that could reach the
   port. Now only the files the page needs are served.

2. **Only this page can write.** Every request must carry our own Host header,
   which defeats DNS rebinding - a hostile site pointing its own domain at
   127.0.0.1. Writes additionally require a same-origin Origin and a JSON body,
   so a cross-site page cannot make one without a CORS preflight, which this
   server refuses.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
APPS_FILE = DATA / "applications.json"
BACKUP_FILE = DATA / "applications.backup.json"

API_PATH = "/api/applications"

# URL path -> file under ROOT. Anything not listed is a 404.
STATIC = {
    "/": "index.html",
    "/index.html": "index.html",
    "/data/events.json": "data/events.json",
}

MAX_BODY = 5 * 1024 * 1024   # far beyond any real tracker; bounds a bad request

_write_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #

def read_store() -> dict:
    """The saved applications, or an empty store marked as not yet existing."""
    if not APPS_FILE.exists():
        return {"exists": False, "revision": 0, "apps": [], "seen": {}}
    try:
        data = json.loads(APPS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # A damaged file must not look like an empty tracker, or the next save
        # from the browser would overwrite whatever could still be recovered.
        raise StoreCorrupt(APPS_FILE)
    return {
        "exists": True,
        "revision": int(data.get("revision", 0)),
        "savedAt": data.get("savedAt", ""),
        "apps": data.get("apps", []),
        "seen": data.get("seen", {}),
    }


class StoreCorrupt(Exception):
    pass


class StaleRevision(Exception):
    def __init__(self, current: dict):
        self.current = current


def write_store(apps: list, seen: dict, expected_revision: int) -> dict:
    """
    Replace the saved applications, if nobody has written since you loaded.

    Optimistic concurrency: the browser sends the revision it last read. If a
    second tab has saved in the meantime, the write is refused rather than
    silently discarding that tab's changes.

    The write is atomic - a temp file renamed over the original - so a crash
    mid-write leaves the previous version intact, and that previous version is
    kept as a backup before being replaced.
    """
    with _write_lock:
        current = read_store()
        if current["revision"] != expected_revision:
            raise StaleRevision(current)

        payload = {
            "revision": current["revision"] + 1,
            "savedAt": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "apps": apps,
            "seen": seen,
        }

        DATA.mkdir(exist_ok=True)
        if APPS_FILE.exists():
            BACKUP_FILE.write_bytes(APPS_FILE.read_bytes())

        fd, tmp = tempfile.mkstemp(dir=DATA, prefix=".applications-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, APPS_FILE)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

        return {"revision": payload["revision"], "savedAt": payload["savedAt"]}


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

class TrackerHandler(SimpleHTTPRequestHandler):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    # -- guards --------------------------------------------------------------

    def _allowed_hosts(self) -> set[str]:
        port = self.server.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _host_ok(self) -> bool:
        return (self.headers.get("Host") or "").lower() in self._allowed_hosts()

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        # Same-origin fetches may omit Origin; a cross-site one never does.
        return origin is None or origin.lower() in {
            f"http://{h}" for h in self._allowed_hosts()}

    # -- responses -----------------------------------------------------------

    def _json(self, status: int, body: dict) -> None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _deny(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    # -- methods -------------------------------------------------------------

    def do_GET(self):
        if not self._host_ok():
            return self._deny(403, "unexpected host")

        path = urlsplit(self.path).path
        if path == API_PATH:
            try:
                return self._json(200, read_store())
            except StoreCorrupt:
                return self._deny(500, "applications.json is unreadable; "
                                       "a copy may be in applications.backup.json")

        target = STATIC.get(path)
        if target is None:
            return self._deny(404, "not found")
        self.path = "/" + target
        return super().do_GET()

    def do_HEAD(self):
        if not self._host_ok() or urlsplit(self.path).path not in STATIC:
            self.send_response(404)
            self.end_headers()
            return
        self.path = "/" + STATIC[urlsplit(self.path).path]
        return super().do_HEAD()

    def do_PUT(self):
        if not self._host_ok():
            return self._deny(403, "unexpected host")
        if urlsplit(self.path).path != API_PATH:
            return self._deny(404, "not found")
        if not self._origin_ok():
            return self._deny(403, "cross-origin write refused")
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self._deny(415, "expected application/json")

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._deny(400, "bad length")
        if length <= 0 or length > MAX_BODY:
            return self._deny(413, "body too large or empty")

        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            apps, seen = body["apps"], body.get("seen", {})
            revision = int(body["revision"])
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            return self._deny(400, "expected {revision, apps, seen}")

        if not isinstance(apps, list) or not all(isinstance(a, dict) for a in apps):
            return self._deny(400, "apps must be a list of objects")
        if not isinstance(seen, dict):
            return self._deny(400, "seen must be an object")

        try:
            return self._json(200, write_store(apps, seen, revision))
        except StaleRevision as stale:
            return self._json(409, {"error": "changed elsewhere", **stale.current})
        except StoreCorrupt:
            return self._deny(500, "applications.json is unreadable; not overwriting it")

    # Anything else - including OPTIONS, which is what a cross-site preflight
    # needs to succeed - is refused.
    def _refuse(self):
        self._deny(405, "method not allowed")

    do_POST = do_DELETE = do_PATCH = do_OPTIONS = _refuse

    def log_message(self, fmt, *args):
        # Per-request lines drown the console; keep only failures.
        if len(args) >= 2 and str(args[1])[:1] in "45":
            super().log_message(fmt, *args)


def make_server(port: int) -> ThreadingHTTPServer:
    # Loopback only: nothing on your network can reach this.
    return ThreadingHTTPServer(("127.0.0.1", port), TrackerHandler)
