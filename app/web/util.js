// Small DOM helpers. Every string goes in through textContent, so log lines, transcripts and
// integration text can never become markup.

export function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === false || v == null) continue;
    if (k === "class") el.className = v;
    else if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "vars") for (const [n, val] of Object.entries(v)) el.style.setProperty(n, val);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  append(el, kids);
  return el;
}

export function append(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return el;
}

export function clear(el) {
  while (el.firstChild) el.removeChild(el.firstChild);
  return el;
}

const SVG_NS = "http://www.w3.org/2000/svg";
export function s(tag, attrs, ...kids) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "text") el.textContent = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  append(el, kids);
  return el;
}

// Fixed icon paths (24x24 grid), never built from data.
const ICONS = {
  check: "M5 12.5l4.5 4.5L19 7.5",
  cross: "M6 6l12 12M18 6L6 18",
  warn: "M12 4l9 16H3zM12 10v4M12 17v.5",
  help: "M9.5 9a2.5 2.5 0 115 0c0 1.7-2.5 2-2.5 4M12 17v.5",
};
export function icon(name) {
  return s("svg", { class: "icon", viewBox: "0 0 24 24", "aria-hidden": "true", fill: "none", stroke: "currentColor",
                    "stroke-width": "2.2", "stroke-linecap": "round", "stroke-linejoin": "round" },
           s("path", { d: ICONS[name] }));
}

// A small safe subset of markdown: **bold**, `code`, [text](https://link). Everything else is plain text.
// Links must be http(s); they open in a new tab without a referrer.
export function md(text) {
  const frag = document.createDocumentFragment();
  const re = /\*\*([^*]+)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)/g;
  let last = 0, m;
  text = String(text ?? "");
  while ((m = re.exec(text))) {
    if (m.index > last) frag.append(text.slice(last, m.index));
    if (m[1] != null) frag.append(h("strong", { text: m[1] }));
    else if (m[2] != null) frag.append(h("code", { text: m[2] }));
    else if (/^https?:\/\//i.test(m[4])) {
      frag.append(h("a", { href: m[4], target: "_blank", rel: "noopener noreferrer", text: m[3] }));
    } else frag.append(m[0]);
    last = re.lastIndex;
  }
  if (last < text.length) frag.append(text.slice(last));
  return frag;
}

export function timeAgo(t, now = Date.now() / 1000) {
  const d = Math.max(0, now - t);
  if (d < 10) return "just now";
  if (d < 60) return `${Math.floor(d)} s ago`;
  if (d < 3600) return `${Math.floor(d / 60)} min ago`;
  if (d < 86400) return `${Math.floor(d / 3600)} h ago`;
  return `${Math.floor(d / 86400)} d ago`;
}

export function clock(t) {
  return new Date(t * 1000).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", second: "2-digit" });
}

export function toast(message, kind = "info") {
  const box = document.getElementById("toasts");
  const el = h("div", { class: `toast ${kind}` }, kind === "error" ? icon("warn") : icon("check"), h("span", { text: message }));
  box.append(el);
  while (box.children.length > 4) box.firstChild.remove();
  setTimeout(() => el.remove(), kind === "error" ? 9000 : 4500);
}

// A modal confirmation. Resolves true or false. Escape and Cancel both mean no.
export function confirmDialog({ title, body, confirmLabel = "Confirm", danger = false }) {
  const dlg = document.getElementById("dialog");
  return new Promise((resolve) => {
    clear(dlg);
    const ok = h("button", { type: "button", class: danger ? "btn danger" : "btn primary", text: confirmLabel });
    const no = h("button", { type: "button", class: "btn", text: "Cancel" });
    let answer = false;
    ok.addEventListener("click", () => { answer = true; dlg.close(); });
    no.addEventListener("click", () => dlg.close());
    dlg.append(h("h2", { id: "dialog-title", text: title }),
      ...[].concat(body).map((p) => h("p", { text: p })),
      h("div", { class: "dialog-actions" }, no, ok));
    dlg.addEventListener("close", () => resolve(answer), { once: true });
    dlg.showModal();
    no.focus();
  });
}

// A modal message with one button, for things the owner must read (not a toast that disappears).
export function noticeDialog(title, body) {
  const dlg = document.getElementById("dialog");
  return new Promise((resolve) => {
    clear(dlg);
    const ok = h("button", { type: "button", class: "btn primary", text: "OK" });
    ok.addEventListener("click", () => dlg.close());
    dlg.append(h("h2", { id: "dialog-title", text: title }), ...[].concat(body).map((p) => h("p", { text: p })),
      h("div", { class: "dialog-actions" }, ok));
    dlg.addEventListener("close", () => resolve(), { once: true });
    dlg.showModal();
    ok.focus();
  });
}

export function errText(e) {
  return e && e.message ? e.message : String(e);
}

export function el$(id) { return document.getElementById(id); }

export function debounce(fn, ms) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
