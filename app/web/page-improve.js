import { h, clear, toast, errText, icon, timeAgo, clock, confirmDialog } from "./util.js";
import { get, post, del } from "./api.js";
import { needRestart } from "./store.js";
import { serviceAction } from "./service.js";

export const KINDS = [
  ["stopped_unprompted", "Jarvis stopped without me saying stop"],
  ["missed_wake", "I said Hey Jarvis and it didn’t wake"],
  ["false_wake", "It woke by itself"],
  ["misheard", "It misheard me"],
  ["missed_stop", "It didn’t stop when I said stop"],
  ["wrong_answer", "It said the wrong thing"],
];
export const KIND_LABEL = Object.fromEntries(KINDS);
const STATUS_LABEL = { open: "Open", fixed: "Fixed", dismissed: "Dismissed" };

export function mount(root, { store }) {
  let reports = [];
  let pending = []; // { key, kind, error }
  let loaded = false;
  let loadError = null;
  let alive = true;

  const note = h("input", { type: "text", id: "report-note", maxlength: "300", autocomplete: "off", placeholder: "e.g. the TV was on" });
  const list = h("div", { class: "report-list" });
  const live = h("div", { class: "visually-hidden", "aria-live": "polite" });
  const liveNote = h("p", { class: "muted small", id: "hooks-line" });

  const grid = h("div", { class: "report-buttons" });
  for (const [kind, label] of KINDS) {
    const b = h("button", { type: "button", class: "report-btn", "data-kind": kind }, h("span", { class: "report-label", text: label }), h("span", { class: "report-sub", text: "Press to report" }));
    b.addEventListener("click", () => press(kind, b));
    grid.append(b);
  }

  root.append(h("h1", { text: "Improve" }),
    h("p", { class: "lede", text: "When Jarvis gets something wrong, press the matching button right away, even mid-conversation. The press saves the last 30 seconds of audio and what Jarvis was doing; nothing else is recorded." }),
    grid,
    h("div", { class: "note-row" }, h("label", { for: "report-note", text: "Note for the next report (optional)" }), note),
    liveNote, live,
    h("h2", { class: "section-title", text: "Reports" }), list);

  const NO_AUDIO = {
    needs_restart: "Voice hooks aren't live, so reports are saved without audio. Restart Jarvis (Home page) to turn audio capture on.",
    voice_off: "Voice is turned off in Settings, so reports are saved without audio.",
    starting: "Jarvis is still starting up. Reports are saved without audio until it's ready (a minute or two).",
    down: "Jarvis isn't running, so reports are saved without audio. Start it on the Home page.",
  };
  function drawHooks() {
    liveNote.textContent = store.conn === "ok" && !store.hooks ? (NO_AUDIO[store.hooksReason] || NO_AUDIO.needs_restart) : "";
    liveNote.classList.toggle("warn-text", !!liveNote.textContent);
  }

  async function press(kind, btn) {
    // No confirmation: the moment of the press is what captures the audio.
    const text = note.value.trim();
    note.value = "";
    const key = Math.random().toString(36).slice(2);
    const p = { key, kind, text };
    pending.unshift(p);
    btn.classList.add("pressed");
    btn.querySelector(".report-sub").textContent = "Captured. Saving…";
    setTimeout(() => {
      btn.classList.remove("pressed");
      btn.querySelector(".report-sub").textContent = "Press to report";
    }, 1800);
    live.textContent = `Report sent: ${KIND_LABEL[kind]}`;
    draw();
    try {
      const r = await post("api/reports", text ? { kind, note: text } : { kind });
      pending = pending.filter((x) => x !== p);
      reports.unshift(r);
      if (alive) { draw(); const el = list.querySelector(`[data-id="${CSS.escape(r.id)}"]`); if (el) el.classList.add("fresh"); }
    } catch (e) {
      p.error = errText(e);
      if (alive) draw();
    }
  }

  function reportCard(r) {
    const card = h("article", { class: `report status-${r.status}`, "data-id": r.id });
    const chip = h("span", { class: `chip ${r.status === "open" ? "warn" : r.status === "fixed" ? "ok" : "off"}`, text: STATUS_LABEL[r.status] || r.status });
    card.append(h("header", { class: "report-head" },
      h("h3", { text: KIND_LABEL[r.kind] || r.kind }),
      h("span", { class: "muted", title: clock(r.t), text: timeAgo(r.t) }), chip));
    if (r.note) card.append(h("p", { class: "report-note", text: `“${r.note}”` }));

    if (r.has_audio) {
      card.append(h("audio", { controls: true, preload: "none", src: `api/reports/${encodeURIComponent(r.id)}/audio`, "aria-label": `Audio for report: ${KIND_LABEL[r.kind] || r.kind}` }));
    } else {
      card.append(h("p", { class: "muted small", text: "No audio: the voice hooks weren't live when this was pressed." }));
    }

    if (r.suggestion) {
      const sg = r.suggestion;
      const row = h("div", { class: "suggestion" }, h("p", { text: sg.text }));
      if (sg.key) {
        const apply = h("button", { type: "button", class: "btn primary", text: "Apply" });
        apply.disabled = r.status === "fixed";
        apply.addEventListener("click", () => applySuggestion(r, apply));
        row.append(apply);
      } else {
        row.classList.add("advice");
      }
      card.append(row);
    }

    const ctx = r.context || {};
    const det = h("details", { class: "ctx" }, h("summary", { text: "What was happening" }));
    const bits = [];
    if (ctx.peak_wake_score != null) bits.push(["Peak wake score", `${ctx.peak_wake_score} (needs ${ctx.threshold ?? "?"})`]);
    if (ctx.states && ctx.states.length) bits.push(["States", ctx.states.map((s) => `${s.state}${s.t ? ` @ ${new Date(s.t * 1000).toLocaleTimeString()}` : ""}`).join(" → ")]);
    if (ctx.stop_events && ctx.stop_events.length) bits.push(["Stop checks", ctx.stop_events.map((e) => JSON.stringify(e)).join("; ")]);
    const turns = ctx.turns || [];
    for (const t of turns) {
      const at = t.time ? ` @ ${new Date(t.time).toLocaleTimeString()}` : "";
      bits.push([`You said${at}`, t.heard || "(nothing heard)"]);
      bits.push(["Jarvis", t.reply || t.outcome || "(no reply)"]);
    }
    const ex = ctx.last_exchange || {};
    if (!turns.length && ex.heard) bits.push(["You said", ex.heard]);
    if (!turns.length && (ex.said || ex.reply)) bits.push(["Jarvis said", ex.said || ex.reply]);
    if (ctx.prompt) bits.push(["Report said", ctx.prompt]);
    if (!bits.length) bits.push(["", "No context was recorded."]);
    det.append(h("dl", {}, ...bits.flatMap(([k, v]) => k ? [h("dt", { text: k }), h("dd", { text: v })] : [h("dd", { class: "wide", text: v })])));
    card.append(det);

    const seg = h("div", { class: "seg", role: "group", "aria-label": "Status" });
    for (const st of ["open", "fixed", "dismissed"]) {
      const b = h("button", { type: "button", class: r.status === st ? "on" : "", "aria-pressed": String(r.status === st), text: STATUS_LABEL[st] });
      b.addEventListener("click", () => setStatus(r, st));
      seg.append(b);
    }
    const delBtn = h("button", { type: "button", class: "btn quiet danger-text", text: "Delete…" });
    delBtn.addEventListener("click", () => remove(r));
    card.append(h("footer", { class: "report-foot" }, seg, delBtn));
    return card;
  }

  async function setStatus(r, st) {
    if (r.status === st) return;
    try {
      const upd = await post(`api/reports/${encodeURIComponent(r.id)}/status`, { status: st });
      r.status = (upd && upd.status) || st;
      draw();
    } catch (e) { toast(`Couldn't change status: ${errText(e)}`, "error"); }
  }

  async function remove(r) {
    const ok = await confirmDialog({ title: "Delete this report?", body: ["The report and its audio are deleted from the Jarvis machine. This can't be undone."], confirmLabel: "Delete report", danger: true });
    if (!ok) return;
    try {
      await del(`api/reports/${encodeURIComponent(r.id)}`);
      reports = reports.filter((x) => x.id !== r.id);
      draw();
      toast("Report deleted.");
    } catch (e) { toast(`Couldn't delete: ${errText(e)}`, "error"); }
  }

  async function applySuggestion(r, btn) {
    const sg = r.suggestion;
    const ok = await confirmDialog({
      title: "Change a setting?",
      body: [sg.text, `This sets ${sg.key} to ${sg.value}. It takes effect when Jarvis restarts, and you can change it back on the Settings page.`],
      confirmLabel: "Apply change",
    });
    if (!ok) return;
    btn.disabled = true;
    try {
      const res = await post(`api/reports/${encodeURIComponent(r.id)}/apply`, {});
      r.status = "fixed";
      if (res.restart) needRestart();
      toast(res.message || "Setting changed.");
      draw();
      if (res.restart) offerRestart(r.id);
    } catch (e) {
      btn.disabled = false;
      toast(`Couldn't apply: ${errText(e)}`, "error");
    }
  }

  function offerRestart(id) {
    const card = list.querySelector(`[data-id="${CSS.escape(id)}"]`);
    if (!card) return;
    const b = h("button", { type: "button", class: "btn", text: "Restart Jarvis now…" });
    b.addEventListener("click", () => serviceAction("restart"));
    card.querySelector(".suggestion").append(h("p", { class: "applied" }, icon("check"), "Saved. Restart Jarvis for it to take effect."), b);
  }

  function draw() {
    clear(list);
    for (const p of pending) {
      list.append(h("article", { class: `report pending${p.error ? " failed" : ""}`, role: p.error ? "alert" : null },
        h("header", { class: "report-head" }, h("h3", { text: KIND_LABEL[p.kind] }),
          h("span", { class: "muted", text: p.error ? "Not saved" : "Saving…" })),
        p.error ? h("p", { text: `This report wasn't saved: ${p.error} Press the button again.` })
                : h("p", { class: "muted", text: "Captured at the moment you pressed. Working out what to suggest…" })));
    }
    if (loadError) list.append(h("p", { class: "error-note", text: `Couldn't load reports: ${loadError}` }));
    else if (loaded && !reports.length && !pending.length) {
      list.append(h("p", { class: "empty", text: "No reports yet. When Jarvis gets something wrong, press one of the buttons above and it will show up here with the audio and a suggestion." }));
    }
    for (const r of reports) list.append(reportCard(r));
  }

  async function load() {
    try {
      reports = (await get("api/reports")).reports || [];
      loadError = null;
    } catch (e) { loadError = errText(e); }
    loaded = true;
    if (alive) draw();
  }

  const off = store.on((w) => { if (w === "status" || w === "conn") drawHooks(); });
  drawHooks(); draw(); load();
  return () => { alive = false; off(); };
}
