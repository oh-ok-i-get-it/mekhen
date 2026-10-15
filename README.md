# mekhen

View and compare a project's Mermaid architecture diagrams in one live-updating window: baseline
vs proposed, side by side, with changes highlighted. A small CLI that works in any repo.

- **One window** lists every diagram in the project, with a filter box. It never opens a window
  per diagram.
- **Baseline vs proposed** are shown side by side, with pan and zoom kept in sync. Added nodes and
  edges are green, removed ones red, changed ones amber, and a list of the changes appears below.
- **Live:** saving a file re-renders it in place, keeping scroll and zoom, without reloading the
  page.
- **Baselines generated from code** (`mekhen gen imports`), so "current" diagrams can't drift
  from reality. `mekhen check` flags any that have.
- **Nothing added to your project** but the diagram files, which are kept out of git by default.
  No `package.json`, no config needed.

## Install

Once per machine (Python 3.11 or newer, standard library only):

```
pipx install git+https://github.com/oh-ok-i-get-it/mekhen
# or
uv tool install git+https://github.com/oh-ok-i-get-it/mekhen
```

## Use

In any project:

```
mekhen new pipeline          # creates .diagrams/baseline/pipeline.mmd and .diagrams/proposed/pipeline.mmd
mekhen gen imports           # generates .diagrams/baseline/imports.mmd from the Python code
mekhen new imports           # adds a proposal that starts as a copy of the generated baseline
mekhen serve                 # opens the viewer (or reuses the one already running)
```

| Command | What it does |
|---|---|
| `mekhen serve [PATH] [--port N] [--no-open] [--gen] [--offline]` | Local viewer on 127.0.0.1. `--no-open` prints the URL instead, for VS Code's Simple Browser (Ctrl+Shift+P, then "Simple Browser: Show"). `--gen` refreshes generated baselines first. |
| `mekhen list [PATH] [--json]` | Each diagram with its status (`both`, `baseline-only`, `proposed-only`, `generated`, `stale`, `lint-error`) and its change counts. |
| `mekhen show NAME [--json]` | One diagram's changes and issues. `--json` includes both texts and the highlighted render copies. |
| `mekhen new NAME` | Creates whichever of the baseline and proposal is missing. |
| `mekhen check [PATH] [--warnings-as-errors] [--json]` | Lints every diagram, and checks that generated baselines are current. Exits 1 on errors. |
| `mekhen gen [imports] [--depth N] [--package P] [--exclude PAT] [--include-external] [--name N]` | Generates baselines from code. With no kind, it refreshes every generated baseline using the arguments recorded in each file's first line. |
| `mekhen conventions [--json]` | Prints the authoring rules. Tell an AI session to run it before writing diagrams. |
| `mekhen mermaid info\|download` | Shows the pinned Mermaid version, or downloads it for offline use (`serve --offline`). |

Viewer keys: `/` filter, `j`/`k` or arrow keys to move between diagrams, `1` `2` `3` to switch
view, `f` fit, `0` actual size, `+`/`-` zoom, `h` highlighting, `s` sync. Double-click a panel to
fit it.

## Where diagrams live

By default they go in `<project>/.diagrams/`, which holds its own `.gitignore` (containing `*`).
Git ignores the folder without any change to your project's ignore files, so there is never
anything to commit.

To keep nothing at all in the project, use the home store, `~/.mekhen/projects/<name>-<hash>/`:

```
mekhen serve --store home          # one command
MEKHEN_STORE=home                  # this shell
# ~/.mekhen/config.toml            # every project
store = "home"
```

(Claude Code asks for permission before writing outside the project folder, so the default
in-repo store is smoother for AI sessions.)

The first match wins: `--store`/`--dir` flags, then `MEKHEN_STORE`, then project config, then
`~/.mekhen/config.toml`, then the default. If you do want diagrams committed, set an explicit
folder in project config. It is not auto-ignored, and `mekhen check` can then run in CI:

```toml
[tool.mekhen]            # in pyproject.toml, or the same keys at the top level of mekhen.toml
dir = "docs/diagrams"

[tool.mekhen.gen.imports]
packages = ["mypkg"]     # default: auto-detect packages in src/ or the project root
depth = 2                # collapse module names to their first 2 parts
exclude = ["migrations"] # tests, test and conftest are always excluded
include_external = false # third-party packages; the standard library is never shown
direction = "LR"
```

## Conventions

`mekhen conventions` prints these rules, and `mekhen check` enforces them:

1. One diagram per file: `baseline/NAME.mmd` (current) and `proposed/NAME.mmd` (proposed).
2. **Keep node ids stable** between baseline and proposed. The diff matches nodes by id, so
   rename a box by changing its label, not its id.
3. Put every node label in double quotes, `a["Parse input"]`, and use `<br/>` for line breaks.
4. No `#` and no double quotes inside a label. No `→`, `≠`, `⊆` or similar symbols: use "to",
   "not", "subset of".
5. No node id named `end`.
6. A hand-written baseline starts with `%% mekhen:hand-written`. Generated ones start with
   `%% mekhen:generated ...` and are never edited by hand.

## How the diff works

Flowcharts (`flowchart` / `graph`) are compared as follows:

- **Nodes and subgraphs** are matched by id. Same id with a different label, shape or parent
  subgraph counts as *changed*.
- **Edges** are matched by (source, target, n-th occurrence). A different label or arrow type
  counts as *changed*.
- **Highlighting** is applied to a copy of the text (`classDef`, `class` and `linkStyle` lines are
  appended). Your files are never modified.

What it can't detect:
- **Renamed ids:** they show as one removed and one added node. When the labels match, the change
  list says "possibly renamed".
- **Style-only changes:** ignored.
- **Diagrams with their own `style` lines:** these can override the highlight colours.
- **Other diagram types:** shown side by side without highlighting.

## Security

- **Network:** the server binds to 127.0.0.1 only and rejects requests whose `Host` header isn't
  `127.0.0.1:<port>` or `localhost:<port>`.
- **Read-only:** it is GET-only and sends no CORS headers.
- **No file paths from the browser:** diagrams are looked up by id among the discovered files,
  and viewer assets come from a fixed list, so a request can never name a file path.
- **Rendering:** Mermaid runs with `securityLevel: "strict"`, which disables click handlers and
  sanitises labels.
- **Generators:** `gen imports` reads code with `ast` and never imports or runs it.

## Mermaid version

The viewer loads exactly Mermaid **11.16.1** from jsDelivr, with an SRI hash. `mekhen mermaid
download` stores a verified copy in your user cache for offline use. To move to a new version, run
`python scripts/update_mermaid.py X.Y.Z`, which rewrites `src/mekhen/mermaid_pin.py`. Then run the
browser tests and bump mekhen's minor version. Prefer releases at least two weeks old.

## Development

```
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"      # Windows; .venv/bin/python elsewhere
.venv/Scripts/python -m pytest
```

Browser tests (`tests/e2e`) need `pip install -e ".[e2e]"`. On Windows they use the installed
Microsoft Edge. Elsewhere, run `python -m playwright install chromium` and set
`MEKHEN_E2E_BROWSER=`.

Layout: `src/mekhen/core/` holds the host-independent logic (discovery, parsing, diff, lint,
generators, and the JSON documents). `src/mekhen/cli/` holds the command and the server.
`src/mekhen/viewer/` is the static web app. The viewer talks to its host only through
`viewer/host.js` (project, list, diagram and change events). The server's API returns the same
JSON as `mekhen list --json` and `mekhen show NAME --json`, so a VS Code webview could reuse the
viewer by relaying those commands.
