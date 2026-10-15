"""Viewer tests in a real browser, against the pinned Mermaid (loaded from the CDN).

Needs `pip install -e ".[e2e]"` and a browser: MEKHEN_E2E_BROWSER picks a Playwright channel
(default: msedge on Windows, Playwright's own Chromium elsewhere, after `playwright install chromium`).
"""

from __future__ import annotations

import os
import sys
import threading

import pytest
from conftest import make_project, write

sync_api = pytest.importorskip("playwright.sync_api")

from mekhen.cli.registry import Registry  # noqa: E402
from mekhen.cli.server import MekhenServer, start_watcher  # noqa: E402
from mekhen.core.store import project_key  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.usefixtures("repo_store")]

BASE = """%% mekhen:hand-written
flowchart LR
    load["Load"] --> parse["Parse"]
    parse --> check["Check"]
    check --> save[("Store")]
"""
PROPOSED = """flowchart LR
    load["Load"] --> parse["Parse input"]
    parse --> save[("Store")]
    subgraph extra["New stage"]
        notify["Notify"]
    end
    save --> notify
"""


@pytest.fixture(scope="module")
def browser():
    channel = os.environ.get("MEKHEN_E2E_BROWSER", "msedge" if sys.platform == "win32" else "")
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(channel=channel or None)
        except Exception as e:  # browser not installed
            pytest.skip(f"no browser for Playwright ({e.__class__.__name__})")
        yield b
        b.close()


@pytest.fixture
def served(project_dir):
    store = project_dir / ".diagrams"
    write(store / "baseline" / "flow.mmd", BASE)
    write(store / "proposed" / "flow.mmd", PROPOSED)
    write(store / "baseline" / "broken.mmd", "%% mekhen:hand-written\nflowchart TD\n    a --> end\n")
    registry = Registry()
    registry.add(make_project(project_dir))
    srv = MekhenServer(registry, port=0)
    srv.key = project_key(project_dir)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    start_watcher(srv, interval=0.1)
    yield srv, store
    srv.stopping.set()
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def page(browser, served):
    srv, _ = served
    context = browser.new_context(viewport={"width": 1400, "height": 900})
    pg = context.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.base = f"http://127.0.0.1:{srv.server_port}/"
    pg.key = srv.key
    yield pg
    context.close()
    assert errors == []


def ids(page, role, selector):
    return page.eval_on_selector_all(
        f'.panel[data-role="{role}"] .canvas {selector}',
        "els => els.map(e => [e.id, e.getAttribute('class') || '', e.getAttribute('style') || ''])",
    )


def classes_by_node(page, role):
    out = {}
    for el_id, cls, _ in ids(page, role, "g.node"):
        name = el_id.split("-flowchart-")[1].rsplit("-", 1)[0]
        out[name] = cls
    return out


def test_renders_and_highlights(page):
    page.goto(page.base + f"#{page.key}/flow")
    page.wait_for_selector('.panel[data-role="proposed"] .canvas svg', timeout=30000)
    page.wait_for_selector('.panel[data-role="baseline"] .canvas svg')

    proposed = classes_by_node(page, "proposed")
    baseline = classes_by_node(page, "baseline")
    assert "mekhenAdded" in proposed["notify"]
    assert "mekhenChanged" in proposed["parse"] and "mekhenChanged" in baseline["parse"]
    assert "mekhenRemoved" in baseline["check"]
    assert "mekhen" not in proposed["load"]

    # linkStyle indices land on the right edges (Mermaid ids edges L_<src>_<dst>_<n>).
    styled = {el_id.split("-L_")[1]: style for el_id, _, style in ids(page, "proposed", "path.flowchart-link")}
    assert "#1a7f37" in styled["parse_save_0"] and "#1a7f37" in styled["save_notify_0"]
    assert "#1a7f37" not in styled["load_parse_0"]
    removed = {el_id.split("-L_")[1]: style for el_id, _, style in ids(page, "baseline", "path.flowchart-link")}
    assert "#cf222e" in removed["parse_check_0"] and "#cf222e" in removed["check_save_0"]

    # Subgraphs take the class too.
    clusters = {el_id.split("-", 2)[-1]: cls for el_id, cls, _ in ids(page, "proposed", "g.cluster")}
    assert "mekhenAdded" in clusters["extra"]

    summary = page.inner_text("#summary-body")
    assert "notify" in summary and "check" in summary and "Parse input" in summary


def test_highlight_toggle_renders_plain_text(page):
    page.goto(page.base + f"#{page.key}/flow")
    page.wait_for_selector('.panel[data-role="proposed"] .canvas svg', timeout=30000)
    page.uncheck("#highlight")
    page.wait_for_function(
        "() => !document.querySelector('.panel[data-role=\"proposed\"] .canvas .mekhenAdded')")
    page.check("#highlight")
    page.wait_for_selector('.panel[data-role="proposed"] .canvas .mekhenAdded')


def test_syntax_error_is_shown_in_the_panel(page):
    page.goto(page.base + f"#{page.key}/broken")
    page.wait_for_selector('.panel[data-role="baseline"] .panel-error:not([hidden])', timeout=30000)
    assert "Mermaid could not render" in page.inner_text('.panel[data-role="baseline"] .panel-error')
    assert "a --> end" in page.inner_text('.panel[data-role="baseline"] .canvas pre.source')
    assert "No proposed file" in page.inner_text('.panel[data-role="proposed"] .panel-empty')


def test_live_update_keeps_zoom_without_reload(page, served):
    _, store = served
    page.goto(page.base + f"#{page.key}/flow")
    page.wait_for_selector('.panel[data-role="proposed"] .canvas svg', timeout=30000)
    page.wait_for_selector("#conn[data-state='live']")
    page.evaluate("window.__marker = 1")
    page.click("#zoom-in")
    page.click("#zoom-in")
    before = page.eval_on_selector('.panel[data-role="proposed"] .canvas', "e => e.style.transform")
    assert before == page.eval_on_selector('.panel[data-role="baseline"] .canvas', "e => e.style.transform")

    write(store / "proposed" / "flow.mmd", PROPOSED + '    notify --> audit["Audit"]\n')
    page.wait_for_selector('.panel[data-role="proposed"] .canvas g.node[id*="-audit-"]', timeout=10000)
    assert page.eval_on_selector('.panel[data-role="proposed"] .canvas', "e => e.style.transform") == before
    assert page.evaluate("window.__marker") == 1


def test_routing_and_views(page):
    page.goto(page.base + f"?view=proposed#{page.key}/flow")
    page.wait_for_selector('.panel[data-role="proposed"] .canvas svg', timeout=30000)
    assert not page.is_visible('.panel[data-role="baseline"]')
    page.click('button[data-view="side"]')
    assert page.is_visible('.panel[data-role="baseline"]')
    assert "view=side" in page.url
    page.click(f'a.item[href="#{page.key}/broken"]')
    page.wait_for_selector('#diagram-title:text("broken")')
    assert page.url.endswith(f"#{page.key}/broken")
    page.fill("#filter", "flo")
    assert page.eval_on_selector_all("a.item", "els => els.map(e => e.title)") == ["flow"]


@pytest.fixture
def plan_served(sample_dir, tmp_path):
    from conftest import git_commit

    from mekhen.core.plans import set_active_plan

    git_commit(sample_dir)
    plan = write(tmp_path / "plan.md", "# Add a cache\n\nEdit `core/db.py` and add `src/sample/core/cache.py`.\n")
    set_active_plan(sample_dir, plan, "cli")
    registry = Registry()
    registry.add(make_project(sample_dir))
    registry.scan()
    srv = MekhenServer(registry, port=0)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    start_watcher(srv, interval=0.1)
    yield srv, plan
    srv.stopping.set()
    srv.shutdown()
    srv.server_close()


def test_follow_opens_the_plan_in_the_unified_view(browser, plan_served):
    srv, plan = plan_served
    context = browser.new_context(viewport={"width": 1400, "height": 900})
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"http://127.0.0.1:{srv.server_port}/")  # no hash: follow picks the active plan
    page.wait_for_selector('.panel[data-role="unified"] .canvas svg', timeout=30000)
    assert page.is_visible('.panel[data-role="unified"]') and not page.is_visible('.panel[data-role="baseline"]')
    assert "Add a cache" in page.inner_text("#diagram-title")
    nodes = classes_by_node(page, "unified")
    assert "mekhenAdded" in nodes["n_sample__core__cache"]
    assert "mekhenTouched" in nodes["n_sample__core__db"]
    assert "mekhen" not in nodes["n_sample__api__routes"]
    assert "edited" in page.inner_text("#summary-body")

    page.wait_for_selector("#conn[data-state='live']")
    write(plan, "# Add a cache\n\nEdit `core/db.py` and `core/models.py`, add `src/sample/core/cache.py`.\n")
    page.wait_for_function(
        """() => [...document.querySelectorAll('.panel[data-role="unified"] g.node')]
                .some(n => n.id.includes('n_sample__core__models') && n.classList.contains('mekhenTouched'))""",
        timeout=10000)
    context.close()
    assert errors == []


def test_plan_progress_ticks_live(browser, plan_served, sample_dir):
    srv, plan = plan_served
    context = browser.new_context(viewport={"width": 1400, "height": 900})
    page = context.new_page()
    page.goto(f"http://127.0.0.1:{srv.server_port}/")
    page.wait_for_selector(".progress-title", timeout=30000)
    assert page.inner_text(".progress-title") == "Progress: 0 of 2 done"
    assert page.inner_text("a.item[aria-current='true'] .counts") == "0/2"
    page.wait_for_selector("#conn[data-state='live']")
    write(sample_dir / "src" / "sample" / "core" / "db.py", "x = 1\n")
    page.wait_for_selector('.progress-title:text("1 of 2 done")', timeout=15000)
    assert "✓" in page.inner_text(".progress li.done")
    context.close()
