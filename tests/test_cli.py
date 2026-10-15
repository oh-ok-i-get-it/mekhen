import json

import pytest
from conftest import write

from mekhen.cli.main import main


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
