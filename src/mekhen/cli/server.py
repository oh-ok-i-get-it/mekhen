"""The local server behind `mekhen serve`: one per user, showing every project that joined it.

Security:
- binds to 127.0.0.1 only and rejects any Host header but 127.0.0.1:<port> / localhost:<port>
  (DNS rebinding)
- GET/HEAD only; no CORS headers. Projects are looked up by key and diagrams by id in what the
  server discovered itself, viewer files come from a fixed allow-list: no request names a path
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from mekhen import __version__
from mekhen.cli import instances, vendor
from mekhen.cli.registry import Registry
from mekhen.core.project import Project
from mekhen.core.store import describe, project_key
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
API_SCHEMA = 2


class Broadcaster:
    """Fans events out to every connected viewer (one queue per SSE connection)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: set[queue.Queue[str]] = set()
        self.last_seen = time.monotonic()  # when a viewer was last connected

    def add(self) -> queue.Queue[str]:
        q: queue.Queue[str] = queue.Queue()
        with self._lock:
            self._clients.add(q)
        return q

    def remove(self, q: queue.Queue[str]) -> None:
        with self._lock:
            self._clients.discard(q)
            self.last_seen = time.monotonic()

    @property
    def count(self) -> int:
        with self._lock:
            if self._clients:
                self.last_seen = time.monotonic()
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

    def __init__(self, registry: Registry, port: int = 0, offline: bool = False, quiet: bool = True,
                 idle_minutes: float = 0) -> None:
        self.registry = registry
        self.offline = offline
        self.quiet = quiet
        self.idle_minutes = idle_minutes
        self.assets = viewer_assets()
        self.broadcaster = Broadcaster()
        self.stopping = threading.Event()
        self.last_activity = time.monotonic()
        super().__init__(("127.0.0.1", port), Handler)
        self.allowed_hosts = {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"}

    def index_html(self) -> bytes:
        html = self.assets["index.html"].read_text(encoding="utf-8")
        if self.offline:
            tag = '<script src="/vendor/mermaid.min.js"></script>'
        else:
            tag = f'<script src="{MERMAID_URL}" integrity="{MERMAID_SRI}" crossorigin="anonymous"></script>'
        return html.replace(MERMAID_SLOT, tag).encode("utf-8")

    def idle(self) -> bool:
        if not self.idle_minutes or self.broadcaster.count:
            return False
        quiet_since = max(self.last_activity, self.broadcaster.last_seen)
        return time.monotonic() - quiet_since > self.idle_minutes * 60


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
        registry = self.server.registry
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
                self._json({"schema": API_SCHEMA, "app": "mekhen", "version": __version__,
                            "viewers": self.server.broadcaster.count, "projects": sorted(registry.entries)})
            elif path == "/api/projects":
                self._json(registry.projects_json())
            elif path == "/api/events":
                self.events()
            elif path.startswith("/api/projects/"):
                self.project_route(path[len("/api/projects/"):])
            else:
                self._error(404, "not found")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def project_route(self, rest: str) -> None:
        key, _, tail = rest.partition("/")
        project = self.server.registry.get(urllib.parse.unquote(key))
        if project is None:
            self._error(404, "no such project")
        elif tail == "":
            self._json(project.project_json(offline_available=vendor.offline_copy() is not None)
                       | {"key": project_key(project.root)})
        elif tail == "diagrams":
            self._json(project.list_json())
        elif tail.startswith("diagrams/"):
            diagram_id = urllib.parse.unquote(tail[len("diagrams/"):])
            data = project.show_json(diagram_id)
            if data is None:
                self._error(404, f"no diagram {diagram_id!r}")
            else:
                self._json(data)
        else:
            self._error(404, "not found")

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
    """One thread: picks up new projects and plans, polls every project, sends events, and stops
    an idle background server."""

    def run() -> None:
        while not server.stopping.wait(interval):
            try:
                if server.registry.scan():
                    server.last_activity = time.monotonic()
                    server.broadcaster.publish("projects", server.registry.projects_json())
                for key, changes in server.registry.poll():
                    server.last_activity = time.monotonic()
                    server.broadcaster.publish("diagrams", {"project": key, **changes.to_json()})
            except Exception:  # noqa: BLE001 - one bad project must not stop the others
                pass
            if server.idle():
                server.stopping.set()
                threading.Thread(target=server.shutdown, daemon=True).start()

    thread = threading.Thread(target=run, name="mekhen-watch", daemon=True)
    thread.start()
    return thread


def serve(project: Project | None, port: int = 0, open_browser: bool = True, offline: bool = False,
          idle_minutes: float = 0) -> int:
    if offline and vendor.offline_copy() is None:
        print("mekhen: no offline Mermaid copy yet; run `mekhen mermaid download` first", file=sys.stderr)
        return 1
    registry = Registry()
    if project is not None:
        registry.add(project)
    registry.scan()
    try:
        server = MekhenServer(registry, port=port, offline=offline, idle_minutes=idle_minutes)
    except OSError as e:
        print(f"mekhen: cannot listen on 127.0.0.1:{port}: {e}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{server.server_port}/"
    instances.register(server.server_port)
    start_watcher(server)
    print(f"mekhen: serving at {url}")
    if project is not None:
        print(f"project: {project.root}  (store: {describe(project.cfg)})")
    print("Press Ctrl+C to stop." if not idle_minutes else f"Stops after {idle_minutes:g} idle minutes.")
    if open_browser:
        webbrowser.open(url + (f"#{project_key(project.root)}" if project else ""))
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("\nmekhen: stopped")
    finally:
        server.stopping.set()
        server.server_close()
        instances.unregister(server.server_port)
    return 0
