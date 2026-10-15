# Graph JSON, schema 1

The graph is mekhen's core contract. Every provider produces one, and every renderer draws one.
The `command` provider reads it from a program's standard output. The server returns it as the
`graph` field of a diagram (`GET /api/projects/KEY/diagrams/@plan`), with a `state` on every
element.

## What a `command` provider prints

```json
{
  "schema": 1,
  "direction": "LR",
  "groups": [
    {"id": "src/", "label": "src/", "parent": null, "path": "src"},
    {"id": "src/db/", "label": "db/", "parent": "src/", "path": "src/db"}
  ],
  "nodes": [
    {"id": "src/db/pool.ts", "label": "pool", "group": "src/db/", "paths": ["src/db/pool.ts"]},
    {"id": "src/api.ts", "label": "api", "group": "src/", "path": "src/api.ts"}
  ],
  "edges": [
    {"src": "src/api.ts", "dst": "src/db/pool.ts", "weak": false, "label": null}
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `schema` | no (default 1) | Must be `1`. mekhen refuses a schema it doesn't know |
| `direction` | no | Layout direction: `LR`, `RL`, `TB`, `TD` or `BT` |
| `groups[].id` | yes | Unique among groups. Group and node ids are separate namespaces |
| `groups[].parent` | no | Id of the enclosing group |
| `groups[].path` | no | The folder the group stands for, relative to the project root. mekhen uses it to place new files a plan mentions |
| `nodes[].id` | yes | Unique among nodes |
| `nodes[].group` | no | Id of the group the node belongs to (it must exist) |
| `nodes[].paths` (or `path`) | no | The files the node stands for, relative to the project root, with `/`. This is how a plan's file mentions find the node |
| `edges[].src`, `edges[].dst` | yes | Node ids (they must exist). One edge per pair |
| `edges[].weak` | no | Drawn dotted, for example an import used only for type checking |

Ids are opaque to mekhen. Use whatever is natural for the language (file paths, module names) and
keep them **stable**: the diff matches nodes by id.

The program runs in the project folder with `MEKHEN_SOURCE=worktree`. For the baseline it runs in
a temporary export of the last commit, with `MEKHEN_SOURCE=git` and `MEKHEN_COMMIT=<sha>`. It
should finish within `timeout` seconds (default 120). Set `inputs` (globs) so that mekhen only
re-runs the program when those files change.

## What the server adds

In the server's responses, every group, node and edge also has a `state`:

| State | Meaning |
|---|---|
| `same` | unchanged |
| `added` | only in the proposal (a new module, or a new dependency) |
| `removed` | only in the baseline |
| `changed` | in both, with a different label or group |
| `touched` | unchanged in structure, but its file is edited (named by the plan, or modified in the working tree) |

Nodes can also carry a `note`, such as `moved from app.old` or `collapsed: 12 items` (a group
folded into one box because nothing inside it changed). The graph also has `hints`, `otherFiles`
(files a plan names that aren't in the graph) and `counts`.
