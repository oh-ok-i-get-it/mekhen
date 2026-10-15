import json

import pytest
from conftest import write

from mekhen.cli.main import main

pytestmark = pytest.mark.usefixtures("repo_store")  # these tests look inside <project>/.diagrams/


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "mekhen" in capsys.readouterr().out


def test_new_list_show_check(capsys, project_dir):
    p = str(project_dir)
    code, out, _ = run(capsys, "new", "flow", "--project", p)
    assert code == 0 and "created .diagrams/baseline/flow.mmd" in out
    code, _, err = run(capsys, "new", "flow", "--project", p)
    assert code == 2 and "already has both" in err

    write(project_dir / ".diagrams" / "proposed" / "flow.mmd",
          'flowchart TD\n    start["Start"] --> finish["Finish"]\n    finish --> extra["Extra"]\n')
    code, out, _ = run(capsys, "list", p)
    assert code == 0 and "flow" in out and "+2 -0 ~0" in out

    code, out, _ = run(capsys, "list", p, "--json")
    assert json.loads(out)["diagrams"][0]["changes"]["added"] == 2

    code, out, _ = run(capsys, "show", "flow", "--project", p)
    assert "+ node extra" in out and "+ edge finish --> extra" in out

    data = json.loads(run(capsys, "show", "flow", "--project", p, "--json")[1])
    assert "mekhenAdded" in data["proposed"]["render"] and "mekhenAdded" not in data["proposed"]["text"]

    code, out, _ = run(capsys, "check", p)
    assert code == 0 and "0 error(s)" in out


def test_new_copies_existing_baseline(capsys, project_dir):
    write(project_dir / ".diagrams" / "baseline" / "imports.mmd",
          "%% mekhen:generated imports, do not edit, run: mekhen gen imports\nflowchart LR\n    a --> b\n")
    run(capsys, "new", "imports", "--project", str(project_dir))
    text = (project_dir / ".diagrams" / "proposed" / "imports.mmd").read_text(encoding="utf-8")
    assert "mekhen:generated" not in text and "a --> b" in text


def test_new_rejects_bad_names(capsys, project_dir):
    code, _, err = run(capsys, "new", "../escape", "--project", str(project_dir))
    assert code == 2 and "invalid name" in err


def test_check_exit_codes(capsys, project_dir):
    store = project_dir / ".diagrams"
    write(store / "proposed" / "warn.mmd", "flowchart TD\n a[unquoted]\n")
    p = str(project_dir)
    assert run(capsys, "check", p)[0] == 0
    assert run(capsys, "check", p, "--warnings-as-errors")[0] == 1
    write(store / "proposed" / "bad.mmd", "flowchart TD\n a --> end\n")
    code, out, _ = run(capsys, "check", p)
    assert code == 1 and "bad.mmd:2: error [no-end-id]" in out
    data = json.loads(run(capsys, "check", p, "--json")[1])
    assert data["errors"] >= 1 and data["issues"][0]["path"].startswith(".diagrams/")


def test_gen_and_store_flags(capsys, sample_dir):
    code, out, _ = run(capsys, "gen", "imports", "--depth", "2", "--project", str(sample_dir))
    assert code == 0 and "wrote .diagrams/baseline/imports.mmd" in out
    code, out, _ = run(capsys, "gen", "--project", str(sample_dir))
    assert "up to date" in out
    code, out, _ = run(capsys, "gen", "imports", "--store", "home", "--project", str(sample_dir))
    assert code == 0 and "projects" in out


def test_conventions(capsys, project_dir):
    code, out, _ = run(capsys, "conventions", "--project", str(project_dir))
    assert code == 0 and "No node id named end" in out and "Store for this project: .diagrams/" in out
    data = json.loads(run(capsys, "conventions", "--json")[1])
    assert any(r["id"] == "quote-labels" for r in data["rules"])


def test_show_unknown(capsys, project_dir):
    code, _, err = run(capsys, "show", "nope", "--project", str(project_dir))
    assert code == 1 and "no diagram named" in err


def test_plan_command(capsys, sample_dir, tmp_path):
    from mekhen.core.plans import active_plan

    plan = write(tmp_path / "plan.md", "# Cache\n\nEdit `core/db.py`, add `src/sample/core/cache.py`.\n")
    code, out, err = run(capsys, "plan", str(plan), "--project", str(sample_dir), "--no-serve")
    assert code == 0, err
    assert "plan: Cache" in out and "1 added" in out and "1 touched" in out
    assert active_plan(sample_dir).path == plan.resolve()

    write(plan, "# Cache\n\n```mekhen\n- src/sample/nope.py\nwhat is this\n```\n")
    code, out, err = run(capsys, "plan", str(plan), "--project", str(sample_dir), "--no-serve", "--json")
    data = json.loads(out)
    assert code == 1 and [i["line"] for i in data["issues"]] == [4, 5]

    code, out, _ = run(capsys, "plan", "--clear", "--project", str(sample_dir))
    assert code == 0 and active_plan(sample_dir) is None
    code, _, err = run(capsys, "plan", str(tmp_path / "missing.md"), "--project", str(sample_dir), "--no-serve")
    assert code == 2 and "no such plan file" in err


def test_core_knows_nothing_about_claude():
    """The core works with any plan from any tool; integrations live in mekhen.cli."""
    from pathlib import Path

    import mekhen.core

    core = Path(mekhen.core.__file__).parent
    for path in core.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "permission_mode" not in text, path
        assert "mekhen.cli" not in text, path
        assert "claude" not in text.lower(), path


def test_plan_status(capsys, sample_dir, tmp_path):
    from conftest import git_commit

    from mekhen.core.plans import active_plan

    git_commit(sample_dir)
    plan = write(tmp_path / "plan.md", "# Cache\n\nEdit `core/db.py` and `core/models.py`.\n")
    assert run(capsys, "plan", str(plan), "--project", str(sample_dir), "--no-serve")[0] == 0
    write(sample_dir / "src" / "sample" / "core" / "db.py", "x = 1\n")
    code, out, _ = run(capsys, "plan", "--status", "--project", str(sample_dir))
    assert code == 0 and "1 of 2 done" in out and "✓ sample.core.db" in out and "○ sample.core.models" in out
    data = json.loads(run(capsys, "plan", "--status", "--json", "--project", str(sample_dir))[1])
    assert (data["progress"]["done"], data["progress"]["total"]) == (1, 2)
    assert active_plan(sample_dir).path == plan.resolve()  # --status doesn't change the active plan

    run(capsys, "plan", "--clear", "--project", str(sample_dir))
    code, _, err = run(capsys, "plan", "--status", "--project", str(sample_dir))
    assert code == 1 and "no active plan" in err
