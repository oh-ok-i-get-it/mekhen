# Changelog

## 0.1.0 (unreleased)

First version.

- `mekhen serve`: one browser window for all of a project's diagrams. Side-by-side, baseline-only
  and proposed-only views, synced pan and zoom, live re-render on save (Server-Sent Events, no
  page reload), and Mermaid errors shown inside the panel. A second `serve` reuses the running
  server.
- Change highlighting for flowcharts: added (green), removed (red), changed (amber) nodes, edges
  and subgraphs, plus a change list with rename hints.
- `mekhen list`, `show`, `new`, `check`, `conventions`, `gen imports`, `mermaid info|download`.
- Diagrams live in `<project>/.diagrams/` (git-ignored by its own `.gitignore`), or with
  `--store home` in `~/.mekhen/projects/`.
- Mermaid pinned to 11.16.1.
