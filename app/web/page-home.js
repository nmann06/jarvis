import { h, clear, toast, errText, icon } from "./util.js";
import { post } from "./api.js";
import { applyLight, lightWords } from "./light.js";
import { serviceAction } from "./service.js";

const SERVICE_WORDS = {
  active: ["Running", "ok"], inactive: ["Stopped", "off"], failed: ["Failed", "bad"], unknown: ["Unknown", "off"],
};

export function mount(root, { store }) {
  const light = h("div", { class: "light big", role: "img", "data-state": "unknown" });
  const lightTitle = h("h2", { class: "light-title" });
  const lightSub = h("p", { class: "light-sub" });
  const hooksNote = h("div", { class: "hooks-note" });
  const hero = h("section", { class: "card hero", "aria-label": "Light" },
    h("div", { class: "light-stage" }, light), h("div", { class: "light-words", "aria-live": "polite" }, lightTitle, lightSub), hooksNote);

  const svcChip = h("span", { class: "chip" });
  const svcMeta = h("span", { class: "muted" });
  const bStart = h("button", { type: "button", class: "btn", text: "Start", onclick: () => serviceAction("start") });
  const bStop = h("button", { type: "button", class: "btn", text: "Stop…", onclick: () => serviceAction("stop") });
  const bRestart = h("button", { type: "button", class: "btn", text: "Restart…", onclick: () => serviceAction("restart") });
  const svc = h("section", { class: "card" }, h("h2", { text: "Jarvis service" }),
    h("div", { class: "row" }, svcChip, svcMeta), h("div", { class: "btn-row" }, bStart, bStop, bRestart));

  const bar = h("div", { class: "meter-fill" });
  const meter = h("div", { class: "meter", role: "meter", "aria-label": "Microphone level", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": "0" }, bar);
  const wake = h("span", { class: "muted" });
  const mute = h("button", { type: "button", class: "btn", role: "switch", "aria-checked": "false" });
  const micNote = h("p", { class: "muted small" });
  const mic = h("section", { class: "card" }, h("h2", { text: "Microphone" }), meter,
    h("div", { class: "row between" }, wake, mute), micNote);

  root.append(h("h1", { text: "Home" }), h("div", { class: "home-grid" }, hero, h("div", { class: "stack" }, svc, mic)));

  function drawLight() {
    applyLight(light, store);
    const [t, sub] = lightWords(store);
    lightTitle.textContent = t;
    lightSub.textContent = sub;
  }

  function drawHooks() {
    clear(hooksNote);
    if (store.conn !== "ok" || store.hooks) return;
    // Only needs_restart asks for a restart; the other reasons say what is actually going on.
    const reason = store.hooksReason;
    let title, text, action;
    if (reason === "voice_off") {
      title = "Voice is turned off. ";
      text = "VOICE_ENABLED is off in Settings, so Jarvis isn't listening and there's no light or microphone meter. A restart alone won't change that: turn Voice on in Settings, then restart.";
      action = h("a", { class: "btn", href: "#settings", text: "Open Settings" });
    } else if (reason === "starting") {
      title = "Jarvis is starting up. ";
      text = "It's still loading its models. The light and the microphone meter come on by themselves in a minute or two; there's nothing to do.";
    } else if (reason === "down") {
      title = "Jarvis isn't running. ";
      text = "Start it to get the light, the microphone meter and audio capture for reports.";
      action = h("button", { type: "button", class: "btn primary", text: "Start Jarvis", onclick: () => serviceAction("start") });
    } else {
      title = "Jarvis needs a restart for the light and audio capture. ";
      text = "The running copy started before the voice hooks existed, so the light can't show what it's doing and the Improve reports can't save audio. Restarting takes a few seconds and interrupts anything Jarvis is doing.";
      action = h("button", { type: "button", class: "btn primary", text: "Restart Jarvis", onclick: () => serviceAction("restart") });
    }
    hooksNote.append(h("div", { class: `banner ${reason === "needs_restart" || !reason ? "warn" : "info"}`, role: "status" }, icon(reason === "starting" ? "help" : "warn"),
      h("div", {}, h("strong", { text: title }), text), action));
  }

  function drawStatus() {
    const st = store.status;
    const [word, kind] = SERVICE_WORDS[(st && st.service) || "unknown"] || SERVICE_WORDS.unknown;
    svcChip.className = `chip ${store.conn === "ok" ? kind : "off"}`;
    svcChip.textContent = store.conn === "ok" ? word : "Unknown";
    svcMeta.textContent = st && st.version ? `version ${st.version}` : "";
    const svcState = store.conn === "ok" && st ? st.service : "unknown";
    bStart.disabled = svcState === "active" || store.conn !== "ok";
    bStop.disabled = svcState === "inactive" || store.conn !== "ok";
    bRestart.disabled = store.conn !== "ok";
    const live = store.conn === "ok" && store.hooks;
    mute.disabled = !live;
    const muted = !!(st && st.muted);
    mute.setAttribute("aria-checked", String(muted));
    mute.textContent = muted ? "Unmute microphone" : "Mute microphone";
    mute.classList.toggle("on", muted);
    micNote.textContent = !live ? "The meter and mute work once Jarvis's voice hooks are live. See the note beside the light."
      : muted ? "Muted: Jarvis can't hear you. Alarms still ring." : "";
    if (!live) drawLevel();
    drawHooks();
  }

  function drawLevel() {
    const live = store.conn === "ok" && store.hooks;
    const l = live ? Math.min(1, Math.sqrt(store.level.rms || 0) * 1.6) : 0;
    bar.style.setProperty("--level", String(l));
    meter.setAttribute("aria-valuenow", String(Math.round(l * 100)));
    meter.classList.toggle("off", !live);
    wake.textContent = live ? `Wake score ${(store.level.wake_score ?? 0).toFixed(2)}` : "No signal";
  }

  mute.addEventListener("click", async () => {
    const want = mute.getAttribute("aria-checked") !== "true";
    mute.disabled = true;
    try {
      const r = await post("api/mute", { muted: want });
      if (store.status) store.status.muted = r.muted;
      store.emit("status");
    } catch (e) { toast(`Couldn't change mute: ${errText(e)}`, "error"); }
    drawStatus();
  });

  const off = store.on((what) => {
    if (what === "light" || what === "conn") { drawLight(); drawStatus(); }
    if (what === "status") drawStatus();
    if (what === "level") drawLevel();
  });
  drawLight(); drawStatus(); drawLevel();
  return off;
}
