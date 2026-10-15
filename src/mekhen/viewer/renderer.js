// The renderer seam: the only place that decides how a diagram document becomes a picture.
//
// A renderer gets the diagram document (GET /api/projects/KEY/diagrams/ID) and a panel role
// ("baseline", "proposed" or "unified") and resolves to {svg} or {error}; it never rejects.
// The Mermaid renderer draws the Mermaid text the server derives from the graph. A graph-native
// renderer (Cytoscape + ELK, for stable layouts on large graphs) would read `detail.graph`, the
// versioned graph contract, instead; nothing outside this file would change.

import { render } from "./render.js";

export const mermaidRenderer = {
  name: "mermaid",

  // A string that changes whenever the picture would: lets the viewer skip needless re-renders.
  key(detail, role, highlight) {
    const side = detail && detail[role];
    return side ? (highlight ? side.render : side.text) : null;
  },

  draw(detail, role, highlight) {
    return render(this.key(detail, role, highlight));
  },
};

export function createRenderer() {
  return mermaidRenderer;
}
