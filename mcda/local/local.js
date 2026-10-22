// local.js — what the local server (mcda/local/server.py) adds to the
// afwegingskaart: your own area, what-if new green, saved scenarios with a
// difference map, export, and the NEO tree points. Extends app.js via window.APP.
/* global APP, maplibregl */
"use strict";

(function () {
  const $ = (s, el = document) => el.querySelector(s);
  const nl = new Intl.NumberFormat("nl-NL");
  const fmtInt = (v) => nl.format(Math.round(v));
  const fmtPct = (v, d = 0) => (v == null || isNaN(v) ? "–" : `${v.toFixed(d).replace(".", ",")}%`);
  const fmtScore = (v) => (v == null || isNaN(v) ? "–" : Math.round(v).toString());
  const fmtSigned = (v, d = 1) => (v == null || isNaN(v) ? "–" : `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d).replace(".", ",")}`);

  async function api(path, body, method) {
    const r = await fetch(`/api/${path}`, body === undefined ? { method: method || "GET" }
      : { method: method || "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const ctype = r.headers.get("Content-Type") || "";
    if (!r.ok) {
      const msg = ctype.includes("json") ? (await r.json()).fout : r.statusText;
      throw new Error(msg || `fout ${r.status}`);
    }
    return ctype.includes("json") ? r.json() : r;
  }

  const local = {
    tab: "gebied",
    draw: null,                 // {doel: "gebied" | "groen", pts: [[lon, lat], ...]}
    gebied: null,               // {geometry, indices: Int32Array, ha}
    groen: [],                  // [{geometry, changes: [[i, d]], ha}]
    omweg: 1.3,
    scenarios: [],
    vergelijk: null,            // {id, naam, sums: per level}
  };

  // ------------------------------------------------------------ panel
  function buildPanel() {
    const sec = document.createElement("section");
    sec.className = "section local";
    sec.innerHTML = `
      <h2>Alleen lokaal <span class="chip">Lokaal</span></h2>
      <div class="ltabs" role="tablist" aria-label="Lokale functies">
        <button role="tab" data-tab="gebied">Eigen gebied</button>
        <button role="tab" data-tab="groen">Wat als</button>
        <button role="tab" data-tab="scen">Scenario's</button>
        <button role="tab" data-tab="export">Export</button>
      </div>
      <div class="lpanel" data-panel="gebied">
        <p class="hint">Teken een gebied: klik de hoeken aan en klik op het eerste punt om af te sluiten (Esc stopt).
          Of laad een GeoJSON-bestand, bijvoorbeeld een plangebied.</p>
        <div class="lrow">
          <button class="btn primary" type="button" data-act="teken-gebied">Teken gebied</button>
          <label class="btn" tabindex="0">GeoJSON laden<input type="file" accept=".geojson,.json,application/geo+json" hidden data-act="laad"></label>
          <button class="btn" type="button" data-act="wis-gebied" hidden>Wis</button>
        </div>
        <div class="lcard" data-out="gebied" hidden></div>
      </div>
      <div class="lpanel" data-panel="groen" hidden>
        <p class="hint">Teken waar nieuw openbaar groen komt. Voor de woningen in de buurt wordt de loopafstand opnieuw geschat:
          de afstand in rechte lijn × de omwegfactor. Telt alleen waar dat korter is dan de gemeten route.</p>
        <label class="field"><span>Omwegfactor</span>
          <input type="range" min="1" max="2" step="0.05" data-act="omweg" aria-label="Omwegfactor"><output data-out="omweg"></output></label>
        <p class="hint" style="margin-top:2px">Een looproute is in de stad meestal 1,2 tot 1,4 keer de rechte lijn.</p>
        <div class="lrow">
          <button class="btn primary" type="button" data-act="teken-groen">Teken nieuw groen</button>
          <button class="btn" type="button" data-act="wis-groen" hidden>Alles ongedaan</button>
        </div>
        <div class="lcard" data-out="groen" hidden></div>
      </div>
      <div class="lpanel" data-panel="scen" hidden>
        <p class="hint">Bewaar de huidige afweging (met eventueel nieuw groen) en vergelijk later. De scenario's staan in
          <code>mcda/local/data/scenarios.json</code> op deze computer.</p>
        <div class="lrow">
          <label class="sr-only" for="scen-naam">Naam</label>
          <input id="scen-naam" class="linput" type="text" maxlength="80" placeholder="Naam, bv. 'Raadsvoorstel bomen'">
          <button class="btn primary" type="button" data-act="bewaar">Bewaar</button>
        </div>
        <ul class="slist" data-out="scen"></ul>
      </div>
      <div class="lpanel" data-panel="export" hidden>
        <p class="hint">De scores van alle gebieden van het niveau in de ranglijst, met de afweging erbij in elke rij.</p>
        <div class="lrow">
          <div class="seg" role="group" aria-label="Formaat">
            <button type="button" data-fmt="gpkg" aria-pressed="true">GeoPackage</button>
            <button type="button" data-fmt="csv" aria-pressed="false">CSV</button>
          </div>
          <button class="btn primary" type="button" data-act="export">Download</button>
        </div>
        <p class="hint" data-out="export"></p>
      </div>
      <label class="check lcheck"><input type="checkbox" data-act="bomen"> NEO-bomen tonen (vanaf zoom 15)</label>
      <p class="hint" data-out="bomen">Aangekochte gegevens: daarom alleen in de lokale versie.</p>`;
    $("#extra-sections").appendChild(sec);
    local.sec = sec;

    sec.querySelectorAll(".ltabs button").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));
    setTab("gebied");
    $('[data-act="teken-gebied"]', sec).onclick = () => startDraw("gebied");
    $('[data-act="teken-groen"]', sec).onclick = () => startDraw("groen");
    $('[data-act="wis-gebied"]', sec).onclick = clearGebied;
    $('[data-act="wis-groen"]', sec).onclick = clearGroen;
    const file = $('[data-act="laad"]', sec);
    file.onchange = loadFile;
    file.parentElement.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); file.click(); }
    });
    const om = $('[data-act="omweg"]', sec);
    om.value = local.omweg;
    $('[data-out="omweg"]', sec).textContent = local.omweg.toFixed(2).replace(".", ",");
    om.oninput = () => { local.omweg = Number(om.value); $('[data-out="omweg"]', sec).textContent = local.omweg.toFixed(2).replace(".", ","); };
    $('[data-act="bewaar"]', sec).onclick = saveScenario;
    sec.querySelectorAll("[data-fmt]").forEach((b) => b.onclick = () =>
      sec.querySelectorAll("[data-fmt]").forEach((x) => x.setAttribute("aria-pressed", String(x === b))));
    $('[data-act="export"]', sec).onclick = doExport;
    $('[data-act="bomen"]', sec).onchange = (e) => toggleTrees(e.target.checked);
  }
  function setTab(t) {
    local.tab = t;
    local.sec.querySelectorAll(".ltabs button").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === t)));
    local.sec.querySelectorAll(".lpanel").forEach((p) => { p.hidden = p.dataset.panel !== t; });
    if (t === "scen") refreshScenarios();
  }

  // ------------------------------------------------------------ map layers
  function addLayers() {
    const map = APP.map;
    const empty = { type: "FeatureCollection", features: [] };
    for (const id of ["teken", "eigen", "groen", "bomen"]) map.addSource(id, { type: "geojson", data: empty });
    map.addLayer({ id: "groen-fill", type: "fill", source: "groen", paint: { "fill-color": "#16a34a", "fill-opacity": 0.45 } });
    map.addLayer({ id: "groen-line", type: "line", source: "groen", paint: { "line-color": "#14532d", "line-width": 2 } });
    map.addLayer({ id: "eigen-line", type: "line", source: "eigen", paint: { "line-color": "#7c3aed", "line-width": 3 } });
    map.addLayer({ id: "teken-line", type: "line", source: "teken", paint: { "line-color": "#0f172a", "line-width": 2, "line-dasharray": [2, 1] } });
    map.addLayer({ id: "teken-pt", type: "circle", source: "teken", filter: ["==", ["geometry-type"], "Point"],
                   paint: { "circle-radius": 5, "circle-color": "#ffffff", "circle-stroke-color": "#0f172a", "circle-stroke-width": 2 } });
    map.addLayer({ id: "bomen-pt", type: "circle", source: "bomen", minzoom: 15,
                   paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 15, 1.6, 18, 4],
                            "circle-color": "#166534", "circle-stroke-color": "#ffffff", "circle-stroke-width": 0.6 } });
  }

  // ------------------------------------------------------------ drawing
  function startDraw(doel) {
    local.draw = { doel, pts: [] };
    APP.map.getCanvas().style.cursor = "crosshair";
    APP.map.doubleClickZoom.disable();
    APP.toast(doel === "groen" ? "Klik de hoeken van het nieuwe groen aan" : "Klik de hoeken van het gebied aan");
    renderDraw();
  }
  function stopDraw() {
    local.draw = null;
    APP.map.getCanvas().style.cursor = "";
    setTimeout(() => APP.map.doubleClickZoom.enable(), 300);
    renderDraw();
  }
  function renderDraw() {
    const pts = local.draw ? local.draw.pts : [];
    const feats = pts.map((p) => ({ type: "Feature", geometry: { type: "Point", coordinates: p }, properties: {} }));
    if (pts.length > 1) feats.push({ type: "Feature", geometry: { type: "LineString", coordinates: pts }, properties: {} });
    APP.map.getSource("teken").setData({ type: "FeatureCollection", features: feats });
  }
  APP.clickHandler = (e) => {
    if (!local.draw) return false;
    const pts = local.draw.pts, p = [e.lngLat.lng, e.lngLat.lat];
    if (pts.length >= 3) {
      const first = APP.map.project(pts[0]);
      if (Math.hypot(first.x - e.point.x, first.y - e.point.y) < 12) { finishDraw(); return true; }
    }
    pts.push(p);
    renderDraw();
    return true;
  };
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && local.draw) stopDraw(); });
  function finishDraw() {
    const { doel, pts } = local.draw;
    const geometry = { type: "Polygon", coordinates: [[...pts, pts[0]]] };
    stopDraw();
    if (doel === "gebied") setGebied(geometry);
    else addGroen(geometry);
  }

  // ------------------------------------------------------------ own area
  async function setGebied(geometry) {
    const card = $('[data-out="gebied"]', local.sec);
    card.hidden = false;
    card.innerHTML = '<p class="hint">Woningen zoeken …</p>';
    try {
      const r = await api("gebied", { geometry });
      local.gebied = { geometry, indices: Int32Array.from(r.indices), ha: r.oppervlakte_ha };
      APP.map.getSource("eigen").setData({ type: "Feature", geometry, properties: {} });
      $('[data-act="wis-gebied"]', local.sec).hidden = false;
      renderGebied();
    } catch (err) {
      card.innerHTML = `<p class="hint lerr">${err.message}</p>`;
    }
  }
  function clearGebied() {
    local.gebied = null;
    APP.map.getSource("eigen").setData({ type: "FeatureCollection", features: [] });
    $('[data-out="gebied"]', local.sec).hidden = true;
    $('[data-act="wis-gebied"]', local.sec).hidden = true;
  }
  async function loadFile(e) {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    try {
      const gj = JSON.parse(await file.text());
      const geoms = gj.type === "FeatureCollection" ? gj.features.map((f) => f.geometry)
        : gj.type === "Feature" ? [gj.geometry] : [gj];
      const polys = geoms.filter((g) => g && (g.type === "Polygon" || g.type === "MultiPolygon"));
      if (!polys.length) throw new Error("geen vlak (Polygon) in het bestand");
      // several polygons: one MultiPolygon of all of them
      const geometry = polys.length === 1 ? polys[0] : { type: "MultiPolygon",
        coordinates: polys.flatMap((g) => (g.type === "Polygon" ? [g.coordinates] : g.coordinates)) };
      const c = JSON.stringify(geometry).match(/-?\d+\.?\d*/);
      if (c && Math.abs(Number(c[0])) > 180) throw new Error("het bestand lijkt niet in lengte- en breedtegraden (WGS84) te staan");
      await setGebied(geometry);
      const bb = bbox(geometry);
      APP.map.fitBounds([[bb[0], bb[1]], [bb[2], bb[3]]], { padding: 60 });
    } catch (err) {
      APP.toast(`Laden mislukt: ${err.message}`);
    }
  }
  function bbox(g) {
    const b = [180, 90, -180, -90];
    JSON.stringify(g.coordinates).replace(/\[(-?[\d.]+),(-?[\d.]+)\]/g, (_, x, y) => {
      x = Number(x); y = Number(y);
      b[0] = Math.min(b[0], x); b[1] = Math.min(b[1], y); b[2] = Math.max(b[2], x); b[3] = Math.max(b[3], y);
      return "";
    });
    return b;
  }

  // MCDA of an arbitrary set of homes, with the current weighting
  function statsFor(indices) {
    const { MCDA, data, state } = APP, p = state.params, t = MCDA.tables(p);
    const A = state.afstandOverride || data.afstand;
    let som = 0, ns = 0, alle = 0, s = [0, 0, 0], n = [0, 0, 0], m = [0, 0, 0];
    for (const i of indices) {
      const b = data.bomen[i], k = data.kroon[i], a = A[i];
      const sb = t.tb[b], sk = t.tk[k], sa = t.ta[a];
      if (sb === sb) { s[0] += sb; n[0]++; m[0] += t.mb[b]; }
      if (sk === sk) { s[1] += sk; n[1]++; m[1] += t.mk[k]; }
      s[2] += sa; n[2]++; m[2] += t.ma[a];
      const c = MCDA.combine(p.methode, sb, sk, sa, p.w.bomen, p.w.kroon, p.w.afstand);
      if (c === c) { som += c; ns++; }
      if (t.mb[b] && t.mk[k] && t.ma[a]) alle++;
    }
    const pct = (x, y) => (y ? 100 * x / y : null);
    return { n: indices.length, score: pct(som, ns), alle: pct(alle, indices.length),
             s: s.map((v, j) => pct(v, n[j])), m: m.map((v, j) => pct(v, n[j])) };
  }
  function bars(st) {
    const C = APP.CRITERIA;
    return ["bomen", "kroon", "afstand"].map((k, j) => `<div class="bar"><span>${C[k].titel}</span>
      <div class="track"><i style="width:${st.s[j] || 0}%;background:${C[k].kleur}"></i></div><output>${fmtScore(st.s[j])}</output></div>`).join("");
  }
  function renderGebied() {
    const g = local.gebied, card = $('[data-out="gebied"]', local.sec);
    if (!g) return;
    if (!g.indices.length) { card.innerHTML = `<p class="hint">Geen woningen in dit gebied (${fmtInt(g.ha)} ha).</p>`; return; }
    const st = statsFor(g.indices);
    const prov = Number($("#st-score").textContent);
    const c = APP.rampColour(APP.RAMP_SCORE, st.score);
    card.innerHTML = `<div class="big"><b class="pill" style="background:${c};color:#0f172a">${fmtScore(st.score)}</b>
        <span>${fmtInt(st.n)} woningen · ${fmtInt(g.ha)} ha${isNaN(prov) ? "" : ` · provincie ${prov}`}</span></div>
      <div class="bars">${bars(st)}</div>
      <dl class="kv"><dt>Haalt het doel van de 3</dt><dd>${fmtPct(st.m[0])}</dd><dt>Haalt het doel van de 30</dt><dd>${fmtPct(st.m[1])}</dd>
        <dt>Haalt het doel van de 300</dt><dd>${fmtPct(st.m[2])}</dd><dt>Haalt alle drie</dt><dd>${fmtPct(st.alle, 1)}</dd></dl>`;
  }

  // ------------------------------------------------------------ what-if green
  async function addGroen(geometry, quiet) {
    const card = $('[data-out="groen"]', local.sec);
    card.hidden = false;
    if (!quiet) card.innerHTML = '<p class="hint">Loopafstanden opnieuw schatten …</p>';
    try {
      const r = await api("watals", { geometry, omweg: local.omweg });
      local.groen.push({ geometry, changes: r.wijzigingen, ha: r.oppervlakte_ha, omweg: r.omweg });
      applyGroen();
    } catch (err) {
      card.innerHTML = `<p class="hint lerr">${err.message}</p>`;
    }
  }
  function applyGroen() {
    const { data } = APP;
    APP.map.getSource("groen").setData({ type: "FeatureCollection",
      features: local.groen.map((g) => ({ type: "Feature", geometry: g.geometry, properties: {} })) });
    $('[data-act="wis-groen"]', local.sec).hidden = !local.groen.length;
    if (!local.groen.length) { APP.setAfstandOverride(null); $('[data-out="groen"]', local.sec).hidden = true; return; }
    const arr = new Uint16Array(data.afstand);
    for (const g of local.groen) for (const [i, d] of g.changes) if (d < arr[i]) arr[i] = d;
    APP.setAfstandOverride(arr);
  }
  function clearGroen() {
    local.groen = [];
    applyGroen();
  }
  function renderGroen() {
    if (!local.groen.length) return;
    const { MCDA, data, gebieden, state } = APP, p = state.params;
    const card = $('[data-out="groen"]', local.sec), arr = state.afstandOverride;
    let closer = 0, nieuw = 0;
    for (let i = 0; i < arr.length; i++) {
      if (arr[i] !== data.afstand[i]) {
        closer++;
        if (arr[i] <= p.doel.afstand && !(data.afstand[i] <= p.doel.afstand)) nieuw++;
      }
    }
    // score per buurt before and after
    const before = MCDA.aggregate(data, gebieden.buurten, p, null), after = state.sums.buurten;
    const tot = (s) => s.som.reduce((a, b) => a + b, 0) / s.nScore.reduce((a, b) => a + b, 0) * 100;
    const gains = [];
    for (let j = 0; j < after.n.length; j++) {
      if (!after.nScore[j]) continue;
      const d = 100 * (after.som[j] / after.nScore[j] - before.som[j] / before.nScore[j]);
      if (d > 0.05) gains.push([j, d]);
    }
    gains.sort((a, b) => b[1] - a[1]);
    const ha = local.groen.reduce((a, g) => a + g.ha, 0);
    card.innerHTML = `<dl class="kv">
        <dt>Nieuw groen</dt><dd>${local.groen.length} ${local.groen.length === 1 ? "vlak" : "vlakken"}, ${fmtInt(ha)} ha</dd>
        <dt>Woningen die dichterbij komen</dt><dd>${fmtInt(closer)}</dd>
        <dt>Nieuw binnen ${fmtInt(p.doel.afstand)} m</dt><dd>${fmtInt(nieuw)}</dd>
        <dt>Score Zuid-Holland</dt><dd>${Math.abs(tot(after) - tot(before)) < 0.01 ? "minder dan 0,01" : fmtSigned(tot(after) - tot(before), 2)}</dd></dl>
      <p class="hint">Buurten, wijken, gemeenten en de ranglijst rekenen met het nieuwe groen. De gebouwen op de kaart tonen de gemeten afstanden.</p>
      ${gains.length ? `<p class="hint" style="margin-top:10px"><b>Buurten die het meest stijgen</b></p>
        <ol class="gains">${gains.slice(0, 5).map(([j, d]) => `<li><button type="button" data-b="${j}">${gebieden.buurten[j][1]}</button>
          <span>${fmtSigned(d)}</span></li>`).join("")}</ol>` : '<p class="hint">Geen buurt verandert merkbaar.</p>'}`;
    card.querySelectorAll("[data-b]").forEach((b) => b.onclick = () => APP.select({ level: "buurten", i: Number(b.dataset.b) }, true));
  }

  // ------------------------------------------------------------ scenarios
  async function refreshScenarios() {
    try { local.scenarios = await api("scenario"); } catch (err) { APP.toast(err.message); return; }
    const ul = $('[data-out="scen"]', local.sec);
    if (!local.scenarios.length) { ul.innerHTML = '<li class="hint">Nog geen scenario\'s.</li>'; return; }
    ul.innerHTML = local.scenarios.slice().reverse().map((s) => `<li data-id="${s.id}">
        <div><b>${escapeHtml(s.naam)}</b><small>${s.gemaakt}${s.watals && s.watals.length ? ` · ${s.watals.length}× nieuw groen` : ""}</small></div>
        <div class="sact">
          <button class="btn" type="button" data-s="laad">Laad</button>
          <button class="btn" type="button" data-s="vgl" aria-pressed="${local.vergelijk && local.vergelijk.id === s.id}">Vergelijk</button>
          <button class="btn icon-btn" type="button" data-s="del" aria-label="Verwijder ${escapeHtml(s.naam)}">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/></svg></button>
        </div></li>`).join("");
    ul.querySelectorAll("[data-s]").forEach((b) => b.onclick = () => {
      const s = local.scenarios.find((x) => x.id === b.closest("li").dataset.id);
      if (b.dataset.s === "laad") loadScenario(s);
      else if (b.dataset.s === "vgl") compareScenario(s);
      else deleteScenario(s);
    });
  }
  function escapeHtml(s) { return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
  async function saveScenario() {
    const inp = $("#scen-naam"), naam = inp.value.trim();
    if (!naam) { inp.focus(); APP.toast("Geef het scenario een naam"); return; }
    try {
      await api("scenario", { naam, params: APP.state.params,
                              watals: local.groen.map((g) => ({ geometry: g.geometry, omweg: g.omweg })) });
      inp.value = "";
      APP.toast(`Scenario "${naam}" bewaard`);
      refreshScenarios();
    } catch (err) { APP.toast(err.message); }
  }
  async function deleteScenario(s) {
    if (!confirm(`Scenario "${s.naam}" verwijderen?`)) return;
    await api(`scenario/${s.id}`, undefined, "DELETE");
    if (local.vergelijk && local.vergelijk.id === s.id) stopCompare();
    refreshScenarios();
  }
  async function overrideFor(watals) {
    if (!watals || !watals.length) return null;
    const arr = new Uint16Array(APP.data.afstand);
    for (const w of watals) {
      const r = await api("watals", { geometry: w.geometry, omweg: w.omweg });
      for (const [i, d] of r.wijzigingen) if (d < arr[i]) arr[i] = d;
    }
    return arr;
  }
  async function loadScenario(s) {
    local.groen = [];
    for (const w of s.watals || []) {
      const r = await api("watals", { geometry: w.geometry, omweg: w.omweg });
      local.groen.push({ geometry: w.geometry, changes: r.wijzigingen, ha: r.oppervlakte_ha, omweg: r.omweg });
    }
    APP.state.params = JSON.parse(JSON.stringify(s.params));
    applyGroen();
    if (!local.groen.length) APP.setAfstandOverride(null);
    APP.toast(`Scenario "${s.naam}" geladen`);
  }
  async function compareScenario(s) {
    if (local.vergelijk && local.vergelijk.id === s.id) { stopCompare(); refreshScenarios(); return; }
    const { MCDA, data, gebieden, LEVELS } = APP;
    const buurt = MCDA.aggregate(data, gebieden.buurten, s.params, await overrideFor(s.watals));
    const sums = {};
    for (const lv of LEVELS) sums[lv.key] = MCDA.rollup(buurt, gebieden, lv.key);
    local.vergelijk = { id: s.id, naam: s.naam, sums };
    const btn = $('.viewswitch [data-view="verschil"]');
    btn.hidden = false;
    updateDiff();
    APP.setView("verschil");
    refreshScenarios();
  }
  function stopCompare() {
    local.vergelijk = null;
    APP.state.diff = null;
    $('.viewswitch [data-view="verschil"]').hidden = true;
    if (APP.state.view === "verschil") APP.setView("score");
  }
  function updateDiff() {
    const v = local.vergelijk;
    if (!v) return;
    const { MCDA, LEVELS, state } = APP, diff = {};
    for (const lv of LEVELS) {
      const now = state.sums[lv.key], then = v.sums[lv.key], d = new Float64Array(now.n.length).fill(NaN);
      for (let i = 0; i < d.length; i++) {
        const a = MCDA.area(now, i), b = MCDA.area(then, i);
        if (a && b && a.score != null && b.score != null) d[i] = a.score - b.score;
      }
      diff[lv.key] = d;
    }
    state.diff = diff;
    state.diffLabel = `"${v.naam}"`;
  }

  // ------------------------------------------------------------ export
  function afwegingTekst() {
    const p = APP.state.params, C = APP.CRITERIA;
    const methode = { som: "gewogen gemiddelde", product: "gewogen product", min: "zwakste schakel" }[p.methode];
    const parts = ["bomen", "kroon", "afstand"].map((k) => `${C[k].badge}: gewicht ${p.w[k]}, ${p.vf[k] === "bin" ? "ja/nee" : "geleidelijk"}, doel ${p.doel[k]}`);
    return `${methode}; ${parts.join("; ")}; 300 nul bij ${p.nul} m${local.groen.length ? `; ${local.groen.length}x nieuw groen` : ""}`;
  }
  async function doExport() {
    const { MCDA, state } = APP, level = state.rankLevel, sums = state.sums[level];
    const fmt = local.sec.querySelector('[data-fmt][aria-pressed="true"]').dataset.fmt;
    const rows = [];
    for (let i = 0; i < sums.n.length; i++) {
      const a = MCDA.area(sums, i);
      if (a) rows.push({ i, ...a });
    }
    const out = $('[data-out="export"]', local.sec);
    out.textContent = "Bestand maken …";
    try {
      const r = await api("export", { level, format: fmt, rows, afweging: afwegingTekst() });
      const blob = await r.blob();
      const name = (r.headers.get("Content-Disposition") || "").match(/filename="(.+)"/)?.[1] || `afweging.${fmt}`;
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = name;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
      out.textContent = `${name}: ${fmtInt(rows.length)} ${level}.`;
    } catch (err) { out.textContent = `Export mislukt: ${err.message}`; }
  }

  // ------------------------------------------------------------ trees
  let treesOn = false, treeTimer;
  function toggleTrees(on) {
    treesOn = on;
    if (on) loadTrees();
    else APP.map.getSource("bomen").setData({ type: "FeatureCollection", features: [] });
  }
  async function loadTrees() {
    if (!treesOn) return;
    const map = APP.map, note = $('[data-out="bomen"]', local.sec);
    if (map.getZoom() < 15) { note.textContent = "Zoom verder in (15 of meer) om de bomen te zien."; return; }
    const b = map.getBounds();
    try {
      const gj = await api(`bomen?bbox=${[b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(5)).join(",")}`);
      map.getSource("bomen").setData(gj);
      note.textContent = `${fmtInt(gj.features.length)} bomen in beeld${gj.afgekapt ? " (afgekapt: zoom verder in)" : ""}. Aangekochte NEO-gegevens.`;
    } catch (err) { note.textContent = err.message; }
  }

  // ------------------------------------------------------------ start
  function start() {
    buildPanel();
    addLayers();
    APP.map.on("moveend", () => { clearTimeout(treeTimer); treeTimer = setTimeout(loadTrees, 250); });
    APP.hooks.push(() => {
      renderGebied();
      renderGroen();
      if (local.vergelijk) { updateDiff(); APP.paint(); }
    });
  }
  if (APP.map && APP.map.getSource && APP.data) start();
  else document.addEventListener("mcda:ready", start, { once: true });
})();
