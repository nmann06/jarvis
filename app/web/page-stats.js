// Stats: hand-written SVG charts (no library). Colours are fixed per entity (see app.css --s1..--s6),
// every chart has a legend, a hover/focus tooltip listing every series, and a table view.
import { h, clear, s, errText } from "./util.js";
import { get } from "./api.js";
import { KINDS } from "./page-improve.js";

const KIND_SLOT = Object.fromEntries(KINDS.map(([k], i) => [k, i + 1]));
const KIND_NAME = Object.fromEntries(KINDS);
const fmtDate = (d) => new Date(`${d}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" });
const fmtDay = (d) => new Date(`${d}T12:00:00`).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
const fmtSec = (ms) => (ms == null ? "–" : `${(ms / 1000).toFixed(1)} s`);
const fmtInt = (n) => (n == null ? "–" : String(Math.round(n)));

// A top value that makes four whole, round steps (so integer counts never get 1.3 on the axis).
function niceMax(max) {
  if (max <= 0) return 4;
  const raw = max / 4;
  const p = 10 ** Math.floor(Math.log10(raw));
  for (const m of [1, 2, 5, 10]) if (m * p >= raw) return m * p * 4;
  return 40 * p;
}

function roundedTop(x, y, w, hgt, r) {
  r = Math.min(r, w / 2, hgt);
  return `M${x},${y + hgt}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hgt}Z`;
}

// spec: { kind: "columns"|"stacked"|"lines", days: [{label, full}], series: [{name, slot, values}], unit, fmt, yMax }
function chart(spec, width) {
  const m = { l: 44, r: spec.kind === "lines" ? 104 : 12, t: 10, b: 26 };
  const H = 220;
  const W = Math.max(280, width);
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  const n = spec.days.length;
  const slot = pw / Math.max(1, n);
  const totals = spec.days.map((_, i) => spec.series.reduce((a, se) => a + (se.values[i] || 0), 0));
  const rawMax = spec.kind === "stacked" ? Math.max(...totals, 0) : Math.max(...spec.series.flatMap((se) => se.values.map((v) => v || 0)), 0);
  const yMax = niceMax(rawMax);
  const y = (v) => m.t + ph - (v / yMax) * ph;
  const cx = (i) => m.l + slot * (i + 0.5);
  const svg = s("svg", { class: "chart-svg", viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", "aria-label": spec.title });

  // Recessive grid and y labels
  for (let i = 0; i <= 4; i++) {
    const v = (yMax / 4) * i;
    const yy = y(v);
    svg.append(s("line", { class: i === 0 ? "axis" : "grid", x1: m.l, x2: W - m.r, y1: yy, y2: yy }));
    svg.append(s("text", { class: "tick", x: m.l - 8, y: yy + 4, "text-anchor": "end", text: spec.axisFmt ? spec.axisFmt(v) : String(Math.round(v * 100) / 100) }));
  }
  // x labels, thinned to avoid collisions
  const every = Math.max(1, Math.ceil(64 / slot));
  spec.days.forEach((d, i) => {
    if ((n - 1 - i) % every === 0) svg.append(s("text", { class: "tick", x: cx(i), y: H - 6, "text-anchor": "middle", text: d.label }));
  });

  const marks = s("g", {});
  svg.append(marks);
  const k = spec.series.length;
  if (spec.kind === "columns") {
    const bw = Math.min(24, (slot * 0.7 - 2 * (k - 1)) / k);
    spec.days.forEach((_, i) => {
      spec.series.forEach((se, j) => {
        const v = se.values[i] || 0;
        if (v <= 0) return;
        const x = cx(i) - (k * bw + 2 * (k - 1)) / 2 + j * (bw + 2);
        marks.append(s("path", { class: `fill-${se.slot}`, d: roundedTop(x, y(v), Math.max(2, bw), y(0) - y(v), 4) }));
      });
    });
  } else if (spec.kind === "stacked") {
    const bw = Math.min(24, slot * 0.6);
    spec.days.forEach((_, i) => {
      let acc = 0;
      const segs = spec.series.map((se) => ({ se, v: se.values[i] || 0 })).filter((q) => q.v > 0);
      segs.forEach((q, j) => {
        const y0 = y(acc), y1 = y(acc + q.v);
        acc += q.v;
        const top = j === segs.length - 1;
        const gap = j > 0 ? 2 : 0; // 2px surface gap between segments
        const hh = Math.max(1, y0 - y1 - gap);
        const x = cx(i) - bw / 2;
        marks.append(top ? s("path", { class: `fill-${q.se.slot}`, d: roundedTop(x, y1, bw, hh, 4) })
                         : s("rect", { class: `fill-${q.se.slot}`, x, y: y1, width: bw, height: hh }));
      });
    });
  } else {
    for (const se of spec.series) {
      let d = "", pen = false;
      se.values.forEach((v, i) => {
        if (v == null) { pen = false; return; }
        d += `${pen ? "L" : "M"}${cx(i).toFixed(1)},${y(v).toFixed(1)}`;
        pen = true;
      });
      marks.append(s("path", { class: `line stroke-${se.slot}`, d, fill: "none" }));
      // Markers only where a point stands alone or at the end; the line itself carries the rest.
      let last = -1;
      se.values.forEach((v, i) => { if (v != null) last = i; });
      se.values.forEach((v, i) => {
        if (v == null) return;
        const alone = (i === 0 || se.values[i - 1] == null) && (i === n - 1 || se.values[i + 1] == null);
        if (alone || i === last) marks.append(s("circle", { class: `dot fill-${se.slot}`, cx: cx(i), cy: y(v), r: 4.5 }));
      });
      if (last >= 0) {
        marks.append(s("text", { class: "end-label", x: cx(last) + 10, y: y(se.values[last]) + 4, text: `${se.name} ${spec.fmt(se.values[last])}` }));
      }
    }
  }
  return { svg, geom: { m, slot, cx, W, H, y } };
}

function attachHover(wrap, svgInfo, spec) {
  const { svg, geom } = svgInfo;
  const { m, slot, cx, H, y } = geom;
  const tip = h("div", { class: "tooltip", hidden: true, role: "presentation" });
  const band = s("rect", { class: "hover-band", y: m.t, height: H - m.t - m.b, width: Math.max(2, slot), visibility: "hidden" });
  const cross = s("line", { class: "crosshair", y1: m.t, y2: H - m.b, visibility: "hidden" });
  const rings = s("g", {});
  svg.insertBefore(band, svg.children[1]);
  svg.append(cross, rings);
  const hits = s("g", {});
  svg.append(hits);

  function show(i) {
    clear(tip);
    tip.append(h("div", { class: "tip-title", text: spec.days[i].full }));
    for (const se of spec.series) {
      const v = se.values[i];
      tip.append(h("div", { class: "tip-row" }, h("span", { class: `key bg-${se.slot}` }), h("strong", { text: v == null ? "–" : spec.fmt(v) }), h("span", { class: "muted", text: se.name })));
    }
    tip.hidden = false;
    const x = cx(i);
    band.setAttribute("x", x - slot / 2);
    band.setAttribute("visibility", spec.kind === "lines" ? "hidden" : "visible");
    cross.setAttribute("x1", x); cross.setAttribute("x2", x);
    cross.setAttribute("visibility", spec.kind === "lines" ? "visible" : "hidden");
    clear(rings);
    if (spec.kind === "lines") for (const se of spec.series) if (se.values[i] != null) rings.append(s("circle", { class: `dot ring fill-${se.slot}`, cx: x, cy: y(se.values[i]), r: 5 }));
    const svgW = svg.getBoundingClientRect().width || geom.W;
    const px = (x / geom.W) * svgW;
    const tw = tip.offsetWidth || 150;
    tip.style.setProperty("left", `${Math.min(Math.max(0, px + 14), Math.max(0, svgW - tw))}px`);
    if (px + 14 + tw > svgW) tip.style.setProperty("left", `${Math.max(0, px - tw - 14)}px`);
  }
  function hide() {
    tip.hidden = true;
    band.setAttribute("visibility", "hidden");
    cross.setAttribute("visibility", "hidden");
    clear(rings);
  }
  spec.days.forEach((d, i) => {
    const summary = `${d.full}: ${spec.series.map((se) => `${se.name} ${se.values[i] == null ? "none" : spec.fmt(se.values[i])}`).join(", ")}`;
    const r = s("rect", { class: "hit", x: cx(i) - slot / 2, y: m.t, width: slot, height: H - m.t - m.b, tabindex: 0, role: "img", "aria-label": summary });
    r.addEventListener("pointerenter", () => show(i));
    r.addEventListener("focus", () => show(i));
    r.addEventListener("blur", hide);
    hits.append(r);
  });
  svg.addEventListener("pointerleave", hide);
  wrap.append(tip);
}

function legend(series) {
  return h("ul", { class: "legend" }, ...series.map((se) => h("li", {}, h("span", { class: `key bg-${se.slot}` }), h("span", { text: se.name }))));
}

export function mount(root) {
  let days = 14;
  let view = "charts";
  let data = null;
  let alive = true;
  const out = h("div", { class: "stats-out" });
  const sel = h("select", { id: "range", "aria-label": "Period" }, ...[7, 14, 30, 90].map((d) => h("option", { value: String(d), text: `Last ${d} days`, selected: d === days })));
  const seg = h("div", { class: "seg", role: "group", "aria-label": "View" });
  const bC = h("button", { type: "button", text: "Charts" }), bT = h("button", { type: "button", text: "Table" });
  seg.append(bC, bT);
  root.append(h("h1", { text: "Stats" }), h("div", { class: "toolbar" }, h("label", { for: "range", class: "visually-hidden", text: "Period" }), sel, seg), out);

  const wraps = [];
  const ro = new ResizeObserver(() => { if (view === "charts") redrawCharts(); });
  ro.observe(root);

  function tile(label, value, sub) {
    return h("div", { class: "tile" }, h("div", { class: "tile-label", text: label }), h("div", { class: "tile-value", text: value }), h("div", { class: "tile-sub", text: sub }));
  }

  function specs() {
    const d = data.days;
    const dl = d.map((x) => ({ label: fmtDate(x.date), full: fmtDay(x.date) }));
    const kindsSeen = KINDS.map(([k]) => k).filter((k) => d.some((x) => (x.reports || {})[k]));
    return [
      { id: "activity", title: "Wakes and turns per day", sub: "A wake is Jarvis hearing “Hey Jarvis”; a turn is a full question and answer. Wakes with no turn were false alarms or cancelled.", kind: "columns", days: dl, fmt: fmtInt,
        series: [{ name: "Wakes", slot: 1, values: d.map((x) => x.wakes) }, { name: "Turns", slot: 2, values: d.map((x) => x.turns) }] },
      { id: "latency", title: "How long answers take", sub: "Time from the end of your question to the start of the answer. Half of answers are faster than the median; nine in ten are faster than the slow line.", kind: "lines", days: dl, fmt: fmtSec,
        axisFmt: (v) => `${(v / 1000).toFixed(v % 1000 ? 1 : 0)} s`,
        series: [{ name: "Median", slot: 1, values: d.map((x) => x.latency_ms_p50) }, { name: "Slow (p90)", slot: 2, values: d.map((x) => x.latency_ms_p90) }] },
      { id: "reports", title: "Reports per day", sub: "Each report you press on the Improve page, by what went wrong.", kind: "stacked", days: dl, fmt: fmtInt,
        series: kindsSeen.map((k) => ({ name: KIND_NAME[k], slot: KIND_SLOT[k], values: d.map((x) => (x.reports || {})[k] || 0) })) },
    ];
  }

  function redrawCharts() {
    for (const w of wraps) {
      const width = w.host.clientWidth || 600;
      clear(w.host);
      const info = chart(w.spec, width);
      w.host.append(info.svg);
      attachHover(w.host, info, w.spec);
    }
  }

  function draw() {
    clear(out); wraps.length = 0;
    bC.className = view === "charts" ? "on" : ""; bT.className = view === "table" ? "on" : "";
    bC.setAttribute("aria-pressed", String(view === "charts")); bT.setAttribute("aria-pressed", String(view === "table"));
    if (!data) { out.append(h("p", { class: "muted", text: "Loading…" })); return; }
    const sm = data.summary || {};
    out.append(h("div", { class: "tiles" },
      tile("Wakes", fmtInt(sm.wakes), `last ${days} days`), tile("Turns", fmtInt(sm.turns), `last ${days} days`),
      tile("Median answer time", fmtSec(sm.latency_ms_p50), "half are faster"), tile("Slow answers (p90)", fmtSec(sm.latency_ms_p90), "nine in ten are faster")));
    if (!data.days.length || !data.days.some((x) => x.wakes || x.turns || Object.keys(x.reports || {}).length)) {
      out.append(h("p", { class: "empty", text: "No activity recorded in this period yet. Wakes, turns and reports appear here as you use Jarvis." }));
      return;
    }
    const all = specs();
    if (view === "table") {
      const kinds = KINDS.map(([k]) => k);
      const tbl = h("table", { class: "data" }, h("caption", { class: "visually-hidden", text: "Daily numbers" }),
        h("thead", {}, h("tr", {}, ...["Date", "Wakes", "Turns", "Median", "Slow (p90)", "Reports"].map((t, i) => h("th", { scope: "col", class: i ? "num" : "", text: t })))),
        h("tbody", {}, ...data.days.map((x) => h("tr", {}, h("th", { scope: "row", text: fmtDay(x.date) }),
          h("td", { class: "num", text: fmtInt(x.wakes) }), h("td", { class: "num", text: fmtInt(x.turns) }),
          h("td", { class: "num", text: fmtSec(x.latency_ms_p50) }), h("td", { class: "num", text: fmtSec(x.latency_ms_p90) }),
          h("td", { class: "num", text: kinds.filter((k) => (x.reports || {})[k]).map((k) => `${x.reports[k]} ${KIND_NAME[k]}`).join(", ") || "–" })))));
      out.append(h("div", { class: "table-wrap" }, tbl));
      return;
    }
    for (const sp of all) {
      const host = h("div", { class: "chart-host" });
      const card = h("section", { class: "card chart" }, h("h2", { text: sp.title }), h("p", { class: "muted small", text: sp.sub }),
        sp.series.length ? legend(sp.series) : h("p", { class: "muted small", text: "No reports in this period." }), sp.series.length ? host : null);
      out.append(card);
      if (sp.series.length) wraps.push({ host, spec: sp });
    }
    redrawCharts();
  }

  async function load() {
    out.classList.add("reloading");
    try {
      data = await get(`api/stats?days=${days}`);
    } catch (e) {
      clear(out);
      out.append(h("p", { class: "error-note", text: `Couldn't load stats: ${errText(e)}` }));
      out.classList.remove("reloading");
      return;
    }
    out.classList.remove("reloading");
    if (alive) draw();
  }
  sel.addEventListener("change", () => { days = Number(sel.value); load(); });
  bC.addEventListener("click", () => { view = "charts"; draw(); });
  bT.addEventListener("click", () => { view = "table"; draw(); });
  draw(); load();
  return () => { alive = false; ro.disconnect(); };
}
