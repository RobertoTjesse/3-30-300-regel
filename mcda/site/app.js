// app.js — the afwegingskaart: controls, map, ranking. The arithmetic is in
// mcda.js; the sensitivity runs in worker.js. When served by the local server
// (mcda/local/server.py), local.js extends this through window.APP.
/* global MCDA, maplibregl, pmtiles */
"use strict";

// ---------------------------------------------------------------- constants
const CRITERIA = {
  bomen: { badge: "3", cls: "b", kleur: "#2f7d32", titel: "Bomen in zicht",
           sub: "Zichtbare bomen vanuit de gevel, binnen 30 m",
           doel: { min: 1, max: 10, step: 1, fmt: (v) => `${v} ${v === 1 ? "boom" : "bomen"}` } },
  kroon: { badge: "30", cls: "k", kleur: "#0f766e", titel: "Kroonbedekking",
           sub: "Boomkroon binnen 500 m van het gebouw",
           doel: { min: 5, max: 50, step: 5, fmt: (v) => `${v}%` } },
  afstand: { badge: "300", cls: "a", kleur: "#1d4ed8", titel: "Groen dichtbij",
             sub: "Looproute naar de ingang van openbaar groen",
             doel: { min: 100, max: 1000, step: 25, fmt: (v) => `${fmtInt(v)} m` } },
};
const LEVELS = [
  { key: "gemeenten", naam: "Gemeenten", enkel: "gemeente", min: 0, max: 9 },
  { key: "wijken", naam: "Wijken", enkel: "wijk", min: 9, max: 11 },
  { key: "buurten", naam: "Buurten", enkel: "buurt", min: 11, max: 13 },
];
const BUILDING_ZOOM = 13;
const RAMP_SCORE = [[0, "#ffffcc"], [25, "#c2e699"], [50, "#78c679"], [75, "#31a354"], [100, "#006837"]];
const CLASSES = RAMP_SCORE.map(([, c]) => c);   // the relative scale: five quintile classes
const STEPS_ALLE = [[0, "#f1eef6"], [1, "#bdc9e1"], [2.5, "#74a9cf"], [5, "#2b8cbe"], [10, "#045a8d"]];
const RAMP_KANS = [[0, "#fef0d9"], [0.25, "#fdcc8a"], [0.5, "#fc8d59"], [0.75, "#e34a33"], [1, "#b30000"]];
const RAMP_DIFF = [[-20, "#b2182b"], [-10, "#ef8a62"], [0, "#f7f7f7"], [10, "#67a9cf"], [20, "#2166ac"]];
const NO_DIFF = -999;
const PRESETS = [
  { naam: "Gelijke weging", p: {} },
  { naam: "Strikt volgens de regel", p: { vf: { bomen: "bin", kroon: "bin", afstand: "bin" }, methode: "min" } },
  { naam: "Bomen in zicht voorop", p: { w: { bomen: 3, kroon: 1, afstand: 1 } } },
  { naam: "Groen dichtbij voorop", p: { w: { bomen: 1, kroon: 1, afstand: 3 } } },
  { naam: "Zonder de 30", p: { w: { bomen: 1, kroon: 0, afstand: 1 } } },
];
const MIN_WONINGEN = 25;
const PAGE = 100;

// ---------------------------------------------------------------- helpers
const nl = new Intl.NumberFormat("nl-NL");
function fmtInt(v) { return nl.format(Math.round(v)); }
function fmtPct(v, d = 0) { return v == null || isNaN(v) ? "–" : `${v.toFixed(d).replace(".", ",")}%`; }
function fmtScore(v) { return v == null || isNaN(v) ? "–" : Math.round(v).toString(); }
const $ = (s, el = document) => el.querySelector(s);
function clone(o) { return JSON.parse(JSON.stringify(o)); }
function merge(base, extra) {
  const out = clone(base);
  for (const k in extra) out[k] = typeof extra[k] === "object" ? Object.assign(out[k], extra[k]) : extra[k];
  return out;
}
function rampColour(ramp, v) {
  if (v == null || isNaN(v)) return "#e2e8f0";
  for (let i = 1; i < ramp.length; i++) {
    if (v <= ramp[i][0]) {
      const [x0, c0] = ramp[i - 1], [x1, c1] = ramp[i], t = (v - x0) / (x1 - x0);
      const a = parseInt(c0.slice(1), 16), b = parseInt(c1.slice(1), 16);
      const ch = (s) => Math.round(((a >> s) & 255) + t * (((b >> s) & 255) - ((a >> s) & 255)));
      return `rgb(${ch(16)},${ch(8)},${ch(0)})`;
    }
  }
  return ramp[ramp.length - 1][1];
}
function textOn(rgb) {   // dark or light text on a fill
  const m = rgb.match(/\d+/g) || [255, 255, 255];
  const [r, g, b] = rgb.startsWith("#") ? [1, 3, 5].map((i) => parseInt(rgb.slice(i, i + 2), 16)) : m.map(Number);
  return 0.299 * r + 0.587 * g + 0.114 * b > 150 ? "#0f172a" : "#ffffff";
}
let toastTimer;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 2200);
}

// ---------------------------------------------------------------- state
const state = {
  params: MCDA.defaults(),
  view: "score",
  scale: "rel",             // "rel" (quintiles of the areas shown) or "vast" (0..100)
  breaks: {},               // per level: the four quintile breaks (scale "rel")
  rankLevel: "buurten",
  sort: { key: "score", dir: 1 },
  query: "",
  minFilter: true,
  shown: PAGE,
  selected: null,           // {level, i} or {gebouw: props}
  robust: null,             // {level, kans, lo, hi, med, samples, eligible}
  afstandOverride: null,    // Uint16Array (local what-if) or null
  sums: null,               // per level: sum fields (MCDA.rollup)
  diff: null,               // local.js: per level, score now minus a saved scenario
  diffLabel: "",
};
let data, gebieden, map, worker;
const hooks = [];           // local.js: called after every recompute

function readUrl() {
  const q = new URLSearchParams(location.search), p = state.params;
  const nums = (s) => (s || "").split(",").map(Number);
  const w = nums(q.get("w")), d = nums(q.get("d")), f = (q.get("f") || "").split(",");
  MCDA.KEYS.forEach((k, i) => {
    if (w.length === 3 && w[i] >= 0 && w[i] <= 5) p.w[k] = w[i];
    if (d.length === 3 && d[i] >= CRITERIA[k].doel.min && d[i] <= CRITERIA[k].doel.max) p.doel[k] = d[i];
    if (f.length === 3 && (f[i] === "bin" || f[i] === "lin")) p.vf[k] = f[i];
  });
  const z = Number(q.get("z"));
  if (z >= 400 && z <= 1275) p.nul = z;
  if (["som", "product", "min"].includes(q.get("m"))) p.methode = q.get("m");
  if (["gemeenten", "wijken", "buurten"].includes(q.get("r"))) state.rankLevel = q.get("r");
  if (q.get("s") === "vast" || q.get("s") === "rel") state.scale = q.get("s");
}
function writeUrl() {
  const p = state.params, u = new URL(location.href);
  u.searchParams.set("w", MCDA.KEYS.map((k) => p.w[k]).join(","));
  u.searchParams.set("f", MCDA.KEYS.map((k) => p.vf[k]).join(","));
  u.searchParams.set("d", MCDA.KEYS.map((k) => p.doel[k]).join(","));
  u.searchParams.set("z", p.nul);
  u.searchParams.set("m", p.methode);
  u.searchParams.set("r", state.rankLevel);
  u.searchParams.set("s", state.scale);
  history.replaceState(null, "", u);
}

// ---------------------------------------------------------------- controls
function buildCriteria() {
  const root = $("#criteria");
  for (const k of MCDA.KEYS) {
    const c = CRITERIA[k];
    const el = document.createElement("div");
    el.className = "crit";
    el.dataset.k = k;
    el.innerHTML = `
      <div class="crit-head"><span class="badge ${c.cls}" aria-hidden="true">${c.badge}</span>
        <div><h3>${c.titel}</h3><p>${c.sub}</p></div>
        <output class="share" title="Aandeel in het totaal"></output></div>
      <label class="field"><span>Gewicht</span>
        <input type="range" min="0" max="5" step="0.5" data-role="w" aria-label="Gewicht ${c.titel}"><output data-out="w"></output></label>
      <div class="field"><span>Scoren</span>
        <div class="seg" role="group" aria-label="Scoren ${c.titel}">
          <button type="button" data-vf="bin">Ja/nee</button><button type="button" data-vf="lin">Geleidelijk</button></div></div>
      <label class="field"><span>Doel</span>
        <input type="range" min="${c.doel.min}" max="${c.doel.max}" step="${c.doel.step}" data-role="doel" aria-label="Doel ${c.titel}"><output data-out="doel"></output></label>
      ${k === "afstand" ? `<label class="field" data-role="nulrow"><span>Score 0 vanaf</span>
        <input type="range" min="400" max="1275" step="25" data-role="nul" aria-label="Afstand met score 0"><output data-out="nul"></output></label>` : ""}
      <p class="crit-stat" data-out="stat"></p>`;
    root.appendChild(el);
    $('[data-role="w"]', el).addEventListener("input", (e) => { state.params.w[k] = Number(e.target.value); changed(); });
    $('[data-role="doel"]', el).addEventListener("input", (e) => { state.params.doel[k] = Number(e.target.value); changed(); });
    el.querySelectorAll("[data-vf]").forEach((b) => b.addEventListener("click", () => { state.params.vf[k] = b.dataset.vf; changed(); }));
    const nul = $('[data-role="nul"]', el);
    if (nul) nul.addEventListener("input", (e) => { state.params.nul = Number(e.target.value); changed(); });
  }
  document.querySelectorAll('input[name="methode"]').forEach((r) =>
    r.addEventListener("change", () => { state.params.methode = r.value; changed(); }));
  $("#btn-reset").addEventListener("click", () => { state.params = MCDA.defaults(); changed(); });

  const pr = $("#presets");
  PRESETS.forEach((preset) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "preset";
    b.textContent = preset.naam;
    b.addEventListener("click", () => { state.params = merge(MCDA.defaults(), preset.p); changed(); });
    pr.appendChild(b);
  });
}

function syncControls() {
  const p = state.params, tot = MCDA.KEYS.reduce((a, k) => a + p.w[k], 0);
  for (const k of MCDA.KEYS) {
    const el = $(`.crit[data-k="${k}"]`), c = CRITERIA[k];
    el.dataset.off = String(p.w[k] === 0);
    $(".share", el).textContent = tot ? `${Math.round(100 * p.w[k] / tot)}%` : "–";
    $('[data-role="w"]', el).value = p.w[k];
    $('[data-out="w"]', el).textContent = p.w[k].toString().replace(".", ",");
    $('[data-role="doel"]', el).value = p.doel[k];
    $('[data-out="doel"]', el).textContent = c.doel.fmt(p.doel[k]);
    el.querySelectorAll("[data-vf]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.vf === p.vf[k])));
    if (k === "afstand") {
      const row = $('[data-role="nulrow"]', el);
      row.hidden = p.vf.afstand !== "lin";
      $('[data-role="nul"]', el).value = p.nul;
      $('[data-out="nul"]', el).textContent = `${fmtInt(Math.max(p.nul, p.doel.afstand + 25))} m`;
    }
  }
  document.querySelectorAll('input[name="methode"]').forEach((r) => { r.checked = r.value === p.methode; });
  const now = JSON.stringify(p);
  document.querySelectorAll("#presets .preset").forEach((b, i) =>
    b.setAttribute("aria-pressed", String(JSON.stringify(merge(MCDA.defaults(), PRESETS[i].p)) === now)));
}

// ---------------------------------------------------------------- compute
let pending = false;
function changed() {
  if (state.robust) {           // the sensitivity belongs to the old weights
    state.robust = null;
    $("#rb-note").textContent = "De afweging is veranderd: bereken de zekerheid opnieuw.";
    if (state.view === "robuust") setView("score");
  }
  syncControls();
  if (pending) return;
  pending = true;
  requestAnimationFrame(() => { pending = false; recompute(); });
}

function recompute() {
  const buurtSums = MCDA.aggregate(data, gebieden.buurten, state.params, state.afstandOverride);
  state.sums = {};
  for (const lv of LEVELS) state.sums[lv.key] = MCDA.rollup(buurtSums, gebieden, lv.key);
  // province: everything together
  const prov = {};
  for (const f in buurtSums) prov[f] = new Float64Array([buurtSums[f].reduce((a, b) => a + b, 0)]);
  const pa = MCDA.area(prov, 0);
  $("#st-score").textContent = fmtScore(pa.score);
  $("#st-alle").textContent = fmtPct(pa.alle, 1);
  $("#st-won").textContent = fmtInt(pa.woningen);
  for (const k of MCDA.KEYS) {
    $(`.crit[data-k="${k}"] [data-out="stat"]`).textContent =
      `Zuid-Holland: ${fmtPct(pa["m" + k], 1)} haalt het doel · gemiddelde score ${fmtScore(pa[k])}`;
  }
  paintAreas();
  paintBuildings();
  renderLegend();
  renderRanking();
  renderDetail();
  writeUrl();
  hooks.forEach((h) => h(state));
}

// ---------------------------------------------------------------- map
function initMap() {
  const protocol = new pmtiles.Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);
  const tilesUrl = new URL("data/kaart.pmtiles", location.href).href;
  map = new maplibregl.Map({
    container: "map",
    hash: "kaart",
    center: [4.47, 51.98], zoom: 8.7, minZoom: 7, maxZoom: 18,
    maxBounds: [[3.2, 51.4], [5.6, 52.6]],
    attributionControl: { compact: true },
    style: {
      version: 8,
      sources: {
        brt: { type: "raster", tileSize: 256, maxzoom: 19,
               tiles: ["https://service.pdok.nl/brt/achtergrondkaart/wmts/v2_0/grijs/EPSG:3857/{z}/{x}/{y}.png"],
               attribution: 'Achtergrond: <a href="https://www.pdok.nl">PDOK</a> BRT (Kadaster)' },
        kaart: { type: "vector", url: `pmtiles://${tilesUrl}`,
                 promoteId: { gemeenten: "id", wijken: "id", buurten: "id" },
                 attribution: "AHN5, BKB 2024, BAG, CBS, OSM, bomen: NEO" },
      },
      layers: [{ id: "brt", type: "raster", source: "brt", paint: { "raster-opacity": 0.85 } }],
    },
  });
  map.on("error", (e) => console.error("kaart:", e.error && e.error.message));
  if (location.search.includes("debug")) {
    map.on("idle", () => console.log("idle", map.getZoom().toFixed(1),
      LEVELS.map((l) => `${l.key}=${map.queryRenderedFeatures({ layers: [`${l.key}-fill`] }).length}`).join(" ")));
  }
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
  return new Promise((resolve) => map.on("load", () => { addLayers(); resolve(); }));
}

function addLayers() {
  for (const lv of LEVELS) {
    const maxz = lv.key === "buurten" ? BUILDING_ZOOM : lv.max;
    map.addLayer({ id: `${lv.key}-fill`, type: "fill", source: "kaart", "source-layer": lv.key,
                   minzoom: lv.min, maxzoom: maxz, paint: { "fill-color": "#e2e8f0", "fill-opacity": 0.78 } });
    map.addLayer({ id: `${lv.key}-line`, type: "line", source: "kaart", "source-layer": lv.key,
                   minzoom: lv.min, maxzoom: maxz,
                   paint: { "line-color": "#ffffff", "line-width": lv.key === "gemeenten" ? 1.2 : 0.7, "line-opacity": 0.9 } });
  }
  map.addLayer({ id: "gebouwen", type: "fill", source: "kaart", "source-layer": "gebouwen", minzoom: BUILDING_ZOOM,
                 paint: { "fill-color": "#cbd5e1", "fill-outline-color": "rgba(15,23,42,0.25)", "fill-opacity": 0.9 } });
  map.addLayer({ id: "buurten-rand", type: "line", source: "kaart", "source-layer": "buurten", minzoom: BUILDING_ZOOM,
                 paint: { "line-color": "#0f172a", "line-width": 1, "line-dasharray": [3, 2], "line-opacity": 0.55 } });
  for (const lv of LEVELS) {
    map.addLayer({ id: `${lv.key}-sel`, type: "line", source: "kaart", "source-layer": lv.key,
                   filter: ["==", ["get", "id"], -1],
                   paint: { "line-color": "#0369a1", "line-width": 3 } });
  }
  map.on("zoom", updateLevelTag);
  updateLevelTag();
  bindMapEvents();
}

function currentLevel() {
  const z = map.getZoom();
  if (z >= BUILDING_ZOOM) return "gebouwen";
  return LEVELS.find((l) => z >= l.min && z < l.max)?.key || "buurten";
}
function updateLevelTag() {
  const lv = currentLevel();
  $("#level-tag").textContent = lv === "gebouwen" ? "Gebouwen" : LEVELS.find((l) => l.key === lv).naam;
}

function interp(ramp, input) {
  return ["interpolate", ["linear"], input, ...ramp.flat()];
}
function stepExpr(steps, input) {
  const out = ["step", input, steps[0][1]];
  for (let i = 1; i < steps.length; i++) out.push(steps[i][0], steps[i][1]);
  return out;
}

// Quintile breaks of the area scores of one level (areas with 25+ homes)
function quintiles(level) {
  const sums = state.sums[level], vals = [];
  for (let i = 0; i < sums.n.length; i++) if (sums.n[i] >= MIN_WONINGEN && sums.nScore[i]) vals.push(100 * sums.som[i] / sums.nScore[i]);
  vals.sort((a, b) => a - b);
  // Rounded to 0.1 and without repeats: when many areas share a score (e.g.
  // 0 under the strict rule) quintiles coincide and there are fewer classes
  const raw = [0.2, 0.4, 0.6, 0.8].map((q) => Math.round(10 * (vals[Math.min(vals.length - 1, Math.floor(q * vals.length))] ?? 0)) / 10);
  return raw.filter((b, i) => b > 0 && (i === 0 || b > raw[i - 1]));
}
function classColours(n) {   // n classes, spread over the five colours
  return Array.from({ length: n }, (_, i) => CLASSES[n === 1 ? 4 : Math.round(i * 4 / (n - 1))]);
}
function scoreColourExpr(level, input) {
  if (state.scale !== "rel") return interp(RAMP_SCORE, input);
  const b = state.breaks[level], c = classColours(b.length + 1), out = ["step", input, c[0]];
  b.forEach((x, i) => out.push(x, c[i + 1]));
  return b.length ? out : c[0];
}
function scoreColour(level, v) {
  if (state.scale !== "rel" || v == null || isNaN(v)) return rampColour(RAMP_SCORE, v);
  const b = state.breaks[level];
  return classColours(b.length + 1)[b.filter((x) => v >= x).length];
}

// The difference scale follows the largest difference of a level (98th
// percentile), rounded up to 1, 2, 5, 10, 20 or 50 points
function diffMax(level) {
  const d = state.diff && state.diff[level];
  if (!d) return 20;
  const a = Array.from(d).filter((x) => !isNaN(x)).map(Math.abs).sort((x, y) => x - y);
  const m = a.length ? a[Math.floor(0.98 * (a.length - 1))] : 0;
  return [1, 2, 5, 10, 20, 50].find((s) => s >= m) || 50;
}
function diffRamp(level) {
  const m = diffMax(level);
  return RAMP_DIFF.map(([x, c]) => [x * m / 20, c]);
}

function paintAreas() {
  const v = state.view;
  for (const lv of LEVELS) state.breaks[lv.key] = quintiles(lv.key);
  for (const lv of LEVELS) {
    const sums = state.sums[lv.key], n = sums.n.length;
    const rb = state.robust && state.robust.level === lv.key ? state.robust : null;
    for (let i = 0; i < n; i++) {
      const a = MCDA.area(sums, i);
      map.setFeatureState({ source: "kaart", sourceLayer: lv.key, id: i }, {
        s: a && a.score != null ? a.score : -1,
        a: a ? a.alle : -1,
        r: rb && !isNaN(rb.kans[i]) ? rb.kans[i] : -1,
        d: state.diff && state.diff[lv.key] && !isNaN(state.diff[lv.key][i]) ? state.diff[lv.key][i] : NO_DIFF,
      });
    }
    const key = { score: "s", alle: "a", robuust: "r", verschil: "d" }[v];
    const val = ["coalesce", ["feature-state", key], v === "verschil" ? NO_DIFF : -1];
    const none = v === "verschil" ? ["<=", val, NO_DIFF] : ["<", val, 0];
    const colour = v === "score" ? scoreColourExpr(lv.key, val) : v === "alle" ? stepExpr(STEPS_ALLE, val)
      : v === "robuust" ? interp(RAMP_KANS, val) : interp(diffRamp(lv.key), val);
    map.setPaintProperty(`${lv.key}-fill`, "fill-color", ["case", none, "#e2e8f0", colour]);
    map.setPaintProperty(`${lv.key}-fill`, "fill-opacity", ["case", none, 0.35, 0.8]);
  }
}

// Building colour from its own properties, with the same value functions and
// method as mcda.js (the 300 as the median walking distance of its homes)
function buildingScoreExpr() {
  const p = state.params;
  const has = (f) => ["has", f];
  const lin = (x, doel) => ["min", 1, ["/", x, doel]];
  const bin = (cond) => ["case", cond, 1, 0];
  const bomen = ["to-number", ["get", "bomen"]], kroon = ["/", ["to-number", ["get", "kroon"]], 10];
  const afst = ["to-number", ["get", "afstand"]];
  const sb = p.vf.bomen === "bin" ? bin([">=", bomen, p.doel.bomen]) : lin(bomen, p.doel.bomen);
  const sk = p.vf.kroon === "bin" ? bin([">=", kroon, p.doel.kroon]) : lin(kroon, p.doel.kroon);
  const nul = Math.max(p.nul, p.doel.afstand + 1);
  const saRaw = p.vf.afstand === "bin" ? bin(["<=", afst, p.doel.afstand])
    : ["max", 0, ["min", 1, ["/", ["-", nul, afst], nul - p.doel.afstand]]];
  const sa = ["case", has("afstand"), saRaw, 0];
  const wb = ["case", has("bomen"), p.w.bomen, 0], wk = ["case", has("kroon"), p.w.kroon, 0], wa = p.w.afstand;
  const tot = ["+", wb, wk, wa];
  const sbS = ["case", has("bomen"), sb, 0], skS = ["case", has("kroon"), sk, 0];
  let score;
  if (p.methode === "som") {
    score = ["/", ["+", ["*", wb, sbS], ["*", wk, skS], ["*", wa, sa]], ["max", tot, 1e-9]];
  } else if (p.methode === "product") {
    const pw = (s, w) => ["^", ["max", s, 0], ["/", w, ["max", tot, 1e-9]]];
    score = ["*", pw(sbS, wb), pw(skS, wk), pw(sa, wa)];
  } else {
    const wmax = Math.max(p.w.bomen, p.w.kroon, p.w.afstand) || 1;
    const term = (s, w, present) => ["case", present, ["max", 1 - w / wmax, s], 1];
    score = ["min", term(sbS, p.w.bomen, ["all", has("bomen"), p.w.bomen > 0]),
                    term(skS, p.w.kroon, ["all", has("kroon"), p.w.kroon > 0]),
                    term(sa, p.w.afstand, p.w.afstand > 0)];
  }
  return { score: ["*", 100, score], tot };
}
function paintBuildings() {
  const p = state.params;
  if (state.view === "robuust" || state.view === "verschil") {
    map.setPaintProperty("gebouwen", "fill-color", "#cbd5e1");
    return;
  }
  if (state.view === "alle") {
    const bomen = ["to-number", ["get", "bomen"], -1], kroon = ["/", ["to-number", ["get", "kroon"], -10], 10];
    const afst = ["to-number", ["get", "afstand"], 99999];
    map.setPaintProperty("gebouwen", "fill-color", ["case",
      ["all", [">=", bomen, p.doel.bomen], [">=", kroon, p.doel.kroon], ["<=", afst, p.doel.afstand]], "#045a8d", "#f1eef6"]);
    return;
  }
  const { score, tot } = buildingScoreExpr();
  map.setPaintProperty("gebouwen", "fill-color", ["case", ["<=", tot, 0], "#e2e8f0", scoreColourExpr("buurten", score)]);
}

function setView(v) {
  state.view = v;
  document.querySelectorAll(".viewswitch button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.view === v)));
  if (state.sums) { paintAreas(); paintBuildings(); renderLegend(); }
}

function renderLegend() {
  const el = $("#legend"), v = state.view;
  let html;
  if (v === "score") {
    const lv = map && currentLevel() !== "gebouwen" ? currentLevel() : "buurten";
    const scaleSwitch = `<div class="seg legend-seg" role="group" aria-label="Kleurschaal">
      <button type="button" data-scale="vast" aria-pressed="${state.scale === "vast"}" title="Vaste schaal van 0 tot 100">Vast</button>
      <button type="button" data-scale="rel" aria-pressed="${state.scale === "rel"}">Relatief</button></div>`;
    if (state.scale === "rel") {
      const b = state.breaks[lv], f = (x) => (Number.isInteger(x) ? x : x.toFixed(1).replace(".", ","));
      const labels = b.length ? [`lager dan ${f(b[0])}`, ...b.slice(1).map((x, i) => `${f(b[i])} – ${f(x)}`), `${f(b[b.length - 1])} of hoger`]
        : ["alle gebieden 0"];
      html = `<h3>Score per ${currentLevelName()} ${scaleSwitch}</h3><div class="swatches">${classColours(labels.length).map((c, i) =>
        `<div class="sw"><i style="background:${c}"></i>${labels[i]}</div>`).join("")}</div>
        <p class="note">${b.length === 4 ? "Vijf even grote groepen" : "Groepen naar de kwintielen van"} ${LEVELS.find((l) => l.key === lv).naam.toLowerCase()} met 25+ woningen${b.length < 4 ? "; veel gebieden hebben dezelfde score" : ""}.</p>`;
    } else {
      const grad = RAMP_SCORE.map(([x, c]) => `${c} ${x}%`).join(",");
      html = `<h3>Score per ${currentLevelName()} ${scaleSwitch}</h3>
        <div class="ramp" style="background:linear-gradient(90deg,${grad})" role="img" aria-label="Kleurschaal van 0 (licht) tot 100 (donkergroen)"></div>
        <div class="ticks"><span>0</span><span>25</span><span>50</span><span>75</span><span>100</span></div>
        <p class="note">0 = slechtst gemeten, 100 = alle doelen gehaald. Gebouwen vanaf zoom 13.</p>`;
    }
  } else if (v === "alle") {
    const labels = ["minder dan 1%", "1 – 2,5%", "2,5 – 5%", "5 – 10%", "10% of meer"];
    html = `<h3>Woningen die alle drie doelen halen</h3><div class="swatches">${STEPS_ALLE.map(([, c], i) =>
      `<div class="sw"><i style="background:${c}"></i>${labels[i]}</div>`).join("")}</div>
      <p class="note">Altijd ja/nee op de ingestelde doelen, zonder gewichten.</p>`;
  } else if (v === "verschil") {
    const grad = RAMP_DIFF.map(([x, c]) => `${c} ${(x + 20) * 2.5}%`).join(",");
    const m = diffMax(map && currentLevel() !== "gebouwen" ? currentLevel() : "buurten");
    html = `<h3>Score nu min ${state.diffLabel}</h3>
      <div class="ramp" style="background:linear-gradient(90deg,${grad})" role="img" aria-label="Rood is lager dan het scenario, blauw hoger"></div>
      <div class="ticks"><span>−${m}</span><span>0</span><span>+${m}</span></div>
      <p class="note">Blauw: de huidige afweging scoort hoger. Rood: lager.</p>`;
  } else {
    const grad = RAMP_KANS.map(([x, c]) => `${c} ${x * 100}%`).join(",");
    const rb = state.robust;
    html = `<h3>Kans bij de slechtste 20%</h3>
      <div class="ramp" style="background:linear-gradient(90deg,${grad})" role="img" aria-label="Kleurschaal van 0% tot 100%"></div>
      <div class="ticks"><span>0%</span><span>50%</span><span>100%</span></div>
      <p class="note">${rb ? `${rb.samples} rekenrondes, alleen ${LEVELS.find((l) => l.key === rb.level).naam.toLowerCase()} met 25+ woningen.` : ""}</p>`;
  }
  el.innerHTML = html;
  el.querySelectorAll("[data-scale]").forEach((b) => b.addEventListener("click", () => {
    state.scale = b.dataset.scale;
    paintAreas(); paintBuildings(); renderLegend(); renderRanking(); renderDetail(); writeUrl();
  }));
}
function currentLevelName() {
  const lv = map ? currentLevel() : "buurten";
  return lv === "gebouwen" ? "gebouw" : LEVELS.find((l) => l.key === lv).enkel;
}

function bindMapEvents() {
  const fills = [...LEVELS.map((l) => `${l.key}-fill`), "gebouwen"];
  const tip = $("#tooltip");
  map.on("mousemove", (e) => {
    const f = map.queryRenderedFeatures(e.point, { layers: fills })[0];
    map.getCanvas().style.cursor = f ? "pointer" : "";
    if (!f) { tip.style.display = "none"; return; }
    let text;
    if (f.layer.id === "gebouwen") {
      text = `Gebouw · ${f.properties.woningen} ${f.properties.woningen === 1 ? "woning" : "woningen"}`;
    } else {
      const level = f.layer.id.replace("-fill", ""), a = MCDA.area(state.sums[level], f.properties.id);
      text = `${f.properties.naam} · score ${fmtScore(a?.score)}`;
    }
    tip.textContent = text;
    tip.style.left = `${e.point.x}px`;
    tip.style.top = `${e.point.y}px`;
    tip.style.display = "block";
  });
  map.on("mouseout", () => { tip.style.display = "none"; });
  map.on("click", (e) => {
    if (APP.clickHandler && APP.clickHandler(e)) return;       // local.js drawing tools
    const f = map.queryRenderedFeatures(e.point, { layers: fills })[0];
    if (!f) return;
    if (f.layer.id === "gebouwen") select({ gebouw: f.properties });
    else select({ level: f.layer.id.replace("-fill", ""), i: f.properties.id });
  });
  map.on("zoomend", renderLegend);
}

// ---------------------------------------------------------------- selection
function areaName(level, i) {
  return gebieden[level][i][1];
}
function areaSub(level, i) {
  if (level === "gemeenten") return "gemeente";
  const row = gebieden[level][i], gem = gebieden.gemeenten[row[2]][1];
  if (level === "wijken") return `wijk in ${gem}`;
  const wijk = row[3] >= 0 ? gebieden.wijken[row[3]][1] : null;
  return `buurt in ${wijk ? wijk + ", " : ""}${gem}`;
}
function select(sel, fly) {
  state.selected = sel;
  for (const lv of LEVELS) map.setFilter(`${lv.key}-sel`, ["==", ["get", "id"], sel && sel.level === lv.key ? sel.i : -1]);
  if (fly && sel && sel.level) {
    const bb = gebieden.bbox[sel.level][sel.i];
    if (bb) {
      const lv = LEVELS.find((l) => l.key === sel.level);
      map.fitBounds([[bb[0], bb[1]], [bb[2], bb[3]]], { padding: 60, maxZoom: lv.max - 0.2, minZoom: lv.min + 0.01 });
    }
  }
  renderDetail();
  renderRanking();
}

function bar(label, v, kleur) {
  return `<div class="bar"><span>${label}</span><div class="track"><i style="width:${v == null ? 0 : v}%;background:${kleur}"></i></div>
    <output>${fmtScore(v)}</output></div>`;
}
function renderDetail() {
  const el = $("#detail"), sel = state.selected;
  if (!sel) { el.innerHTML = '<p class="empty">Klik op een gebied op de kaart of in de ranglijst.</p>'; return; }
  if (sel.gebouw) {
    const g = sel.gebouw, b = gebieden.buurten[g.buurt];
    const kroon = g.kroon == null ? null : g.kroon / 10;
    el.innerHTML = `<h2>Gebouw</h2><p class="sub">${g.woningen} ${g.woningen === 1 ? "woning" : "woningen"} · ${b ? b[1] : ""}</p>
      <dl class="kv">
        <dt>Bomen in zicht</dt><dd>${g.bomen ?? "–"}</dd>
        <dt>Kroon binnen 500 m</dt><dd>${kroon == null ? "–" : fmtPct(kroon, 1)}</dd>
        <dt>Loopafstand tot groen</dt><dd>${g.afstand == null ? "meer dan 1.275 m" : `${fmtInt(g.afstand)} m`}</dd>
      </dl>
      <p class="hint">De afstand is de mediaan van de woningen in het gebouw.</p>
      ${b ? `<button class="btn" type="button" id="to-buurt" style="margin-top:10px">Toon de buurt</button>` : ""}`;
    const tb = $("#to-buurt");
    if (tb) tb.onclick = () => select({ level: "buurten", i: g.buurt });
    return;
  }
  const a = MCDA.area(state.sums[sel.level], sel.i);
  const ranked = rankedRows(sel.level), pos = ranked.findIndex((r) => r.i === sel.i);
  const rb = state.robust && state.robust.level === sel.level ? state.robust : null;
  const kleur = scoreColour(sel.level, a?.score);
  el.innerHTML = `<h2>${areaName(sel.level, sel.i)}</h2><p class="sub">${areaSub(sel.level, sel.i)}</p>
    ${a ? `<div class="big"><b class="pill" style="background:${kleur};color:${textOn(kleur)}">${fmtScore(a.score)}</b>
      <span>${pos >= 0 ? `plaats ${pos + 1} van ${ranked.length} (1 = laagste score)` : "te weinig woningen voor de ranglijst"}</span></div>
    <div class="bars">
      ${bar("Bomen in zicht", a.bomen, CRITERIA.bomen.kleur)}
      ${bar("Kroonbedekking", a.kroon, CRITERIA.kroon.kleur)}
      ${bar("Groen dichtbij", a.afstand, CRITERIA.afstand.kleur)}
    </div>
    <dl class="kv">
      <dt>Woningen</dt><dd>${fmtInt(a.woningen)}</dd>
      <dt>Haalt het doel van de 3</dt><dd>${fmtPct(a.mbomen)}</dd>
      <dt>Haalt het doel van de 30</dt><dd>${fmtPct(a.mkroon)}</dd>
      <dt>Haalt het doel van de 300</dt><dd>${fmtPct(a.mafstand)}</dd>
      <dt>Haalt alle drie</dt><dd>${fmtPct(a.alle, 1)}</dd>
      ${rb && !isNaN(rb.kans[sel.i]) ? `<dt>Bij de slechtste 20%</dt><dd>${Math.round(rb.kans[sel.i] * rb.samples)} van ${rb.samples} keer</dd>
        <dt>Plaats in de rekenrondes</dt><dd>${rb.lo[sel.i]} – ${rb.hi[sel.i]}</dd>` : ""}
    </dl>` : '<p class="hint">Geen woningen in dit gebied.</p>'}`;
}

// ---------------------------------------------------------------- ranking
function rankedRows(level) {
  const sums = state.sums[level], out = [];
  for (let i = 0; i < sums.n.length; i++) {
    if (state.minFilter ? sums.n[i] < MIN_WONINGEN : !sums.n[i]) continue;
    const a = MCDA.area(sums, i);
    if (a.score == null) continue;
    out.push({ i, a });
  }
  out.sort((x, y) => x.a.score - y.a.score);
  return out;
}
function renderRanking() {
  const level = state.rankLevel, rb = state.robust && state.robust.level === level ? state.robust : null;
  document.querySelectorAll("#rank-level button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.level === level)));
  $("#th-kans").hidden = !rb;
  let rows = rankedRows(level).map((r, k) => ({ ...r, plaats: k + 1, naam: areaName(level, r.i), sub: areaSub(level, r.i),
                                                kans: rb ? rb.kans[r.i] : NaN }));
  const q = state.query.trim().toLowerCase();
  if (q) rows = rows.filter((r) => r.naam.toLowerCase().includes(q) || r.sub.toLowerCase().includes(q));
  const { key, dir } = state.sort;
  const val = (r) => key === "naam" ? r.naam : key === "woningen" ? r.a.woningen : key === "alle" ? r.a.alle
    : key === "kans" ? (isNaN(r.kans) ? -1 : r.kans) : r.a.score;
  rows.sort((x, y) => { const a = val(x), b = val(y); return (a < b ? -1 : a > b ? 1 : 0) * dir; });
  document.querySelectorAll("table.rank th button").forEach((b) => {
    const th = b.closest("th");
    th.setAttribute("aria-sort", b.dataset.sort === key ? (dir > 0 ? "ascending" : "descending") : "none");
  });
  const sel = state.selected && state.selected.level === level ? state.selected.i : -1;
  const body = $("#rank-body");
  body.innerHTML = rows.slice(0, state.shown).map((r) => {
    const c = scoreColour(level, r.a.score);
    return `<tr data-i="${r.i}" tabindex="0" aria-selected="${r.i === sel}">
      <td class="n">${r.plaats}</td>
      <td class="nm">${r.naam}<small>${level === "gemeenten" ? "" : r.sub.replace(/^(wijk|buurt) in /, "")}</small></td>
      <td class="n">${fmtInt(r.a.woningen)}</td>
      <td class="n"><span class="pill" style="background:${c};color:${textOn(c)}">${fmtScore(r.a.score)}</span></td>
      <td class="n">${fmtPct(r.a.alle, 1)}</td>
      ${rb ? `<td class="n">${isNaN(r.kans) ? "–" : `${Math.round(r.kans * 100)}%`}</td>` : ""}</tr>`;
  }).join("");
  $("#rank-more").hidden = rows.length <= state.shown;
  $("#rank-caption").textContent = `${rows.length} ${LEVELS.find((l) => l.key === level).naam.toLowerCase()}`;
}
function bindRanking() {
  $("#rank-body").addEventListener("click", (e) => {
    const tr = e.target.closest("tr");
    if (tr) select({ level: state.rankLevel, i: Number(tr.dataset.i) }, true);
  });
  $("#rank-body").addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      const tr = e.target.closest("tr");
      if (tr) { e.preventDefault(); select({ level: state.rankLevel, i: Number(tr.dataset.i) }, true); }
    }
  });
  document.querySelectorAll("#rank-level button").forEach((b) => b.addEventListener("click", () => {
    state.rankLevel = b.dataset.level;
    state.shown = PAGE;
    renderRanking();
    writeUrl();
  }));
  document.querySelectorAll("table.rank th button").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.sort;
    state.sort = { key: k, dir: state.sort.key === k ? -state.sort.dir : (k === "naam" ? 1 : k === "woningen" ? -1 : 1) };
    renderRanking();
  }));
  let qt;
  $("#rank-q").addEventListener("input", (e) => {
    clearTimeout(qt);
    qt = setTimeout(() => { state.query = e.target.value; state.shown = PAGE; renderRanking(); }, 120);
  });
  $("#rank-min").addEventListener("change", (e) => { state.minFilter = e.target.checked; renderRanking(); renderDetail(); });
  $("#rank-more").addEventListener("click", () => { state.shown += PAGE; renderRanking(); });
}

// ---------------------------------------------------------------- sensitivity
function runRobust() {
  const level = state.rankLevel, modeVal = $("#rb-mode").value;
  const btn = $("#rb-run"), prog = $("#rb-progress");
  btn.disabled = true;
  prog.hidden = false;
  $("i", prog).style.width = "0";
  $("#rb-note").textContent = `Rekent ${LEVELS.find((l) => l.key === level).naam.toLowerCase()} door …`;
  const started = performance.now();
  worker.onmessage = (e) => {
    const m = e.data;
    if (m.type === "progress") { $("i", prog).style.width = `${100 * m.done / m.total}%`; return; }
    btn.disabled = false;
    prog.hidden = true;
    state.robust = m;
    const sure = Array.from(m.kans).filter((k) => k >= 0.9).length;
    $("#rb-note").textContent = `${sure} van ${m.eligible} ${LEVELS.find((l) => l.key === level).naam.toLowerCase()} staan in 90% of meer `
      + `van de ${m.samples} rondes bij de slechtste 20% (${((performance.now() - started) / 1000).toFixed(1).replace(".", ",")} s).`;
    const rbBtn = $('.viewswitch [data-view="robuust"]');
    rbBtn.disabled = false;
    rbBtn.title = "";
    setView("robuust");
    renderRanking();
    renderDetail();
  };
  worker.postMessage({ type: "run", params: state.params, level, samples: 100, seed: 7,
                       mode: modeVal === "vrij" ? "vrij" : "spreiding", spread: modeVal === "vrij" ? 0 : Number(modeVal),
                       minWoningen: MIN_WONINGEN, worstShare: 0.2, afstand: state.afstandOverride });
}

// ---------------------------------------------------------------- data
async function loadData() {
  const [geb, buf] = await Promise.all([
    fetch("data/gebieden.json").then((r) => { if (!r.ok) throw new Error("gebieden.json"); return r.json(); }),
    fetch("data/woningen.bin.gz").then(async (r) => {
      if (!r.ok) throw new Error("woningen.bin.gz");
      const raw = new Uint8Array(await r.arrayBuffer());
      // GitHub Pages serves .gz as a file; a server may already have unzipped it
      if (raw[0] !== 0x1f || raw[1] !== 0x8b) return raw.buffer;
      const stream = new Blob([raw]).stream().pipeThrough(new DecompressionStream("gzip"));
      return new Response(stream).arrayBuffer();
    }),
  ]);
  gebieden = geb;
  data = MCDA.decode(buf);
  worker = new Worker("worker.js");
  worker.postMessage({ type: "init", buffer: buf.slice(0), gebieden });
}

// ---------------------------------------------------------------- start
const APP = window.APP = {
  state, hooks, LEVELS, CRITERIA, MCDA, select, recompute, toast, areaName, areaSub, rampColour, RAMP_SCORE,
  get map() { return map; }, get data() { return data; }, get gebieden() { return gebieden; },
  setView, paint() { paintAreas(); paintBuildings(); renderLegend(); },
  setAfstandOverride(arr) { state.afstandOverride = arr; changed(); },
  clickHandler: null,
};

(async function main() {
  readUrl();
  buildCriteria();
  syncControls();
  bindRanking();
  $("#btn-uitleg").addEventListener("click", () => $("#dlg-uitleg").showModal());
  $("#dlg-close").addEventListener("click", () => $("#dlg-uitleg").close());
  $("#btn-link").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(location.href); toast("Link naar deze afweging gekopieerd"); }
    catch { toast("Kopiëren lukt niet: kopieer de adresbalk"); }
  });
  document.querySelectorAll(".viewswitch button").forEach((b) => b.addEventListener("click", () => { if (!b.disabled) setView(b.dataset.view); }));
  $("#rb-run").addEventListener("click", runRobust);
  try {
    await Promise.all([loadData(), initMap()]);
  } catch (err) {
    $("#loading div").textContent = `Laden mislukt: ${err.message}`;
    throw err;
  }
  recompute();
  $("#loading").remove();
  document.dispatchEvent(new Event("mcda:ready"));
})();
