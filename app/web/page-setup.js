import { h, clear, md, icon, errText } from "./util.js";
import { get, post } from "./api.js";
import { secretField, textSettingField, resultLine } from "./fields.js";

export function mount(root) {
  let alive = true;
  const timers = new Set();
  const checksBox = h("section", { class: "card" }, h("h2", { text: "This machine" }), h("p", { class: "muted", text: "Checking…" }));
  const intBox = h("div", { class: "int-list" }, h("p", { class: "muted", text: "Loading integrations…" }));
  root.append(h("h1", { text: "Setup" }),
    h("p", { class: "lede", text: "Machine checks first, then each service Jarvis can connect to. Keys you paste here are stored on the Jarvis machine and never shown again." }),
    checksBox, h("h2", { class: "section-title", text: "Integrations" }), intBox);

  async function loadChecks() {
    clear(checksBox);
    checksBox.append(h("div", { class: "row between" }, h("h2", { text: "This machine" }),
      h("button", { type: "button", class: "btn quiet", text: "Check again", onclick: loadChecks })));
    try {
      const { checks } = await get("api/checks");
      const bad = checks.filter((c) => !c.ok).length;
      checksBox.append(h("p", { class: bad ? "warn-text" : "good-text", text: bad ? `${bad} of ${checks.length} checks need attention.` : "All checks pass." }));
      const ul = h("ul", { class: "checks" });
      for (const c of checks) {
        ul.append(h("li", { class: c.ok ? "ok" : "bad" }, icon(c.ok ? "check" : "cross"),
          h("div", {}, h("strong", { text: c.name }), h("span", { class: "muted", text: ` ${c.detail || ""}` }),
            !c.ok && c.fix ? h("p", { class: "fix" }, h("strong", { text: "To fix: " }), c.fix) : null)));
      }
      checksBox.append(ul);
    } catch (e) {
      checksBox.append(h("p", { class: "error-note", text: `Couldn't run the checks: ${errText(e)}` }));
    }
  }

  function integrationCard(it) {
    const status = h("span", { class: `chip ${it.configured ? "ok" : "off"}`, text: it.configured ? "Configured" : "Not set up" });
    const card = h("article", { class: "card int", id: `int-${it.id}`.replace(/[^\w-]/g, "_") },
      h("header", { class: "int-head" }, h("h3", { text: it.name }),
        it.required ? h("span", { class: "chip warn", text: "Required" }) : h("span", { class: "chip off", text: "Optional" }), status),
      h("p", { class: "muted", text: it.summary || "" }));

    if (it.steps && it.steps.length) {
      const ol = h("ol", { class: "steps" });
      for (const s of it.steps) ol.append(h("li", {}, md(s)));
      card.append(ol);
    }
    for (const f of it.fields || []) {
      card.append(f.secret ? secretField(f, { onSaved: () => { status.textContent = "Saved, test it"; } }) : textSettingField(f));
    }

    const out = h("div", { class: "int-out" });
    const actions = h("div", { class: "btn-row" });
    if (it.testable) {
      const t = h("button", { type: "button", class: "btn", text: "Test" });
      t.addEventListener("click", async () => {
        t.disabled = true; t.textContent = "Testing…";
        clear(out);
        try {
          const r = await post(`api/integrations/${encodeURIComponent(it.id)}/test`, {});
          out.append(resultLine(r.ok, r.message));
          status.className = `chip ${r.ok ? "ok" : "bad"}`;
          status.textContent = r.ok ? "Works" : "Test failed";
        } catch (e) {
          out.append(resultLine(false, errText(e)));
        }
        t.disabled = false; t.textContent = "Test";
      });
      actions.append(t);
    } else {
      actions.append(h("span", { class: "muted small", text: "No automatic test for this one." }));
    }
    if (it.login) {
      const b = h("button", { type: "button", class: "btn primary", text: it.login.label || "Sign in" });
      const log = h("pre", { class: "job-out", "aria-label": "Sign-in output" });
      const jobMsg = h("p", { class: "muted small", role: "status" });
      b.addEventListener("click", async () => {
        b.disabled = true; log.textContent = ""; jobMsg.textContent = "Starting…";
        log.hidden = false;
        try {
          const { job } = await post(`api/integrations/${encodeURIComponent(it.id)}/login`, {});
          await poll(job);
        } catch (e) { jobMsg.textContent = `Couldn't start sign-in: ${errText(e)}`; }
        b.disabled = false;
      });
      async function poll(job) {
        for (;;) {
          if (!alive) return;
          let j;
          try { j = await get(`api/jobs/${encodeURIComponent(job)}`); }
          catch (e) { jobMsg.textContent = `Lost track of the sign-in job: ${errText(e)}`; return; }
          log.textContent = j.output || "";
          log.scrollTop = log.scrollHeight;
          if (!j.running) {
            jobMsg.textContent = j.exit === 0 ? "Sign-in finished. Press Test to check it." : `Sign-in ended with exit code ${j.exit}. Read the output above, then try again.`;
            jobMsg.className = `small ${j.exit === 0 ? "good-text" : "bad-text"}`;
            return;
          }
          jobMsg.textContent = "Running. Finish any steps it shows or opens in your browser…";
          await new Promise((r) => { const id = setTimeout(r, 1000); timers.add(id); });
        }
      }
      log.hidden = true;
      actions.append(b);
      card.append(actions, jobMsg, log, out);
    } else card.append(actions, out);
    return card;
  }

  async function loadInts() {
    try {
      const { integrations } = await get("api/integrations");
      clear(intBox);
      if (!integrations.length) intBox.append(h("p", { class: "empty", text: "The server listed no integrations." }));
      for (const it of integrations) intBox.append(integrationCard(it));
    } catch (e) {
      clear(intBox);
      intBox.append(h("p", { class: "error-note", text: `Couldn't load integrations: ${errText(e)}` }));
    }
  }

  loadChecks(); loadInts();
  return () => { alive = false; timers.forEach(clearTimeout); };
}
