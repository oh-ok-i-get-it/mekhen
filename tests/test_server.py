import http.client
import json
import threading
import time

import pytest
from conftest import make_project, write

from mekhen.cli import instances
from mekhen.cli.registry import Registry, register_project
from mekhen.cli.server import MekhenServer, start_watcher
from mekhen.core.plans import set_active_plan
from mekhen.core.store import project_key

pytestmark = pytest.mark.usefixtures("repo_store")  # these tests look inside <project>/.diagrams/


def run(srv):
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return srv


def stop(srv):
    srv.stopping.set()
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def server(project_dir):
    project = make_project(project_dir)
    project.new("flow")
    registry = Registry()
    registry.add(project)
    srv = run(MekhenServer(registry, port=0))
    srv.key = project_key(project.root)
    yield srv
    stop(srv)


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

    status, _, body = request(server, "/api/projects")
    data = json.loads(body)
    assert status == 200 and [p["key"] for p in data["projects"]] == [server.key]

    status, _, body = request(server, f"/api/projects/{server.key}/diagrams")
    assert status == 200 and json.loads(body)["diagrams"][0]["id"] == "flow"

    status, _, body = request(server, f"/api/projects/{server.key}/diagrams/flow")
    data = json.loads(body)
    assert status == 200 and data["baseline"]["text"].startswith("%% mekhen:hand-written")

    status, _, body = request(server, f"/api/projects/{server.key}")
    assert json.loads(body)["store"]["kind"] == "repo" and json.loads(body)["key"] == server.key

    assert request(server, "/api/projects/nope/diagrams")[0] == 404
    assert request(server, "/viewer/app.js")[0] == 200


def test_localhost_host_is_allowed(server):
    assert request(server, "/api/health", host=f"localhost:{server.server_port}")[0] == 200


@pytest.mark.parametrize("host", ["evil.example:80", "127.0.0.1", "127.0.0.1:1", ""])
def test_foreign_host_rejected(server, host):
    assert request(server, "/api/projects", host=host)[0] == 403


@pytest.mark.parametrize(
    "path",
    [
        "/viewer/../core/project.py",
        "/viewer/%2e%2e/core/project.py",
        "/viewer/..%2fcli%2fserver.py",
        "/viewer/C:/Windows/win.ini",
        "/viewer//etc/passwd",
        "/viewer/index.html",
        "/api/projects/..%2f..%2fpyproject.toml",
        "/api/projects/KEY/diagrams/..%2f..%2fpyproject.toml",
        "/api/projects/KEY/diagrams/%2e%2e%2fbaseline%2fflow",
        "/api/projects/KEY/..%2f..",
        "/pyproject.toml",
        "/.diagrams/baseline/flow.mmd",
        "/vendor/../mekhen/cli/server.py",
    ],
)
def test_no_path_traversal(server, path):
    status, _, body = request(server, path.replace("KEY", server.key))
    assert status == 404, (path, body[:200])


def test_read_only(server):
    assert request(server, "/api/projects", method="POST")[0] == 405
    assert request(server, f"/api/projects/{server.key}/diagrams/flow", method="DELETE")[0] == 405


def test_health_and_instance_reuse(server):
    instances.register(server.server_port)
    found = instances.find()
    assert found is not None and found.port == server.server_port and found.viewers == 0
    instances.unregister(server.server_port)
    assert instances.find() is None


def test_stale_registry_entry_is_dropped():
    instances.register(9)  # nothing listens on the discard port
    assert instances.find() is None


def read_event(response):
    lines = []
    while not any(line.startswith(b"data:") for line in lines):
        lines.append(response.fp.readline())
    event = [line for line in lines if line.startswith(b"event:")][0].split(b":", 1)[1].strip().decode()
    return event, json.loads([line for line in lines if line.startswith(b"data:")][0][5:])


def test_sse_pushes_changed_ids(server, project_dir):
    start_watcher(server, interval=0.05)
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    conn.request("GET", "/api/events")
    response = conn.getresponse()
    assert response.status == 200 and response.headers["Content-Type"].startswith("text/event-stream")
    assert response.fp.readline() == b"retry: 1000\n"
    write(project_dir / ".diagrams" / "proposed" / "flow.mmd", 'flowchart TD\n    a["changed"]\n')
    event, data = read_event(response)
    assert event == "diagrams"
    assert data == {"project": server.key, "changed": ["flow"], "added": [], "removed": []}
    conn.close()


def test_projects_join_and_plans_are_followed(server, tmp_path):
    start_watcher(server, interval=0.05)
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    conn.request("GET", "/api/events")
    response = conn.getresponse()
    response.fp.readline()

    other = tmp_path / "other"
    write(other / "pyproject.toml", '[project]\nname = "other"\n')
    plan = write(tmp_path / "plan.md", "# Speed up\n")
    set_active_plan(other, plan, "cli")
    register_project(other)
    event, data = read_event(response)
    while event != "projects":
        event, data = read_event(response)
    key = project_key(other)
    assert key in [p["key"] for p in data["projects"]]
    assert data["follow"]["project"] == key
    assert [p["plan"]["title"] for p in data["projects"] if p["key"] == key] == ["Speed up"]
    assert request(server, f"/api/projects/{key}/diagrams")[0] == 200
    conn.close()


def test_old_projects_do_not_join(project_dir, tmp_path):
    register_project(project_dir)
    registry = Registry(since=time.time() + 3600)  # everything on disk predates this server
    registry.scan()
    assert registry.entries == {}


def test_idle_background_server_stops(project_dir):
    srv = run(MekhenServer(Registry(), port=0, idle_minutes=0.001))
    start_watcher(srv, interval=0.02)
    deadline = time.monotonic() + 5
    while not srv.stopping.is_set() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert srv.stopping.is_set()
    srv.server_close()
