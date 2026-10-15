"""The local server behind `mekhen serve`.

Security:
- binds to 127.0.0.1 only and rejects any Host header but 127.0.0.1:<port> / localhost:<port>
  (DNS rebinding)
- GET/HEAD only; no CORS headers; diagrams are looked up by id in the discovered index, viewer
  files come from a fixed allow-list, so no request ever names a file path
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mekhen.cli import instances, vendor
from mekhen.core.project import Project
from mekhen.core.store import describe
from mekhen.core.watch import ChangeSet, Watcher
from mekhen.mermaid_pin import MERMAID_SRI, MERMAID_URL

VIEWER_DIR = Path(__file__).resolve().parent.parent / "viewer"
CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
}
CSP = ("default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'")
MERMAID_SLOT = "<!-- mekhen:mermaid -->"
HEARTBEAT_SECONDS = 15


class Broadcaster:
    """Fans watcher events out to every connected viewer (one queue per SSE connection)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: set[queue.Queue[str]] = set()

    def add(self) -> queue.Queue[str]:
        q: queue.Queue[str] = queue.Queue()
        with self._lock:
            self._clients.add(q)
        return q

    def remove(self, q: queue.Queue[str]) -> None:
        with self._lock:
            self._clients.discard(q)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._clients)

    def publish(self, event: str, data: dict) -> None:
        message = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        with self._lock:
            for q in self._clients:
                q.put(message)


def viewer_assets() -> dict[str, Path]:
    """The allow-list: every file directly inside the viewer folder with a known type."""
    return {p.name: p for p in VIEWER_DIR.iterdir() if p.is_file() and p.suffix in CONTENT_TYPES}


class MekhenServer(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second process bind the same port; never allow that.
    allow_reuse_address = sys.platform != "win32"

    def __init__(self, project: Project, port: int = 0, offline: bool = False, quiet: bool = True) -> None:
        self.project = project
        self.offline = offline
        self.quiet = quiet
        self.assets = viewer_assets()
        self.broadcaster = Broadcaster()
        self.stopping = threading.Event()
        super().__init__(("127.0.0.1", port), Handler)
        self.allowed_hosts = {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"}

    def index_html(self) -> bytes:
        html = self.assets["index.html"].read_text(encoding="utf-8")
        if self.offline:
            tag = '<script src="/vendor/mermaid.min.js"></script>'
        else:
            tag = f'<script src="{MERMAID_URL}" integrity="{MERMAID_SRI}" crossorigin="anonymous"></script>'
        return html.replace(MERMAID_SLOT, tag).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server: MekhenServer
    server_version = "mekhen"

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - signature from the base class
        if not self.server.quiet:
            super().log_message(format, *args)

    # -- responses --------------------------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str, *, cache: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=31536000, immutable" if cache else "no-store")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data: dict, status: int = 200) -> None:
        self._send(status, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status)

    # -- routing ----------------------------------------------------------------------------

    def do_GET(self) -> None:
        self.route()

    def do_HEAD(self) -> None:
        self.route()

    def _not_allowed(self) -> None:
        self._error(405, "read-only server")

    do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _not_allowed

    def route(self) -> None:
        if self.headers.get("Host", "") not in self.server.allowed_hosts:
            self._error(403, "unexpected Host header")
            return
        path = urllib.parse.urlsplit(self.path).path
        project = self.server.project
        try:
            if path in ("/", "/index.html"):
                self._send(200, self.server.index_html(), CONTENT_TYPES[".html"])
            elif path.startswith("/viewer/"):
                name = path[len("/viewer/"):]
                asset = self.server.assets.get(name)
                if asset is None or name == "index.html":
                    self._error(404, "not found")
                else:
                    self._send(200, asset.read_bytes(), CONTENT_TYPES[asset.suffix])
            elif path == "/vendor/mermaid.min.js":
                copy = vendor.offline_copy()
                if copy is None:
                    self._error(404, "no offline Mermaid copy; run `mekhen mermaid download`")
                else:
                    self._send(200, copy.read_bytes(), CONTENT_TYPES[".js"], cache=True)
            elif path == "/api/health":
                self._json({"schema": 1, "app": "mekhen", "root": str(project.root),
                            "viewers": self.server.broadcaster.count})
            elif path == "/api/project":
                self._json(project.project_json(offline_available=vendor.offline_copy() is not None))
            elif path == "/api/diagrams":
                self._json(project.list_json())
            elif path.startswith("/api/diagrams/"):
                diagram_id = urllib.parse.unquote(path[len("/api/diagrams/"):])
                data = project.show_json(diagram_id)
                if data is None:
                    self._error(404, f"no diagram {diagram_id!r}")
                else:
                    self._json(data)
            elif path == "/api/events":
                self.events()
            else:
                self._error(404, "not found")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command == "HEAD":
            return
        q = self.server.broadcaster.add()
        try:
            self.wfile.write(b"retry: 1000\n\n")
            self.wfile.flush()
            while not self.server.stopping.is_set():
                try:
                    message = q.get(timeout=HEARTBEAT_SECONDS)
                except queue.Empty:
                    message = ": ping\n\n"
                self.wfile.write(message.encode("utf-8"))
                self.wfile.flush()
        except OSError:
            pass  # the viewer went away
        finally:
            self.server.broadcaster.remove(q)


def start_watcher(server: MekhenServer, interval: float = 0.3) -> threading.Thread:
    watcher = Watcher(server.project, interval=interval)

    def publish(changes: ChangeSet) -> None:
        server.broadcaster.publish("diagrams", changes.to_json())

    thread = threading.Thread(target=watcher.run, args=(server.stopping, publish), name="mekhen-watch", daemon=True)
    thread.start()
    return thread


def serve(project: Project, port: int = 0, open_browser: bool = True, offline: bool = False) -> int:
    existing = instances.find(project.root)
    if existing:
        print(f"mekhen is already serving this project at {existing.url}")
        if open_browser and existing.viewers == 0:
            webbrowser.open(existing.url)
        elif open_browser:
            print("A viewer is already open; not opening another tab.")
        return 0
    if offline and vendor.offline_copy() is None:
        print("mekhen: no offline Mermaid copy yet; run `mekhen mermaid download` first", file=sys.stderr)
        return 1
    try:
        server = MekhenServer(project, port=port, offline=offline)
    except OSError as e:
        print(f"mekhen: cannot listen on 127.0.0.1:{port}: {e}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{server.server_port}/"
    instances.register(project.root, server.server_port)
    start_watcher(server)
    print(f"mekhen: serving {project.root.name} at {url}")
    print(f"store: {describe(project.cfg)}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nmekhen: stopped")
    finally:
        server.stopping.set()
        server.server_close()
        instances.unregister(project.root, server.server_port)
    return 0
