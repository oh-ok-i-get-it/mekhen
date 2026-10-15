from conftest import git_commit, git_init, needs_git, write

from mekhen.core.sources import GitRevision, WorkingTree, changed_paths, head_commit, in_git

pytestmark = needs_git


def test_worktree_without_git_skips_tool_folders(tmp_path):
    write(tmp_path / "a.py", "x = 1\n")
    write(tmp_path / "pkg" / "b.py", "")
    write(tmp_path / "node_modules" / "dep" / "c.js", "")
    wt = WorkingTree(tmp_path)
    assert wt.files() == ["a.py", "pkg/b.py"]
    assert wt.read(["a.py", "missing.py"]) == {"a.py": b"x = 1\n"}


def test_head_vs_worktree(tmp_path):
    write(tmp_path / "a.py", "one\n")
    write(tmp_path / "sub" / "b.py", "two\n")
    write(tmp_path / ".gitignore", "ignored.txt\n")
    git_commit(tmp_path)
    write(tmp_path / "a.py", "changed\n")
    write(tmp_path / "new.py", "new\n")
    write(tmp_path / "ignored.txt", "x\n")
    (tmp_path / "sub" / "b.py").unlink()

    assert in_git(tmp_path)
    head = GitRevision(tmp_path)
    assert head.commit == head_commit(tmp_path)
    assert head.files() == [".gitignore", "a.py", "sub/b.py"]
    assert head.read(["a.py", "sub/b.py", "nope.py"]) == {"a.py": b"one\n", "sub/b.py": b"two\n"}
    assert WorkingTree(tmp_path).files() == [".gitignore", "a.py", "new.py"]  # ignored and deleted files left out
    assert changed_paths(tmp_path) == {"a.py", "new.py", "sub/b.py"}


def test_project_in_a_subfolder_of_the_repo(tmp_path):
    write(tmp_path / "other" / "x.py", "")
    write(tmp_path / "proj" / "pkg" / "m.py", "hi\n")
    git_commit(tmp_path)
    write(tmp_path / "proj" / "pkg" / "m.py", "edited\n")
    root = tmp_path / "proj"
    head = GitRevision(root)
    assert head.files() == ["pkg/m.py"]
    assert head.read(["pkg/m.py"]) == {"pkg/m.py": b"hi\n"}
    assert changed_paths(root) == {"pkg/m.py"}


def test_export(tmp_path):
    write(tmp_path / "pkg" / "m.py", "hi\n")
    git_commit(tmp_path)
    out = GitRevision(tmp_path).export(tmp_path / "export")
    assert (out / "pkg" / "m.py").read_text() == "hi\n"


def test_no_commits_yet(tmp_path):
    write(tmp_path / "a.py", "")
    git_init(tmp_path)
    assert in_git(tmp_path)
    assert head_commit(tmp_path) is None
