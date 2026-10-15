# mekhen

See what a plan changes before you build it. mekhen draws your project as a diagram, generated
from the code itself, and highlights the parts a plan touches, adds, moves or removes. The window
updates live as the plan or the code changes.

- **Works with any plan.** A plan is any Markdown file. mekhen reads the file paths it mentions,
  with no special format needed.
- **Always accurate.** The "before" picture comes from your code at the last commit. Nobody keeps
  it up to date by hand.
- **Planned vs. built.** One view shows the plan; another shows what has actually changed since
  the last commit.
- **Nothing added to your repo.** All state lives in `~/.mekhen`.
- **Optional Claude Code plugin.** With it, plans written in plan mode appear in the viewer
  automatically.

Python 3.11+, standard library only.

## Install

```
pipx install git+https://github.com/oh-ok-i-get-it/mekhen
# or
uv tool install git+https://github.com/oh-ok-i-get-it/mekhen
```

## Use

In any project folder:

```
mekhen serve                      # open the viewer
mekhen plan path/to/plan.md       # show a plan in it (starts the viewer if needed)
```

The viewer is one browser tab for all your projects. It shows:

| View | What it shows |
|---|---|
| **Plan** | The project at its last commit, with the active plan applied. Files the plan names are amber (edited) or green (new). A **progress** checklist shows which planned changes are already in your working tree, and what changed that the plan didn't mention. |
| **Changes** | The last commit against your working tree: what you have actually changed so far. |
| Hand-written pairs | Your own `baseline/NAME.mmd` vs `proposed/NAME.mmd` diagrams ([below](#hand-written-diagrams)). |

The viewer **follows the active plan**: when a plan is shown or changes, in any project, the tab
switches to it. Turn this off with the checkbox (or `p`). For VS Code's Simple Browser, run
`mekhen serve --no-open` and open the printed URL (Ctrl+Shift+P, "Simple Browser: Show").

### How a plan is read

You don't have to write a plan in any special way. mekhen marks every file the plan mentions,
however it's written: `src/app/db.py`, `app/db.py` (a shortened path is fine if it matches only
one file), `[db](src/app/db.py#L10)`, `` `app.db` `` for Python modules, and
`core/{cache,store}.py`.

When a plan also changes the *structure* (new modules, moves, new or removed dependencies), it can
say so in a small fenced block. Only the changes go in it, never the whole diagram:

````
```mekhen
+ src/app/cache.py "Cache"            new module
- src/app/legacy.py                   removed module
src/app/old.py => src/app/new.py      moved or renamed (keeps its dependencies)
+ src/app/db.py -> src/app/cache.py   new dependency
- src/app/api.py -> src/app/db.py     removed dependency
```
````

`mekhen plan FILE` reports any line it can't apply, with its line number, and exits 1.

## Use with Claude Code

The plugin connects Claude Code's plan mode to mekhen. Install it once (Claude Code 2.1.275 or
later):

```
/plugin install mekhen --marketplace oh-ok-i-get-it/mekhen
```

or, in your shell, `claude plugin marketplace add oh-ok-i-get-it/mekhen` followed by
`claude plugin install mekhen@mekhen`. Then start a new session.

From then on, whenever Claude writes or edits a plan in plan mode:

- **The viewer opens on the plan**, once per session, and updates as Claude revises it.
- **Overlay mistakes go back to Claude.** If Claude writes a `mekhen` block that doesn't apply,
  it is told the line and the reason, and fixes it.
- **Claude gets a short hint**, once per session and only in plan mode: name files by their repo
  paths, and add a `mekhen` block only when the structure changes. That is all the plugin adds to
  Claude's context. Claude never reads or writes a whole diagram.

The plugin bundles its own copy of mekhen, so it works without the `pipx` install. Installing the
CLI as well just adds `mekhen` to your shell. The plugin installs at user scope and writes nothing
into your projects.

Requirements: Python 3.11+ on your PATH (as `python3`, `python` or `py`). On Windows, hooks need
Git Bash. Without Git Bash, Claude Code runs hooks in PowerShell, which the plugin doesn't support
yet; use `mekhen plan FILE` there instead.

To turn the plugin off, use `/plugin`. To remove it, run `claude plugin uninstall mekhen@mekhen`.

## Languages

A **provider** turns a project's files into the graph. mekhen picks one automatically:

| Provider | Graph | Chosen when |
|---|---|---|
| `python` | Modules and their imports, grouped by package. Files are read with `ast` and never run. | The project has Python packages |
| `files` | Folders and files, no dependency edges | Anything else |
| `command` | Whatever your program prints, in [graph JSON](docs/graph-schema.md) | You set it up (below) |

The `command` provider brings in any other language. Wrap a dependency tool (madge,
dependency-cruiser, `go list -json`, ...) in a small script that prints the graph JSON. For
safety, the command can only come from your own `~/.mekhen/config.toml`, never from a file inside
a repository, so opening a repo can't make mekhen run its code.

Large graphs stay readable: when a graph has more than `max_nodes` nodes, folders with nothing
marked inside them collapse into a single box.

## Configuration

None is needed. Everything is optional, in `~/.mekhen/config.toml`:

```toml
max_nodes = 60          # collapse unmarked groups above this many nodes
open_on_plan = true     # `mekhen plan` and the plugin open the viewer
idle_minutes = 30       # a viewer started in the background stops after this long unused

[providers.python]
packages = ["mypkg"]    # default: auto-detect packages in src/ or the project root
depth = 2               # collapse module names to their first 2 parts
exclude = ["migrations"]

[projects."~/src/web-app"]          # settings for one project (a folder path or a glob)
provider = "command"
providers.command = { command = "node tools/graph.js", inputs = ["src/*"] }
```

A project can also carry its own settings in `[tool.mekhen]` in `pyproject.toml` or in a
`mekhen.toml` (the same keys, except a `command`). You don't have to add one.

## Hand-written diagrams

mekhen also compares diagrams you draw yourself: `baseline/NAME.mmd` (current) against
`proposed/NAME.mmd` (proposed), side by side. Added, removed and changed nodes and edges are
highlighted. These live in `~/.mekhen/projects/<name>-<hash>/` by default, or in a folder you
choose:

```
mekhen new pipeline       # creates both files
mekhen gen imports        # a Python import-graph baseline, written as a file
mekhen check              # lint every diagram; checks generated ones are current (exit 1 on errors)
```

To keep them in the repo, use `--store repo` (`<project>/.diagrams/`, which git ignores) or set
`dir = "docs/diagrams"` in project config to commit them and run `mekhen check` in CI.
`mekhen conventions` prints the authoring rules. The main one: keep node ids stable between
baseline and proposed, because the diff matches nodes by id.

## Commands

| Command | What it does |
|---|---|
| `mekhen serve [PATH] [--plan FILE] [--no-open] [--port N] [--offline]` | Open the viewer (one per user; a second call reuses it) |
| `mekhen plan FILE [--project PATH] [--no-open] [--no-serve] [--json]` | Make FILE the project's active plan; report overlay problems |
| `mekhen plan --status [--json]` | Which of the active plan's changes are done so far |
| `mekhen plan --clear` | Stop showing the project's plan |
| `mekhen list` / `show NAME` / `new NAME` / `check` / `gen` / `conventions` | Hand-written and generated diagram files |
| `mekhen mermaid info\|download` | The pinned Mermaid version; download it for `serve --offline` |
| `mekhen hook EVENT` | Entry point for editor and agent integrations (the Claude Code plugin uses it) |

Viewer keys: `/` filter, `j`/`k` next/previous, `1` unified, `2` side by side, `3` baseline,
`4` proposed, `f` fit, `0` actual size, `+`/`-` zoom, `h` highlighting, `s` sync, `p` follow.

## Security

- The server listens on 127.0.0.1 only and rejects other `Host` headers. It is read-only (GET
  only, no CORS). Requests name projects and diagrams by id, never by file path.
- Mermaid runs with `securityLevel: "strict"`.
- Providers read code (`ast`, `git cat-file`) and never import or run it. The one exception is a
  `command` provider you configure yourself.
- Integrations hand plans to the server through files in `~/.mekhen/state/`, so the server never
  accepts writes.

## Mermaid version

The viewer loads exactly Mermaid **11.16.1** from jsDelivr, with an SRI hash.
`mekhen mermaid download` stores a verified copy for offline use. To move to a new version, run
`python scripts/update_mermaid.py X.Y.Z`, then the browser tests.

## Development

```
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"      # Windows; .venv/bin/python elsewhere
.venv/Scripts/python -m pytest
```

Browser tests (`tests/e2e`) need `pip install -e ".[e2e]"`; on Windows they use Microsoft Edge,
elsewhere run `python -m playwright install chromium` and set `MEKHEN_E2E_BROWSER=`. To try the
plugin from a clone: `claude --plugin-dir .`.

Layout:
- `src/mekhen/core/`: everything that doesn't depend on a host or a tool. It holds the graph
  contract (`graph.py`), providers, sources (HEAD and working tree), plans, views, and the
  hand-written diagram pipeline.
- `src/mekhen/cli/`: the commands, the server, and `hook.py`, the only code that knows Claude
  Code's hook format.
- `src/mekhen/viewer/`: the web app. `host.js` is its only link to the server, and `renderer.js`
  is the only place that turns a diagram into a picture.
- `.claude-plugin/` and `hooks/`: the Claude Code plugin.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
