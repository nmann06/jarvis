import { h, clear, toast, errText } from "./util.js";
import { get, post } from "./api.js";
import { needRestart } from "./store.js";
import { secretField, nextId } from "./fields.js";

const OTHER = "__other__";

export function mount(root) {
  const nav = h("nav", { class: "chips", "aria-label": "Setting groups" });
  const body = h("div", { class: "settings" }, h("p", { class: "muted", text: "Loading settings…" }));
  root.append(h("h1", { text: "Settings" }),
    h("p", { class: "lede", text: "Changes are saved straight away. Most take effect when Jarvis restarts: the bar at the bottom appears when one does." }),
    nav, body);

  async function save(s, value, msgEl, rowEl) {
    msgEl.className = "field-msg"; msgEl.textContent = "Saving…";
    try {
      const r = await post("api/settings", { key: s.key, value });
      const reset = value === "default";
      s.value = reset ? null : value;
      msgEl.className = "field-msg good"; msgEl.textContent = r.message || "Saved.";
      if (r.restart) needRestart();
      rowEl.classList.toggle("changed", s.value != null && s.value !== s.default);
      return true;
    } catch (e) {
      msgEl.className = "field-msg bad"; msgEl.textContent = `Not saved: ${errText(e)}`;
      return false;
    }
  }

  function control(s, row, msg, refresh) {
    const id = nextId("set");
    const eff = () => (s.value ?? s.default ?? "");
    if (s.kind === "bool") {
      const sw = h("button", { type: "button", class: "switch", role: "switch", id, "aria-labelledby": `${id}-l` },
        h("span", { class: "knob" }), h("span", { class: "switch-text" }));
      const draw = () => {
        const on = eff() === "on";
        sw.setAttribute("aria-checked", String(on));
        sw.querySelector(".switch-text").textContent = on ? "On" : "Off";
      };
      sw.addEventListener("click", async () => {
        const want = eff() === "on" ? "off" : "on";
        sw.disabled = true;
        await save(s, want, msg, row);
        sw.disabled = false; draw(); refresh();
      });
      draw();
      return { el: sw, id, redraw: draw };
    }
    if (s.kind === "choice") {
      const choices = s.choices || [];
      const sel = h("select", { id, "aria-labelledby": `${id}-l` },
        ...choices.map((c) => h("option", { value: c, text: c })), h("option", { value: OTHER, text: "Other… (type a model id)" }));
      const custom = h("input", { type: "text", "aria-label": `${s.label}: custom value`, placeholder: "model id", autocomplete: "off", spellcheck: "false" });
      const saveBtn = h("button", { type: "button", class: "btn", text: "Save", disabled: true });
      const wrap = h("div", { class: "field-row" }, sel, custom, saveBtn);
      const draw = () => {
        const v = eff();
        if (choices.includes(v)) { sel.value = v; custom.hidden = true; saveBtn.hidden = true; }
        else { sel.value = OTHER; custom.hidden = false; saveBtn.hidden = false; custom.value = v; saveBtn.disabled = true; }
      };
      sel.addEventListener("change", async () => {
        if (sel.value === OTHER) { custom.hidden = false; saveBtn.hidden = false; custom.focus(); return; }
        await save(s, sel.value, msg, row); draw(); refresh();
      });
      custom.addEventListener("input", () => { saveBtn.disabled = !custom.value.trim() || custom.value.trim() === eff(); });
      const commit = async () => { if (!saveBtn.disabled) { saveBtn.disabled = true; await save(s, custom.value.trim(), msg, row); draw(); refresh(); } };
      saveBtn.addEventListener("click", commit);
      custom.addEventListener("keydown", (e) => { if (e.key === "Enter") commit(); });
      draw();
      return { el: wrap, id, redraw: draw };
    }
    // int, float, text
    const input = h("input", { type: s.kind === "text" ? "text" : "number", id, step: s.kind === "float" ? "any" : "1",
                               inputmode: s.kind === "text" ? null : "decimal", autocomplete: "off", spellcheck: "false", "aria-labelledby": `${id}-l` });
    const saveBtn = h("button", { type: "button", class: "btn", text: "Save", disabled: true });
    const wrap = h("div", { class: "field-row" }, input, saveBtn);
    const draw = () => { input.value = eff(); saveBtn.disabled = true; };
    input.addEventListener("input", () => { saveBtn.disabled = input.value === eff(); });
    const commit = async () => {
      if (saveBtn.disabled) return;
      saveBtn.disabled = true;
      const ok = await save(s, input.value, msg, row);
      if (ok) draw(); else saveBtn.disabled = false;
      refresh();
    };
    saveBtn.addEventListener("click", commit);
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") commit(); });
    draw();
    return { el: wrap, id, redraw: draw };
  }

  function row(s) {
    if (s.secret) {
      const r = h("div", { class: "setting secret" }, h("div", { class: "setting-main" },
        secretField({ key: s.key, label: s.label, set: !!s.set, help: s.about && !/\.env/.test(s.about) ? s.about : "Write-only: the current value is never shown." })));
      return r;
    }
    const msg = h("span", { class: "field-msg", role: "status" });
    const r = h("div", { class: "setting" });
    const reset = h("button", { type: "button", class: "btn quiet", text: "Reset to default" });
    const refresh = () => {
      const isDefault = s.value == null || s.value === s.default;
      reset.disabled = isDefault;
      r.classList.toggle("changed", !isDefault);
      defaultNote.textContent = `Default: ${s.default === "" || s.default == null ? "(empty)" : s.default}`;
    };
    const defaultNote = h("span", { class: "muted small" });
    const c = control(s, r, msg, refresh);
    reset.addEventListener("click", async () => {
      reset.disabled = true;
      const ok = await save(s, "default", msg, r);
      c.redraw(); refresh();
      if (!ok) reset.disabled = false;
    });
    r.append(
      h("div", { class: "setting-main" },
        h("label", { id: `${c.id}-l`, for: s.kind === "choice" || s.kind === "bool" ? c.id : c.id, text: s.label }),
        h("p", { class: "about", text: s.about || "" }),
        s.note ? h("p", { class: "muted small", text: s.note }) : null),
      h("div", { class: "setting-ctl" }, c.el, h("div", { class: "setting-foot" }, defaultNote, reset), msg));
    refresh();
    return r;
  }

  async function load() {
    try {
      const data = await get("api/settings");
      clear(body); clear(nav);
      const cats = [...(data.categories || [])];
      for (const s of data.settings) if (!cats.includes(s.category)) cats.push(s.category);
      let n = 0;
      for (const cat of cats) {
        const items = data.settings.filter((s) => s.category === cat);
        if (!items.length) continue;
        const sec = h("section", { class: "card group", id: `grp-${n++}` }, h("h2", { text: cat }));
        for (const s of items) sec.append(row(s));
        body.append(sec);
        nav.append(h("button", { type: "button", class: "chip-btn", text: cat, onclick: () => sec.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" }) }));
      }
      if (!n) body.append(h("p", { class: "empty", text: "The server returned no settings." }));
    } catch (e) {
      clear(body);
      body.append(h("p", { class: "error-note", text: `Couldn't load settings: ${errText(e)}` }));
    }
  }
  load();
  return () => {};
}
