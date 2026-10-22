// mcda.js — the arithmetic of the 3-30-300 afwegingskaart, shared by the
// page (app.js), the sensitivity worker (worker.js) and the local server's
// extras (local.js). No DOM, no map: plain functions on typed arrays.
//
// Data (mcda/build_data.py): per home, sorted by buurt,
//   kroon    canopy within 500 m x10 (uint16, 65535 = missing)
//   afstand  walking distance to green in m (uint16, 65535 = beyond 1,275 m)
//   bomen    visible trees (uint8, 255 = missing: no facade ring)
//
// A home scores 0..1 per criterion through a value function, then the three
// scores are combined with the weights by one of three methods.

/* exported MCDA */
var MCDA = (function () {
  "use strict";

  var MISSING16 = 65535, MISSING8 = 255, MAX_AFSTAND = 1275;
  var KEYS = ["bomen", "kroon", "afstand"];

  // Parameters of one analysis; also what the URL and the scenarios store
  function defaults() {
    return {
      w: { bomen: 1, kroon: 1, afstand: 1 },             // weights 0..5
      vf: { bomen: "lin", kroon: "lin", afstand: "lin" }, // "bin" (rule met) or "lin" (gradual)
      doel: { bomen: 3, kroon: 30, afstand: 300 },        // target: score 1 at or beyond it
      nul: 1275,                                          // afstand at which the gradual score reaches 0
      methode: "som",                                     // "som" | "product" | "min"
    };
  }

  // Lookup tables: score of every possible stored value, NaN = missing
  function tables(p) {
    var tb = new Float32Array(256), tk = new Float32Array(65536), ta = new Float32Array(65536), v;
    for (v = 0; v < 256; v++) {
      tb[v] = v === MISSING8 ? NaN : p.vf.bomen === "bin" ? (v >= p.doel.bomen ? 1 : 0)
                                                          : Math.min(v / p.doel.bomen, 1);
    }
    for (v = 0; v < 65536; v++) {
      if (v === MISSING16) { tk[v] = NaN; continue; }
      var pct = v / 10;
      tk[v] = p.vf.kroon === "bin" ? (pct >= p.doel.kroon ? 1 : 0) : Math.min(pct / p.doel.kroon, 1);
    }
    var nul = Math.max(p.nul, p.doel.afstand + 1);
    for (v = 0; v < 65536; v++) {
      // beyond 1,275 m is not missing: it is far, so it scores 0
      if (v === MISSING16) { ta[v] = 0; continue; }
      ta[v] = p.vf.afstand === "bin" ? (v <= p.doel.afstand ? 1 : 0)
            : v <= p.doel.afstand ? 1 : v >= nul ? 0 : (nul - v) / (nul - p.doel.afstand);
    }
    // "Meets the rule" always uses the targets as yes/no, whatever the value function
    var mb = new Uint8Array(256), mk = new Uint8Array(65536), ma = new Uint8Array(65536);
    for (v = 0; v < 255; v++) mb[v] = v >= p.doel.bomen ? 1 : 0;
    for (v = 0; v < 65535; v++) { mk[v] = v / 10 >= p.doel.kroon ? 1 : 0; ma[v] = v <= p.doel.afstand ? 1 : 0; }
    return { tb: tb, tk: tk, ta: ta, mb: mb, mk: mk, ma: ma };
  }

  // Combine three scores (NaN = missing, left out) with weights wb, wk, wa
  function combine(methode, sb, sk, sa, wb, wk, wa) {
    if (sb !== sb) wb = 0;
    if (sk !== sk) wk = 0;
    var tot = wb + wk + wa;
    if (tot <= 0) return NaN;
    if (methode === "som") {
      return ((wb ? wb * sb : 0) + (wk ? wk * sk : 0) + (wa ? wa * sa : 0)) / tot;
    }
    if (methode === "product") {
      // weighted geometric mean: a zero on any weighted criterion gives zero
      var r = 1;
      if (wb) r *= Math.pow(sb, wb / tot);
      if (wk) r *= Math.pow(sk, wk / tot);
      if (wa) r *= Math.pow(sa, wa / tot);
      return r;
    }
    // "min": weighted minimum (Yager 1978). The heaviest criterion counts in
    // full; a lighter one can pull the score down by at most its relative weight.
    var wmax = Math.max(wb, wk, wa), m = 1;
    if (wb) m = Math.min(m, Math.max(1 - wb / wmax, sb));
    if (wk) m = Math.min(m, Math.max(1 - wk / wmax, sk));
    if (wa) m = Math.min(m, Math.max(1 - wa / wmax, sa));
    return m;
  }

  // Per buurt sums over its homes. Out: Float64Array per field, length = buurten.
  //   n          homes
  //   som        sum of combined scores (homes with a score)
  //   nScore     homes with a score
  //   alle       homes meeting all three targets
  //   s<k>, n<k> sum of criterion scores and homes with a value, per criterion
  //   m<k>       homes meeting that criterion's target
  // afstandOverride (optional): Uint16Array of afstand replacing data.afstand
  function aggregate(data, buurten, p, afstandOverride) {
    var t = tables(p), nb = buurten.length;
    var out = {};
    ["n", "som", "nScore", "alle", "sbomen", "skroon", "safstand", "nbomen", "nkroon", "nafstand",
     "mbomen", "mkroon", "mafstand"].forEach(function (f) { out[f] = new Float64Array(nb); });
    var K = data.kroon, A = afstandOverride || data.afstand, B = data.bomen;
    var wb = p.w.bomen, wk = p.w.kroon, wa = p.w.afstand, me = p.methode;
    for (var j = 0; j < nb; j++) {
      var first = buurten[j][4], n = buurten[j][5];
      var som = 0, ns = 0, alle = 0, s1 = 0, s2 = 0, s3 = 0, n1 = 0, n2 = 0, m1 = 0, m2 = 0, m3 = 0;
      for (var i = first, end = first + n; i < end; i++) {
        var b = B[i], k = K[i], a = A[i];
        var sb = t.tb[b], sk = t.tk[k], sa = t.ta[a];
        if (sb === sb) { s1 += sb; n1++; m1 += t.mb[b]; }
        if (sk === sk) { s2 += sk; n2++; m2 += t.mk[k]; }
        s3 += sa; m3 += t.ma[a];
        var c = combine(me, sb, sk, sa, wb, wk, wa);
        if (c === c) { som += c; ns++; }
        if (t.mb[b] && t.mk[k] && t.ma[a]) alle++;
      }
      out.n[j] = n; out.som[j] = som; out.nScore[j] = ns; out.alle[j] = alle;
      out.sbomen[j] = s1; out.skroon[j] = s2; out.safstand[j] = s3;
      out.nbomen[j] = n1; out.nkroon[j] = n2; out.nafstand[j] = n;
      out.mbomen[j] = m1; out.mkroon[j] = m2; out.mafstand[j] = m3;
    }
    return out;
  }

  // Roll the buurt sums up to a level: "buurten" | "wijken" | "gemeenten".
  // Returns per area of that level the same sum fields.
  function rollup(sums, gebieden, level) {
    if (level === "buurten") return sums;
    var size = gebieden[level].length, out = {}, f;
    for (f in sums) out[f] = new Float64Array(size);
    var bu = gebieden.buurten;
    for (var j = 0; j < bu.length; j++) {
      var to = level === "gemeenten" ? bu[j][2] : bu[j][3];
      if (to < 0) continue;     // a home outside the buurten counts for its gemeente only
      for (f in sums) out[f][to] += sums[f][j];
    }
    return out;
  }

  // Ratios 0..100 of one area from its sums (null where it has no homes)
  function area(sums, i) {
    var n = sums.n[i];
    if (!n) return null;
    function pct(a, b) { return b ? 100 * a / b : null; }
    return {
      woningen: n,
      score: pct(sums.som[i], sums.nScore[i]),
      alle: pct(sums.alle[i], n),
      bomen: pct(sums.sbomen[i], sums.nbomen[i]), kroon: pct(sums.skroon[i], sums.nkroon[i]),
      afstand: pct(sums.safstand[i], sums.nafstand[i]),
      mbomen: pct(sums.mbomen[i], sums.nbomen[i]), mkroon: pct(sums.mkroon[i], sums.nkroon[i]),
      mafstand: pct(sums.mafstand[i], sums.nafstand[i]),
    };
  }

  // Random weight sets for the sensitivity analysis: "spreiding" multiplies
  // every weight by a factor in [1-x, 1+x]; "vrij" draws uniformly over all
  // weight sets (Dirichlet(1,1,1)), keeping weights that are 0 at 0.
  function sampleWeights(p, mode, spread, rnd) {
    var w = {};
    KEYS.forEach(function (k) {
      if (!p.w[k]) { w[k] = 0; return; }
      w[k] = mode === "vrij" ? -Math.log(1 - rnd()) : p.w[k] * (1 - spread + 2 * spread * rnd());
    });
    return w;
  }

  // Small seeded generator, so a sensitivity run can be repeated exactly
  function rng(seed) {
    var s = seed >>> 0;
    return function () {
      s += 0x6D2B79F5;
      var t = s;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  // Decode woningen.bin(.gz): three columns, n homes
  function decode(buffer) {
    var n = buffer.byteLength / 5;
    return {
      n: n,
      kroon: new Uint16Array(buffer, 0, n),
      afstand: new Uint16Array(buffer, 2 * n, n),
      bomen: new Uint8Array(buffer, 4 * n, n),
    };
  }

  return { KEYS: KEYS, MAX_AFSTAND: MAX_AFSTAND, MISSING16: MISSING16, MISSING8: MISSING8,
           defaults: defaults, tables: tables, combine: combine, aggregate: aggregate,
           rollup: rollup, area: area, sampleWeights: sampleWeights, rng: rng, decode: decode };
})();
