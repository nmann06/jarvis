// Live state shared by every page: connection, service status, the light and the mic level.
import { get, setHandlers } from "./api.js";

const listeners = new Set();
export const store = {
  conn: "connecting",          // connecting | ok | lost | unauth
  status: null,                // last /api/status
  light: "unknown",            // idle | listening | thinking | speaking | unknown
  hooks: false,
  hooksReason: "needs_restart", // null when live; else needs_restart | voice_off | starting | down
  level: { rms: 0, peak: 0, wake_score: 0 },
  localRestart: false,         // we saved something that needs a restart
  get restartNeeded() { return this.localRestart || !!(this.status && this.status.restart_needed); },
  get shownLight() { return this.conn === "ok" && this.hooks ? this.light : "unknown"; },
  on(fn) { listeners.add(fn); return () => listeners.delete(fn); },
  emit(what) { for (const fn of [...listeners]) fn(what, this); },
};

function setConn(c) {
  if (store.conn === c) return;
  store.conn = c;
  store.emit("conn");
  store.emit("light");
}

setHandlers(() => { setConn("lost"); restartEvents(); }, () => setConn("unauth"));

export function needRestart() { store.localRestart = true; store.emit("restart"); }
export function restartDone() { store.localRestart = false; store.emit("restart"); }

let es = null, retry = 0, timer = null;

function restartEvents() {
  if (es) { es.close(); es = null; }
  clearTimeout(timer);
  const delay = Math.min(10000, 1000 * 2 ** retry++);
  timer = setTimeout(connectEvents, delay);
}

function connectEvents() {
  if (es) es.close();
  es = new EventSource("api/events");
  es.onopen = () => { retry = 0; };
  es.onmessage = (m) => {
    let ev;
    try { ev = JSON.parse(m.data); } catch { return; }
    if (store.conn !== "ok") { setConn("ok"); refreshStatus(); }
    if (ev.type === "state") { store.light = ev.state; store.emit("light"); }
    else if (ev.type === "hooks") { store.hooks = !!ev.live; if (ev.live) store.hooksReason = null; else { store.light = "unknown"; refreshStatus(); } store.emit("light"); store.emit("status"); }
    else if (ev.type === "level") { store.level = ev; store.emit("level"); }
  };
  es.onerror = () => {
    // EventSource retries by itself unless the server refused it; either way show unknown, not idle.
    if (store.conn === "ok") setConn("lost");
    if (es && es.readyState === EventSource.CLOSED) restartEvents();
  };
}

export async function refreshStatus() {
  try {
    const st = await get("api/status");
    store.status = st;
    store.hooks = !!st.hooks_live;
    store.hooksReason = st.hooks_live ? null : (st.hooks_reason || "needs_restart");
    if (st.state && st.state !== "unknown") store.light = st.state;
    if (!st.hooks_live) store.light = "unknown";
    if (!st.restart_needed && !store.localRestart) { /* nothing */ }
    if (store.conn !== "ok") {
      setConn("ok");
      connectEvents();
    }
    store.emit("status");
    store.emit("light");
    store.emit("restart");
  } catch (e) {
    if (e.status !== 401) setConn("lost");
  }
}

export function startStore() {
  refreshStatus().then(() => { if (!es) connectEvents(); });
  setInterval(refreshStatus, 4000);
}
