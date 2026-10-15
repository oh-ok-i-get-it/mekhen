// Mermaid wrapper: renders one diagram at a time and turns every failure into an error message,
// so a panel is never left blank.

let api = null;
let counter = 0;
let queue = Promise.resolve();

export function initMermaid(mermaid, dark) {
  api = mermaid;
  mermaid.initialize({
    startOnLoad: false,
    securityLevel: "strict", // diagrams may come from other people's repos: no click handlers, sanitised labels
    theme: dark ? "dark" : "default",
    maxTextSize: 500000,
    flowchart: { useMaxWidth: false },
    sequence: { useMaxWidth: false },
    class: { useMaxWidth: false },
    state: { useMaxWidth: false },
    er: { useMaxWidth: false },
  });
}

function message(error) {
  if (!error) return "Unknown error";
  if (typeof error === "string") return error;
  return String(error.message || error.str || error);
}

async function renderNow(text) {
  const id = `mekhen-${++counter}`;
  try {
    await api.parse(text);
  } catch (error) {
    return { error: message(error) };
  }
  try {
    const { svg } = await api.render(id, text);
    return { svg };
  } catch (error) {
    return { error: message(error) };
  } finally {
    // Mermaid leaves its scratch element (and, on failure, an error graphic) in <body>.
    for (const stray of [document.getElementById(id), document.getElementById(`d${id}`)]) stray?.remove();
  }
}

// Resolves to {svg} or {error}; never rejects. Calls are serialised because Mermaid renders
// through shared global state.
export function render(text) {
  const job = queue.then(() => renderNow(text));
  queue = job.catch(() => {});
  return job;
}
