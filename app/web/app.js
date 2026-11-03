// Shell: navigation, connection banner, restart bar, and mounting one page at a time.
import { h, clear, el$, errText } from "./util.js";
import { store, startStore } from "./store.js";
import { serviceAction } from "./service.js";
import { applyLight, lightWords } from "./light.js";

const PAGES = [
  ["home", "Home", () => import("./page-home.js")],
  ["setup", "Setup", () => import("./page-setup.js")],
  ["settings", "Settings", () => import("./page-settings.js")],
  ["improve", "Improve", () => import("./page-improve.js")],
  ["stats", "Stats", () => import("./page-stats.js")],
  ["logs", "Logs", () => import("./page-logs.js")],
];

let current = null; // { id, unmount }
let navToken = 0;

function buildNav() {
  const nav = el$("nav");
  for (const [id, label] of PAGES) {
    nav.append(h("a", { href: `#${id}`, "data-page": id, text: label }));
  }
}

async function route() {
  const id = (location.hash || "#home").slice(1);
  const entry = PAGES.find((p) => p[0] === id) || PAGES[0];
  const token = ++navToken;
  if (current && current.unmount) { try { current.unmount(); } catch { /* page already gone */ } }
  current = null;
  for (const a of el$("nav").children) {
    const on = a.dataset.page === entry[0];
    a.classList.toggle("on", on);
    if (on) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  const root = clear(el$("page"));
  root.className = `page page-${entry[0]}`;
  document.title = `${entry[1]} · Jarvis Panel`;
  try {
    const mod = await entry[2]();
    if (token !== navToken) return;
    const unmount = mod.mount(root, { store });
    current = { id: entry[0], unmount };
  } catch (e) {
    root.append(h("p", { class: "error-note", text: `This page failed to load: ${errText(e)}` }));
  }
}

function renderRestartBar() {
  const bar = el$("restart-bar");
  clear(bar);
  bar.hidden = !store.restartNeeded;
  if (bar.hidden) return;
  const btn = h("button", { type: "button", class: "btn primary", text: "Restart to apply" });
  btn.addEventListener("click", async () => { btn.disabled = true; await serviceAction("restart"); btn.disabled = false; });
  bar.append(h("span", { class: "restart-text", text: "Changes are saved but not live yet. Jarvis picks them up when it restarts (a few seconds)." }), btn);
}

function renderConn() {
  const c = el$("conn");
  const b = el$("banner");
  clear(c); clear(b);
  const map = { ok: "Connected", lost: "Connection lost", connecting: "Connecting…", unauth: "Signed out" };
  c.dataset.conn = store.conn;
  c.append(h("span", { class: "dot" }), h("span", { text: map[store.conn] }));
  if (store.conn === "lost") {
    b.append(h("div", { class: "banner bad", role: "alert" },
      h("strong", { text: "Lost the connection to Jarvis Panel. " }),
      "Nothing you see below is live. The panel is trying again every few seconds; check that the Jarvis machine is on and Tailscale is connected."));
  } else if (store.conn === "unauth") {
    b.append(h("div", { class: "banner bad", role: "alert" },
      h("strong", { text: "This browser isn't signed in. " }),
      "Open the panel from the Jarvis Panel app, or visit /login?token=… once with the token from data/panel-token."));
  }
}

function renderLight() {
  const l = el$("brand-light");
  applyLight(l, store);
  l.title = `${lightWords(store)[0]}`;
}

export function boot() {
  buildNav();
  store.on((what) => {
    if (what === "conn") renderConn();
    if (what === "light") renderLight();
    if (what === "restart" || what === "status") renderRestartBar();
  });
  renderConn(); renderLight(); renderRestartBar();
  window.addEventListener("hashchange", route);
  route();
  startStore();
}

boot();
