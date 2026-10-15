// The viewer's only link to the outside world. Everything else in the viewer is host-agnostic.
//
// A host provides:
//   getProject()      -> Promise<project JSON>          (GET /api/project)
//   listDiagrams()    -> Promise<list JSON>             (GET /api/diagrams    = `mekhen list --json`)
//   getDiagram(id)    -> Promise<diagram JSON | null>   (GET /api/diagrams/id = `mekhen show ID --json`)
//   subscribe(cb)     -> unsubscribe function; cb receives
//                        {type: "diagrams", changed, added, removed} | {type: "connected"} | {type: "disconnected"}
//
// HttpHost talks to `mekhen serve`. A VS Code webview would add a host that relays the same JSON
// over postMessage from `mekhen list/show/watch --json`; nothing else needs to change.

async function getJSON(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

export class HttpHost {
  getProject() {
    return getJSON("/api/project");
  }

  listDiagrams() {
    return getJSON("/api/diagrams");
  }

  getDiagram(id) {
    return getJSON(`/api/diagrams/${encodeURIComponent(id)}`);
  }

  subscribe(callback) {
    const source = new EventSource("/api/events");
    source.addEventListener("open", () => callback({ type: "connected" }));
    source.addEventListener("error", () => callback({ type: "disconnected" }));
    source.addEventListener("diagrams", (event) => {
      callback({ type: "diagrams", ...JSON.parse(event.data) });
    });
    return () => source.close();
  }
}

export function createHost() {
  return new HttpHost();
}
