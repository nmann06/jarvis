import { h, clear, errText } from "./util.js";
import { get } from "./api.js";

const MAX_LINES = 2000;

export function mount(root) {
  let lines = [];
  let follow = true;
  let wrap = true;
  let filter = "";
  let es = null;
  let alive = true;
  let retryTimer = null;

  const pre = h("div", { class: "log wrap", tabindex: "0", role: "log", "aria-label": "Jarvis log" });
  const status = h("span", { class: "muted small", role: "status" });
  const search = h("input", { type: "search", id: "log-filter", placeholder: "Filter lines", autocomplete: "off", "aria-label": "Filter lines" });
  const followBtn = h("button", { type: "button", class: "btn", role: "switch" });
  const wrapBtn = h("button", { type: "button", class: "btn", role: "switch" });
  const count = h("select", { "aria-label": "Lines to load" }, ...[200, 500, 1000].map((n) => h("option", { value: String(n), text: `Last ${n}` })));
  root.append(h("h1", { text: "Logs" }),
    h("div", { class: "toolbar" }, search, count, followBtn, wrapBtn, status), pre);

  function lineEl(text) {
    const lower = text.toLowerCase();
    const cls = /\b(error|exception|traceback|critical|failed)\b/.test(lower) ? "err" : /\bwarn(ing)?\b/.test(lower) ? "warn" : "";
    return h("div", { class: `ln ${cls}`, text });
  }

  function atBottom() { return pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40; }

  function redraw() {
    clear(pre);
    const f = filter.toLowerCase();
    const shown = f ? lines.filter((l) => l.toLowerCase().includes(f)) : lines;
    if (!shown.length) {
      pre.append(h("p", { class: "empty", text: lines.length ? "No lines match the filter." : "No log lines yet." }));
    } else {
      const frag = document.createDocumentFragment();
      for (const l of shown) frag.append(lineEl(l));
      pre.append(frag);
    }
    if (follow) pre.scrollTop = pre.scrollHeight;
  }

  function addLine(text) {
    lines.push(text);
    if (lines.length > MAX_LINES) lines.splice(0, lines.length - MAX_LINES);
    const f = filter.toLowerCase();
    if (f && !text.toLowerCase().includes(f)) return;
    const empty = pre.querySelector(".empty");
    if (empty) empty.remove();
    pre.append(lineEl(text));
    while (pre.childElementCount > MAX_LINES) pre.firstChild.remove();
    if (follow) pre.scrollTop = pre.scrollHeight;
  }

  function drawToggles() {
    followBtn.textContent = follow ? "Following new lines" : "Paused";
    followBtn.setAttribute("aria-checked", String(follow));
    followBtn.classList.toggle("on", follow);
    wrapBtn.textContent = wrap ? "Wrapping long lines" : "Long lines scroll";
    wrapBtn.setAttribute("aria-checked", String(wrap));
    wrapBtn.classList.toggle("on", wrap);
    pre.classList.toggle("wrap", wrap);
  }

  async function load() {
    try {
      const r = await get(`api/logs?lines=${count.value}`);
      lines = r.lines || [];
      status.textContent = "";
    } catch (e) {
      lines = [];
      status.textContent = `Couldn't load the log: ${errText(e)}`;
    }
    redraw();
  }

  function stream() {
    if (!alive) return;
    es = new EventSource("api/logs/stream");
    es.onopen = () => { if (!status.textContent.startsWith("Couldn")) status.textContent = "Live"; };
    es.onmessage = (m) => {
      try { const { line } = JSON.parse(m.data); if (typeof line === "string") addLine(line); } catch { /* ignore a bad frame */ }
    };
    es.onerror = () => {
      status.textContent = "Live stream dropped. Reconnecting…";
      if (es.readyState === EventSource.CLOSED) { es = null; retryTimer = setTimeout(stream, 3000); }
    };
  }

  followBtn.addEventListener("click", () => { follow = !follow; drawToggles(); if (follow) pre.scrollTop = pre.scrollHeight; });
  wrapBtn.addEventListener("click", () => { wrap = !wrap; drawToggles(); });
  search.addEventListener("input", () => { filter = search.value; redraw(); });
  count.addEventListener("change", load);
  pre.addEventListener("scroll", () => { if (follow && !atBottom()) { follow = false; drawToggles(); } });
  drawToggles();
  load().then(stream);
  return () => { alive = false; if (es) es.close(); clearTimeout(retryTimer); };
}
