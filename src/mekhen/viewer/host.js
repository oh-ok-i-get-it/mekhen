// The viewer's only link to the outside world. Everything else in the viewer is host-agnostic.
//
// A host provides:
//   getProjects()          -> Promise<{projects: [...], follow: {project, time} | null}>   (GET /api/projects)
//   getProject(key)        -> Promise<project JSON | null>                                (GET /api/projects/KEY)
//   listDiagrams(key)      -> Promise<list JSON | null>     (= `mekhen list --json`)      (GET /api/projects/KEY/diagrams)
//   getDiagram(key, id)    -> Promise<diagram JSON | null>  (= `mekhen show ID --json`)   (GET /api/projects/KEY/diagrams/ID)
//   subscribe(cb)          -> unsubscribe function; cb receives
//                             {type: "diagrams", project, changed, added, removed} | {type: "projects", projects, follow}
//                             | {type: "connected"} | {type: "disconnected"}
//
// HttpHost talks to `mekhen serve`. Another host (an editor webview, say) would relay the same JSON
// over its own channel; nothing else needs to change.

async function getJSON(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
  return response.json();
}

const enc = encodeURIComponent;

export class HttpHost {
  getProjects() {
    return getJSON("/api/projects");
  }

  getProject(key) {
    return getJSON(`/api/projects/${enc(key)}`);
  }

  listDiagrams(key) {
    return getJSON(`/api/projects/${enc(key)}/diagrams`);
  }

  getDiagram(key, id) {
    return getJSON(`/api/projects/${enc(key)}/diagrams/${enc(id)}`);
  }

  subscribe(callback) {
    const source = new EventSource("/api/events");
    source.addEventListener("open", () => callback({ type: "connected" }));
    source.addEventListener("error", () => callback({ type: "disconnected" }));
    for (const type of ["diagrams", "projects"]) {
      source.addEventListener(type, (event) => callback({ type, ...JSON.parse(event.data) }));
    }
    return () => source.close();
  }
}

export function createHost() {
  return new HttpHost();
}
