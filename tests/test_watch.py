import os

from conftest import make_project, write

from mekhen.core.gen import parser_for
from mekhen.core.watch import Watcher


def settle(watcher):
    """Two polls: the first sees a change, the second confirms it is stable and reports it."""
    first = watcher.poll()
    return first if first else watcher.poll()


def bump(path):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def test_reports_changed_added_removed(project_dir):
    project = make_project(project_dir)
    project.new("flow")
    watcher = Watcher(project)
    assert not watcher.poll()

    path = project.store / "proposed" / "flow.mmd"
    write(path, "flowchart TD\n a --> b\n")
    bump(path)
    assert settle(watcher).to_json() == {"changed": ["flow"], "added": [], "removed": []}

    write(project.store / "baseline" / "other.mmd", "flowchart TD\n a\n")
    assert settle(watcher).to_json() == {"changed": [], "added": ["other"], "removed": []}

    (project.store / "baseline" / "other.mmd").unlink()
    assert settle(watcher).to_json() == {"changed": [], "added": [], "removed": ["other"]}
    assert not watcher.poll()


def test_waits_for_writes_to_settle(project_dir):
    project = make_project(project_dir)
    project.new("flow")
    watcher = Watcher(project)
    path = project.store / "proposed" / "flow.mmd"
    write(path, "flowchart TD\n a\n")
    bump(path)
    assert not watcher.poll()  # changed since last poll: not reported yet
    write(path, "flowchart TD\n a --> b\n")
    bump(path)
    assert not watcher.poll()  # still changing
    assert watcher.poll().changed == ["flow"]


def test_code_changes_mark_generated_baselines(sample_dir):
    project = make_project(sample_dir)
    project.generate("imports", parser_for("imports").parse_args([]))
    watcher = Watcher(project, fingerprint_every=1)
    assert not watcher.poll()
    write(sample_dir / "src" / "sample" / "api" / "extra.py", "import sample\n")
    assert settle(watcher).changed == ["imports"]
