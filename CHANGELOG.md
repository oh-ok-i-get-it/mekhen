# Changelog

## Unreleased

- **Plan progress.** The Plan view shows a live checklist of the plan's changes: done (already
  in the working tree) or still to do, plus changes the plan didn't mention. The sidebar shows
  `done/total`, and `mekhen plan --status` prints the same in the terminal.

## 0.2.0 (unreleased)

mekhen now shows what a plan changes, using the project's own code as the baseline.

- **Plan view.** `mekhen plan FILE` makes any Markdown file the project's active plan. The viewer
  draws the project at its last commit and marks the files the plan names as edited (amber) or
  new (green). An optional ```` ```mekhen ```` block adds structural changes: new, removed and
  moved modules, and new or removed dependencies.
- **Changes view.** The last commit against the working tree.
- **Claude Code plugin.** Install it from this repository's marketplace. Plans written in plan
  mode appear in the viewer automatically, and overlay mistakes are sent back to Claude.
- **Graph contract.** A versioned JSON graph is now the core data
  ([docs/graph-schema.md](docs/graph-schema.md)), with three providers: `python` (ast imports),
  `files` (any language), and `command` (any program printing graph JSON, from user config only).
- **One viewer per user** for all projects, following the active plan. A viewer started in the
  background stops after an idle period.
- **Unified view** (one layout, changes marked) is the default for code-built views. Unmarked
  edges are faded, and large graphs collapse untouched folders.
- **Nothing in the repo by default.** Diagram files now default to `~/.mekhen/projects/`. Use
  `--store repo` for the old `.diagrams/` folder.
- `mekhen gen` with no kind no longer creates an import graph when there is none.
- The server's API is now per project: `/api/projects/KEY/...`.

## 0.1.0

First version: the baseline vs proposed viewer for hand-written Mermaid diagrams, with change
highlighting, lint, `gen imports`, and live reload.
