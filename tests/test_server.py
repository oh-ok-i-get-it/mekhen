import http.client
import json
import threading

import pytest
from conftest import make_project, write

from mekhen.cli import instances
from mekhen.cli.server import MekhenServer, start_watcher


@pytest.fixture
def server(project_dir):
    project = make_project(project_dir)
    project.new("flow")
    srv = MekhenServer(project, port=0)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv
    srv.stopping.set()
    srv.shutdown()
    srv.server_close()


def request(srv, path, host=None, method="GET"):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", f"127.0.0.1:{srv.server_port}" if host is None else host)
    conn.endheaders()
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response.status, response.headers, body


def test_index_and_api(server):
    status, headers, body = request(server, "/")
    assert status == 200 and b'<script type="module" src="/viewer/app.js">' in body
    assert b"cdn.jsdelivr.net/npm/mermaid@" in body and b'integrity="sha384-' in body
    assert "script-src 'self' https://cdn.jsdelivr.net" in headers["Content-Security-Policy"]

    status, _, body = request(server, "/api/diagrams")
    assert status == 200 and json.loads(body)["diagrams"][0]["id"] == "flow"

    status, _, body = request(server, "/api/diagrams/flow")
    data = json.loads(body)
    assert status == 200 and data["baseline"]["text"].startswith("%% mekhen:hand-written")

    status, _, body = request(server, "/api/project")
    assert json.loads(body)["store"]["kind"] == "repo"

    status, _, _ = request(server, "/viewer/app.js")
    assert status == 200


def test_localhost_host_is_allowed(server):
    assert request(server, "/api/health", host=f"localhost:{server.server_port}")[0] == 200


@pytest.mark.parametrize("host", ["evil.example:80", "127.0.0.1", "127.0.0.1:1", ""])
def test_foreign_host_rejected(server, host):
    assert request(server, "/api/diagrams", host=host)[0] == 403


@pytest.mark.parametrize(
    "path",
    [
        "/viewer/../core/project.py",
        "/viewer/%2e%2e/core/project.py",
        "/viewer/..%2fcli%2fserver.py",
        "/viewer/C:/Windows/win.ini",
        "/viewer//etc/passwd",
        "/viewer/index.html",
        "/api/diagrams/..%2f..%2fpyproject.toml",
        "/api/diagrams/%2e%2e%2fbaseline%2fflow",
        "/pyproject.toml",
        "/.diagrams/baseline/flow.mmd",
        "/vendor/../mekhen/cli/server.py",
    ],
)
def test_no_path_traversal(server, path):
    status, _, body = request(server, path)
    assert status == 404, (path, body[:200])


def test_read_only(server):
    assert request(server, "/api/diagrams", method="POST")[0] == 405
    assert request(server, "/api/diagrams/flow", method="DELETE")[0] == 405


def test_health_and_instance_reuse(server, project_dir):
    instances.register(server.project.root, server.server_port)
    found = instances.find(server.project.root)
    assert found is not None and found.port == server.server_port and found.viewers == 0
    instances.unregister(server.project.root, server.server_port)
    assert instances.find(server.project.root) is None


def test_stale_registry_entry_is_dropped(project_dir):
    root = project_dir.resolve()
    instances.register(root, 9)  # nothing listens on the discard port
    assert instances.find(root) is None


def test_sse_pushes_changed_ids(server, project_dir):
    start_watcher(server, interval=0.05)
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    conn.request("GET", "/api/events")
    response = conn.getresponse()
    assert response.status == 200 and response.headers["Content-Type"].startswith("text/event-stream")
    assert response.fp.readline() == b"retry: 1000\n"
    write(project_dir / ".diagrams" / "proposed" / "flow.mmd", 'flowchart TD\n    a["changed"]\n')
    lines = []
    while not any(line.startswith(b"data:") for line in lines):
        lines.append(response.fp.readline())
    event = [line for line in lines if line.startswith(b"event:")][0]
    data = json.loads([line for line in lines if line.startswith(b"data:")][0][5:])
    assert event.strip() == b"event: diagrams"
    assert data == {"changed": ["flow"], "added": [], "removed": []}
    conn.close()
