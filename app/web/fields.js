// Field controls shared by Setup and Settings.
import { h, toast, errText, icon } from "./util.js";
import { post } from "./api.js";
import { needRestart } from "./store.js";

let uid = 0;
export const nextId = (p = "f") => `${p}${++uid}`;

// A secret is write-only: the server only says whether it's set. The input is never pre-filled.
export function secretField(f, { onSaved } = {}) {
  const id = nextId("sec");
  const chip = h("span", { class: `chip ${f.set ? "ok" : "off"}`, text: f.set ? "Set" : "Not set" });
  const input = h("input", { type: "password", id, autocomplete: "off", spellcheck: "false", "data-lpignore": "true",
                             placeholder: f.set ? "Paste a new value to replace it" : "Paste the value here" });
  const save = h("button", { type: "button", class: "btn", text: "Save", disabled: true });
  const msg = h("span", { class: "field-msg", role: "status" });
  input.addEventListener("input", () => { save.disabled = !input.value.trim(); });
  async function doSave() {
    const value = input.value.trim();
    if (!value) return;
    save.disabled = true;
    try {
      const r = await post("api/secrets", { key: f.key, value });
      input.value = "";
      f.set = true;
      chip.className = "chip ok"; chip.textContent = "Set";
      input.placeholder = "Paste a new value to replace it";
      msg.textContent = r.message || "Saved.";
      msg.className = "field-msg good";
      if (r.restart) needRestart();
      if (onSaved) onSaved(r);
    } catch (e) {
      msg.textContent = `Not saved: ${errText(e)}`;
      msg.className = "field-msg bad";
      save.disabled = !input.value.trim();
    }
  }
  save.addEventListener("click", doSave);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") doSave(); });
  return h("div", { class: "field" },
    h("div", { class: "field-head" }, h("label", { for: id, text: f.label }), chip),
    f.help ? h("p", { class: "help", text: f.help }) : null,
    h("div", { class: "field-row" }, input, save), msg);
}

// A plain setting stored through /api/settings.
export function textSettingField(f, { onSaved } = {}) {
  const id = nextId("txt");
  const input = h("input", { type: "text", id, autocomplete: "off", spellcheck: "false", value: f.value ?? "" });
  const save = h("button", { type: "button", class: "btn", text: "Save", disabled: true });
  const msg = h("span", { class: "field-msg", role: "status" });
  let saved = f.value ?? "";
  input.addEventListener("input", () => { save.disabled = input.value === saved; });
  async function doSave() {
    save.disabled = true;
    try {
      const r = await post("api/settings", { key: f.key, value: input.value });
      saved = input.value;
      msg.textContent = r.message || "Saved.";
      msg.className = "field-msg good";
      if (r.restart) needRestart();
      if (onSaved) onSaved(r);
    } catch (e) {
      msg.textContent = `Not saved: ${errText(e)}`;
      msg.className = "field-msg bad";
      save.disabled = false;
    }
  }
  save.addEventListener("click", doSave);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !save.disabled) doSave(); });
  return h("div", { class: "field" },
    h("div", { class: "field-head" }, h("label", { for: id, text: f.label })),
    f.help ? h("p", { class: "help", text: f.help }) : null,
    h("div", { class: "field-row" }, input, save), msg);
}

export function resultLine(ok, message) {
  return h("p", { class: `result ${ok ? "good" : "bad"}`, role: "status" }, icon(ok ? "check" : "cross"),
    h("span", {}, h("strong", { text: ok ? "Works. " : "Didn’t work. " }), message));
}

export function notifyError(prefix, e) { toast(`${prefix}: ${errText(e)}`, "error"); }
