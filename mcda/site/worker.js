// worker.js — sensitivity of the ranking to the weights, off the main thread.
// For every random weight set (MCDA.sampleWeights) the areas of one level are
// scored and ranked from worst to best; per area the worker counts how often
// it lands in the worst share, and keeps its rank interval (5th-95th percentile).
/* global MCDA */
importScripts("mcda.js");

var data = null, gebieden = null;

self.onmessage = function (e) {
  var m = e.data;
  if (m.type === "init") {
    data = MCDA.decode(m.buffer);
    gebieden = m.gebieden;
    return;
  }
  if (m.type === "run") run(m);
};

function run(m) {
  var p = m.params, rnd = MCDA.rng(m.seed || 1), level = m.level;
  var size = gebieden[level].length, ranks = [], worst = new Uint32Array(size);
  var eligible = null, nEligible = 0;
  for (var s = 0; s < m.samples; s++) {
    var q = JSON.parse(JSON.stringify(p));
    q.w = MCDA.sampleWeights(p, m.mode, m.spread, rnd);
    var sums = MCDA.rollup(MCDA.aggregate(data, gebieden.buurten, q, m.afstand || null), gebieden, level);
    if (!eligible) {
      eligible = [];
      for (var i = 0; i < size; i++) if (sums.n[i] >= m.minWoningen) eligible.push(i);
      nEligible = eligible.length;
    }
    var scored = eligible.map(function (i) { return [i, sums.nScore[i] ? sums.som[i] / sums.nScore[i] : 0]; });
    scored.sort(function (a, b) { return a[1] - b[1]; });
    var r = new Uint16Array(size), cut = Math.ceil(m.worstShare * nEligible);
    for (var k = 0; k < scored.length; k++) {
      r[scored[k][0]] = k + 1;
      if (k < cut) worst[scored[k][0]]++;
    }
    ranks.push(r);
    if (s % 5 === 4) self.postMessage({ type: "progress", done: s + 1, total: m.samples });
  }
  // Rank interval per eligible area
  var lo = new Uint16Array(size), hi = new Uint16Array(size), med = new Uint16Array(size), col = [];
  eligible.forEach(function (i) {
    col.length = 0;
    for (var s2 = 0; s2 < ranks.length; s2++) col.push(ranks[s2][i]);
    col.sort(function (a, b) { return a - b; });
    lo[i] = col[Math.floor(0.05 * (col.length - 1))];
    hi[i] = col[Math.ceil(0.95 * (col.length - 1))];
    med[i] = col[Math.floor(col.length / 2)];
  });
  var kans = new Float32Array(size).fill(NaN);
  eligible.forEach(function (i) { kans[i] = worst[i] / m.samples; });
  self.postMessage({ type: "done", level: level, kans: kans, lo: lo, hi: hi, med: med,
                     eligible: nEligible, samples: m.samples }, [kans.buffer, lo.buffer, hi.buffer, med.buffer]);
}
