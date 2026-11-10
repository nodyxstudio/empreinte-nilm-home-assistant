// Interface d'Empreinte : lit les données de l'intégration par la WebSocket de HA.
"use strict";
const { findHabits, habitHints, periodOf, HABIT } = await import(new URL("habits.js" + new URL(import.meta.url).search, import.meta.url).href);

// ---------- Outils
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const median = a => { if (!a.length) return 0; const s = [...a].sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const quant = (a, q) => { if (!a.length) return 0; const s = [...a].sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.floor(q * s.length))]; };
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const nf1 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1, minimumFractionDigits: 1 });
const nf0 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });
const nf2 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
const PRIX = 0.22033;   // €/kWh, celui saisi dans HA
const STEP = 15;        // s, pas de la courbe envoyée par l'intégration
const N = 86400 / STEP;
const fmtW = w => w >= 1000 ? nf1.format(w / 1000) + " kW" : nf0.format(w) + " W";
const fmtKwh = k => (k < 10 ? nf2 : nf1).format(k) + " kWh";
const fmtEur = k => nf2.format(k * PRIX) + " €";
const fmtDur = s => { s = Math.round(s); if (s < 60) return s + " s"; if (s < 3600) return Math.round(s / 60) + " min"; const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60); return h + " h" + (m ? " " + String(m).padStart(2, "0") : ""); };
const pad = n => String(n).padStart(2, "0");
const fmtHM = ms => { const d = new Date(ms); return pad(d.getHours()) + ":" + pad(d.getMinutes()); };
const dfDay = new Intl.DateTimeFormat("fr-FR", { weekday: "short", day: "numeric", month: "short" });
const dfLong = new Intl.DateTimeFormat("fr-FR", { weekday: "long", day: "numeric", month: "long" });
const slug = s => (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "") || "appareil";
const ago = ms => { const s = (Date.now() - ms) / 1000; if (s < 90) return "à l'instant"; if (s < 3600) return `il y a ${Math.round(s / 60)} min`; if (s < 86400) return `il y a ${Math.round(s / 3600)} h`; return `il y a ${Math.round(s / 86400)} j`; };

// ---------- État
let hass = null;
const S = { groups: [], kinds: {}, status: {}, ver: 0, day: 0, range: [0, 24], sel: new Set(), loaded: false, error: null, grp: false };
try { S.grp = localStorage.getItem("empreinte-grp") === "1"; } catch (e) { }
const catOf = gid => { const g = G(gid); return g && g.cat || null; };
const catCol = cid => `var(--s${[7, 5, 3, 1, 4, 6, 2, 8][Math.max(0, (S.status.categories || []).findIndex(c => c.id === cid)) % 8]})`;
let EVENTS = [];
let D0 = 0, NDAYS = 1;
const raw = new Map();     // jour -> {values, at}
function dayStartMs(d) { const x = new Date(D0); x.setDate(x.getDate() + d); return x.getTime(); }
function dayKey(d) { const x = new Date(dayStartMs(d)); return `${x.getFullYear()}-${pad(x.getMonth() + 1)}-${pad(x.getDate())}`; }
const dayOf = ms => { for (let d = NDAYS - 1; d >= 0; d--) if (ms >= dayStartMs(d)) return d; return -1; };
function fmtWhen(ms) {
  const d = dayOf(ms), today = NDAYS - 1;
  if (d === today) return "Aujourd'hui " + fmtHM(ms);
  if (d === today - 1) return "Hier " + fmtHM(ms);
  return dfDay.format(ms) + " " + fmtHM(ms);
}
function dayLabel(d) { if (d === NDAYS - 1) return "Aujourd'hui"; if (d === NDAYS - 2) return "Hier"; const s = dfLong.format(dayStartMs(d)); return s[0].toUpperCase() + s.slice(1); }

function applyState(st) {
  S.groups = st.groups; S.kinds = st.kinds; S.status = st.status;
  EVENTS = st.events.sort((a, b) => a.start - b.start);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const first = st.status.first_sample ? new Date(st.status.first_sample * 1000) : new Date();
  first.setHours(0, 0, 0, 0);
  const minD = new Date(today); minD.setDate(minD.getDate() - 29);
  const start = first < minD ? minD : first;
  const was = D0 && S.day === NDAYS - 1;
  D0 = start.getTime();
  NDAYS = Math.round((today - start) / 864e5) + 1;
  if (!S.loaded || was) S.day = NDAYS - 1;
  S.day = clamp(S.day, 0, NDAYS - 1);
  S.ver++; S.loaded = true;
}
async function call(msg) { return hass.callWS(msg); }
async function refresh() { applyState(await call({ type: "empreinte/state" })); }
async function ensureDay(d) {
  const k = dayKey(d), c = raw.get(k), today = d === NDAYS - 1;
  if (c && (!today || Date.now() - c.at < 60e3)) return;
  const r = await call({ type: "empreinte/day", day: k });
  raw.set(k, { values: r.values, sub: r.sub || [], at: Date.now() });
  S.ver++;
}

// ---------- Icônes
const IC = {
  logo: '<path d="M3 12h3l2.5-6 4 12 2.5-6H21"/>',
  home: '<path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
  spark: '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15l.7 1.8 1.8.7-1.8.7L19 20l-.7-1.8-1.8-.7 1.8-.7z"/>',
  map: '<path d="M3 3v18h18"/><circle cx="8" cy="14" r="1.6"/><circle cx="12" cy="9" r="1.6"/><circle cx="16.5" cy="13" r="1.6"/><circle cx="18" cy="6" r="1.6"/>',
  zap: '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
  report: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 18v-3M12 18v-6M16 18v-4"/>',
  oven: '<rect x="3" y="3" width="18" height="18" rx="2.5"/><path d="M3 8.5h18"/><rect x="7" y="12" width="10" height="5.5" rx="1"/><path d="M7 5.8h.01M10.5 5.8h.01"/>',
  heater: '<path d="M4 20h16M6 20V8a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v12M9.5 9v8M12 9v8M14.5 9v8"/>',
  tank: '<rect x="6" y="2" width="12" height="20" rx="5"/><path d="M12 8.5c-1.6 2-2.2 3.1-2.2 4.2a2.2 2.2 0 0 0 4.4 0c0-1.1-.6-2.2-2.2-4.2z"/>',
  washer: '<rect x="4" y="2" width="16" height="20" rx="2.5"/><circle cx="12" cy="13.5" r="4.8"/><path d="M8 5.8h.01M11 5.8h.01"/>',
  microwave: '<rect x="2" y="5" width="20" height="14" rx="2.5"/><rect x="5" y="8" width="10" height="8" rx="1"/><path d="M18.5 9v.01M18.5 12v.01M18.5 15v.01"/>',
  kettle: '<path d="M6 10h11l-1.2 10H7.2z"/><path d="M8.5 10V8a3.5 3.5 0 0 1 7 0v2"/><path d="M17 12h1.5a2 2 0 0 1 0 4H16.6"/>',
  cooktop: '<rect x="2" y="4" width="20" height="16" rx="2.5"/><circle cx="8" cy="12" r="3"/><circle cx="16" cy="12" r="3"/>',
  dish: '<rect x="4" y="2" width="16" height="20" rx="2.5"/><path d="M4 7h16M8 4.5h.01M15.5 13a3.5 3.5 0 1 1-7 0"/>',
  plug: '<path d="M9 2v5M15 2v5M6 7h12v4a6 6 0 0 1-12 0zM12 17v5"/>',
  help: '<circle cx="12" cy="12" r="9.5"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .8-1 1.5v.4M12 17h.01"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  x: '<path d="M6 6l12 12M18 6L6 18"/>',
  merge: '<path d="M7 3v5a5 5 0 0 0 5 5 5 5 0 0 1 5 5v3M17 3v5a5 5 0 0 1-2.4 4.3M4 18l3 3 3-3"/>',
  split: '<path d="M12 21v-7a5 5 0 0 0-5-5H4M12 14a5 5 0 0 1 5-5h3M7 6L4 9l3 3M17 6l3 3-3 3"/>',
  edit: '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
  left: '<path d="M15 6l-6 6 6 6"/>',
  right: '<path d="M9 6l6 6-6 6"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M21 13A9 9 0 1 1 11 3a7 7 0 0 0 10 10z"/>',
  auto: '<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/>',
  alert: '<path d="M12 3.5l9.5 16.5h-19z"/><path d="M12 10v4.5M12 17.2h.01"/>',
  move: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  undo: '<path d="M9 14L4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  trash: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  sensor: '<circle cx="12" cy="12" r="2.5"/><path d="M7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4M5 5a10 10 0 0 0 0 14M19 5a10 10 0 0 1 0 14"/>',
  zoom: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4M8 11h6"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
};
const icon = (n, cls = "i") => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${IC[n] || IC.plug}</svg>`;
const ICON_RULES = [[/four/i, "oven"], [/chauff.*eau|ballon|cumulus/i, "tank"], [/chauff|radiat|soufflant|convect/i, "heater"], [/micro/i, "microwave"], [/bouill|th[ée]i/i, "kettle"], [/linge|machine [aà] laver|s[eè]che/i, "washer"], [/vaisselle/i, "dish"], [/plaque|induction|cuisson/i, "cooktop"]];
const iconFor = name => { for (const [re, ic] of ICON_RULES) if (re.test(name || "")) return ic; return name ? "plug" : "help"; };
const SHAPE_TXT = { stable: "Puissance constante", cycles: "S'allume et s'éteint", paliers: "Puissance qui varie" };
const KIND_NOTE = {
  bouilloire: "environ 2 kW pendant quelques minutes : la signature classique d'une bouilloire",
  micro_ondes: "de courtes utilisations autour des repas, parfois en marche/arrêt (puissance réduite)",
  four: "une longue chauffe puis des cycles marche/arrêt : le thermostat d'un four",
  chauffage_sdb: "un plateau stable, le matin ou le soir : un radiateur soufflant",
  lave_linge: "une chauffe de 10 à 40 min vers 2 kW : la chauffe de l'eau d'un lave-linge",
  seche_linge: "une longue chauffe en marche/arrêt : un sèche-linge",
  lave_vaisselle: "une chauffe de 2 kW, souvent après le repas : un lave-vaisselle",
  plaques: "une puissance qui change souvent, aux heures des repas : plaques de cuisson",
  aspirateur: "un plateau de 1 à 1,8 kW de quelques minutes en journée : un aspirateur",
  fer: "des cycles marche/arrêt pendant longtemps : un fer à repasser",
  grille_pain: "environ 1 kW pendant 1 à 5 min le matin : un grille-pain",
  seche_cheveux: "1 à 2 kW pendant quelques minutes, matin ou soir : un sèche-cheveux",
};

// ---------- Groupes
const G = id => S.groups.find(g => g.id === id);
const col = g => g ? `var(--s${g.slot})` : "var(--unk)";
const gname = g => g ? (g.name || "Groupe " + g.letter) : "Pas reconnu";
const gicon = g => g ? (g.name ? iconFor(g.name) : "help") : "help";
const hypLabel = g => g && !g.name && g.hyp ? S.kinds[g.hyp] : null;
const members = gid => EVENTS.filter(e => (e.gid || null) === gid);
// une utilisation qui ne ressemble pas aux autres, et que tu n'as pas encore confirmée (« C'est sûr » ou « Oui, c'est ça »)
const isAty = e => e.sim != null && e.sim < 0.7 && !(e.pinned && !e.doubt);

const statCache = new Map();
function gstats(gid) {
  const key = gid + "|" + S.ver;
  if (statCache.has(key)) return statCache.get(key);
  const m = members(gid), g = G(gid);
  const P = g && g.P || median(m.map(e => e.p)), D = g && g.D || median(m.map(e => e.dur)), WH = median(m.map(e => e.wh));
  const hours = Array(24).fill(0); m.forEach(e => hours[new Date(e.start).getHours()]++);
  let split = null;
  const ps = m.map(e => e.p).sort((a, b) => a - b);
  for (let i = 2; i <= ps.length - 2; i++) {
    const r = ps[i] / ps[i - 1];
    if (r > 1.3 && (!split || r > split.r)) split = { r, thr: (ps[i] + ps[i - 1]) / 2, lowN: i, highN: ps.length - i, lowP: median(ps.slice(0, i)), highP: median(ps.slice(i)) };
  }
  const shape = g && g.shape ? SHAPE_TXT[g.shape] : SHAPE_TXT[["stable", "paliers", "cycles"][median(m.map(e => ["stable", "paliers", "cycles"].indexOf(e.shape))) | 0]] || "—";
  const st = { m, n: m.length, P, D, WH, hours, conf: g ? g.conf : 0, shape, split, last: m.length ? Math.max(...m.map(e => e.start)) : null, kwhWeek: m.filter(e => e.start > Date.now() - 7 * 864e5).reduce((a, e) => a + e.wh, 0) / 1000 };
  statCache.set(key, st);
  return st;
}
function hypReasons(g, st) {
  const pk = st.hours.map((v, h) => [v, h]).sort((a, b) => b[0] - a[0]).filter(x => x[0] > 0).slice(0, 2).map(x => x[1] + " h").join(" et ");
  const r = [`Consomme environ ${fmtW(st.P)}, durée typique ${fmtDur(st.D)}`, `Forme : ${st.shape.toLowerCase()}`, `Revient surtout vers ${pk || "—"}`];
  if (KIND_NOTE[g.hyp]) r.push("Ressemble à " + KIND_NOTE[g.hyp]);
  return r;
}
// pas assez vu (moins de 8 fois) n'est pas « à vérifier » : il faut juste qu'il tourne encore
const confLabel = (c, g) => c >= 0.85 ? "Sûr" : g && (g.n_uses || 0) < 8 && (g.n_fixed || 0) === 0 ? `Vu ${g.n_uses || 0} fois sur 8` : c >= 0.7 ? "Probable" : "À vérifier";
const confColor = c => c >= 0.85 ? "var(--good)" : c >= 0.7 ? "var(--warn)" : "var(--crit)";
const confBar = (c, g) => `<div class="conf"><span>${confLabel(c, g)}</span><div class="trk"><span style="width:${Math.round(c * 100)}%;background:${confColor(c)}"></span></div><span class="num">${Math.round(c * 100)} %</span></div>`;

// ---------- Courbe d'une journée
const dayCache = new Map();
function dayData(d) {
  const k = dayKey(d), r = raw.get(k), key = k + "|" + S.ver + "|" + S.grp;
  if (dayCache.has(key)) return dayCache.get(key);
  const ds = dayStartMs(d), vals = r ? r.values : [];
  let limit = 0, valid = 0; for (let i = vals.length - 1; i >= 0; i--) if (vals[i] != null) { limit = i + 1; break; }
  for (let i = 0; i < limit; i++) if (vals[i] != null) valid++;
  const total = new Float32Array(N), meas = new Float32Array(N), subs = r ? r.sub || [] : [];
  for (let i = 0; i < limit; i++) { meas[i] = Math.min(subs[i] || 0, vals[i] ?? 0); total[i] = (vals[i] ?? 0) - meas[i]; }
  const layers = new Map(), evs = [];
  const sumEv = new Float32Array(N);
  for (const e of EVENTS) {
    if (e.start >= ds + 864e5 || e.start + e.dur * 1000 <= ds) continue;
    evs.push(e);
    const lk = S.grp && catOf(e.gid) ? "cat:" + catOf(e.gid) : e.gid || "unk";
    if (!layers.has(lk)) layers.set(lk, new Float32Array(N));
    const L = layers.get(lk), i0 = Math.round((e.start - ds) / 1000 / STEP), rep = Math.max(1, Math.round(e.st / STEP));
    for (let k = 0; k < e.prof.length; k++) for (let q = 0; q < rep; q++) {
      const i = i0 + k * rep + q;
      if (i >= 0 && i < limit) { const v = Math.min(e.prof[k], total[i]); L[i] += v; sumEv[i] += v; }
    }
  }
  const base = new Float32Array(N); for (let i = 0; i < limit; i++) base[i] = Math.max(0, total[i] - sumEv[i]);
  layers.set("base", base);
  if (meas.some(v => v > 5)) layers.set("meas", meas);
  const order = ["base", ...(layers.has("meas") ? ["meas"] : []), ...(S.status.categories || []).map(c => "cat:" + c.id).filter(k => layers.has(k)), ...S.groups.filter(g => layers.has(g.id)).sort((a, b) => a.slot - b.slot).map(g => g.id), ...(layers.has("unk") ? ["unk"] : [])];
  for (const k of layers.keys()) if (!order.includes(k)) order.push(k);
  const kwh = {}; let tot = 0;
  for (const kk of order) { let s = 0; const L = layers.get(kk); for (let i = 0; i < limit; i++) s += L[i]; kwh[kk] = s * STEP / 3600 / 1000; tot += kwh[kk]; }
  const res = { ds, limit, layers, order, kwh, tot, evs, empty: limit === 0, hours: valid * STEP / 3600 };
  dayCache.set(key, res);
  return res;
}
const layerName = k => k === "base" ? "Toujours allumé" : k === "meas" ? "Prises mesurées (PC…)" : k === "unk" ? "Pas reconnu" : k.startsWith("cat:") ? ((S.status.categories || []).find(c => "cat:" + c.id === k) || {}).name || "Catégorie" : gname(G(k));
const layerCol = k => k === "base" ? "var(--base)" : k === "meas" ? "var(--meas)" : k === "unk" ? "var(--unk)" : k.startsWith("cat:") ? catCol(k.slice(4)) : col(G(k));
function niceMax(v) { const steps = [500, 1000, 1500, 2000, 3000, 4000, 5000, 6000, 8000]; return steps.find(s => s >= v * 1.06) || Math.ceil(v / 1000) * 1000; }
function yTicks(max) { const st = max <= 1500 ? 500 : max <= 3000 ? 1000 : 2000; const out = []; for (let v = 0; v <= max + 1; v += st) out.push(v); return out; }
const fmtTick = v => v === 0 ? "0" : v >= 1000 ? nf1.format(v / 1000).replace(",0", "") + " kW" : v + " W";

function drawDay(el) {
  const W = el.clientWidth, H = W < 600 ? 210 : 290, m = { l: 46, r: 10, t: 10, b: 24 }, pw = W - m.l - m.r, ph = H - m.t - m.b;
  if (pw < 60) return;
  const dd = dayData(S.day), [h0, h1] = S.range;
  const I0 = Math.round(h0 * 3600 / STEP), I1 = Math.round(h1 * 3600 / STEP), span = I1 - I0;
  const x = i => m.l + (i - I0) / span * pw;
  const end = Math.min(I1, dd.limit);
  const B = Math.max(2, Math.min(Math.round(pw / 1.5), span));
  const cols = [];
  for (let b = 0; b < B; b++) {
    const a = I0 + Math.floor(b * span / B), c = Math.max(a + 1, I0 + Math.floor((b + 1) * span / B));
    if (a >= end) break;
    const cc = Math.min(c, end), vals = {};
    for (const k of dd.order) { const L = dd.layers.get(k); let s = 0; for (let i = a; i < cc; i++) s += L[i]; vals[k] = s / (cc - a); }
    cols.push({ i: (a + cc) / 2, vals });
  }
  let vmax = 0; for (const c of cols) { let s = 0; for (const k of dd.order) s += c.vals[k]; vmax = Math.max(vmax, s); }
  const ymax = niceMax(Math.max(vmax, 1000)), y = v => m.t + ph - v / ymax * ph;
  let svg = `<svg viewBox="0 0 ${W} ${H}" height="${H}"><g class="ax">`;
  for (const v of yTicks(ymax)) svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}" class="${v === 0 ? "base" : ""}"/><text x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${fmtTick(v)}</text>`;
  const hs = h1 - h0, stepH = hs > 12 ? 3 : hs > 5 ? 1 : hs > 2 ? 0.5 : 0.25;
  for (let h = Math.ceil(h0 / stepH) * stepH; h <= h1 + 1e-9; h += stepH) {
    const xx = x(h * 3600 / STEP), lbl = Number.isInteger(h) ? h + " h" : Math.floor(h) + ":" + pad(Math.round((h % 1) * 60));
    if (xx < m.l + 8 || xx > W - m.r - 8) continue;
    svg += `<text x="${xx}" y="${H - 6}" text-anchor="middle">${lbl}</text>`;
  }
  svg += `</g>`;
  if (S.day === NDAYS - 1 && dd.limit < I1) { const xa = x(Math.max(dd.limit, I0)); svg += `<rect x="${xa}" y="${m.t}" width="${Math.max(0, W - m.r - xa)}" height="${ph}" style="fill:var(--wash)"/><text x="${(xa + W - m.r) / 2}" y="${m.t + 16}" text-anchor="middle" style="font-size:11px;fill:var(--muted)">à venir</text>`; }
  if (dd.empty) svg += `<text x="${m.l + pw / 2}" y="${m.t + ph / 2}" text-anchor="middle" style="font-size:13px;fill:var(--muted)">Pas de données ce jour-là</text>`;
  const cum = cols.map(() => 0);
  for (const k of dd.order) {
    const top = cols.map((c, j) => cum[j] + c.vals[k]);
    if (!cols.length || Math.max(...cols.map(c => c.vals[k])) < 3) { top.forEach((v, j) => cum[j] = v); continue; }
    let p = "M" + cols.map((c, j) => `${x(c.i).toFixed(1)},${y(top[j]).toFixed(1)}`).join("L");
    for (let j = cols.length - 1; j >= 0; j--) p += `L${x(cols[j].i).toFixed(1)},${y(cum[j]).toFixed(1)}`;
    svg += `<path d="${p}Z" style="fill:${layerCol(k)};opacity:${k === "base" ? 1 : .9}"/>`;
    top.forEach((v, j) => cum[j] = v);
  }
  if (cols.length) svg += `<path d="M${cols.map((c, j) => `${x(c.i).toFixed(1)},${y(cum[j]).toFixed(1)}`).join("L")}" style="fill:none;stroke:var(--ink);stroke-width:1.25;stroke-linejoin:round;opacity:.8"/>`;
  svg += `<line id="xh" y1="${m.t}" y2="${m.t + ph}" style="stroke:var(--ink-2);stroke-width:1;display:none"/><circle id="xd" r="4" style="fill:var(--ink);stroke:var(--surface);stroke-width:2;display:none"/>`;
  svg += `<rect id="brush" y="${m.t}" height="${ph}" style="fill:var(--ink);opacity:.08;display:none"/>`;
  svg += `<rect x="${m.l}" y="${m.t}" width="${pw}" height="${ph}" style="fill:transparent;cursor:crosshair" id="hit"/></svg>`;
  el.innerHTML = svg;

  const lanes = $("#lanes");
  if (lanes) {
    const ds = dd.ds; let html = "";
    const keys = [...S.groups].sort((a, b) => a.slot - b.slot).map(g => g.id).concat(["unk"]);
    for (const k of keys) {
      const evs = dd.evs.filter(e => (e.gid || "unk") === k);
      const vis = evs.filter(e => { const a = (e.start - ds) / 1000 / STEP, b = a + e.dur / STEP; return b > I0 && a < I1; });
      if (!vis.length) continue;
      html += `<div class="lane"><div class="ln" style="left:${m.l}px"><span class="dot" style="background:${layerCol(k)};width:8px;height:8px"></span>${esc(layerName(k))}</div><div class="tr" style="left:${m.l}px;right:${m.r}px">`;
      for (const e of vis) {
        const a = Math.max(I0, (e.start - ds) / 1000 / STEP), b = Math.min(I1, (e.start - ds) / 1000 / STEP + e.dur / STEP);
        html += `<div class="bar" data-e="${e.id}" style="left:${(a - I0) / span * 100}%;width:${(b - a) / span * 100}%;background:${layerCol(k)}"></div>`;
      }
      html += `</div></div>`;
    }
    lanes.innerHTML = html || `<div class="muted" style="font-size:12.5px;padding-left:${m.l}px">Aucun gros appareil allumé sur cette période.</div>`;
    $$(".bar", lanes).forEach(b => {
      const e = EVENTS.find(x => x.id === b.dataset.e);
      b.addEventListener("pointerenter", ev => showTip(ev, `<div class="t">${esc(gname(G(e.gid)))}</div><div class="r"><span>Début</span><b>${fmtHM(e.start)}</b></div><div class="r"><span>Puissance</span><b>${fmtW(e.p)}</b></div><div class="r"><span>Durée</span><b>${fmtDur(e.dur)}</b></div><div class="r"><span>Énergie</span><b>${fmtKwh(e.wh / 1000)}</b></div>`));
      b.addEventListener("pointermove", moveTip);
      b.addEventListener("pointerleave", hideTip);
      b.addEventListener("click", () => go(`#/groupe/${e.gid || "aucun"}?e=${e.id}`));
    });
  }

  const hit = $("#hit", el), xh = $("#xh", el), xd = $("#xd", el), br = $("#brush", el);
  let drag = null;
  const idxAt = px => clamp(Math.round(I0 + (px - m.l) / pw * span), I0, Math.max(I0, Math.min(I1, dd.limit) - 1));
  const localX = ev => { const r = el.getBoundingClientRect(); return (ev.clientX - r.left) * (W / r.width); };
  hit.addEventListener("pointermove", ev => {
    const px = localX(ev);
    if (drag !== null) { br.style.display = ""; br.setAttribute("x", Math.min(drag, px)); br.setAttribute("width", Math.abs(px - drag)); hideTip(); return; }
    const i = idxAt(px); if (i >= dd.limit) { hideTip(); xh.style.display = xd.style.display = "none"; return; }
    let total = 0; const rows = [];
    for (const k of dd.order) { const v = dd.layers.get(k)[i]; total += v; if (v > 8) rows.push([k, v]); }
    rows.sort((a, b) => b[1] - a[1]);
    xh.style.display = xd.style.display = "";
    xh.setAttribute("x1", x(i)); xh.setAttribute("x2", x(i)); xd.setAttribute("cx", x(i)); xd.setAttribute("cy", y(total));
    const dt = new Date(dd.ds + i * STEP * 1000);
    showTip(ev, `<div class="t">${pad(dt.getHours())}:${pad(dt.getMinutes())}:${pad(dt.getSeconds())} · ${fmtW(total)}</div>` + rows.map(([k, v]) => `<div class="r"><span><i class="sw" style="background:${layerCol(k)}"></i>${esc(layerName(k))}</span><b class="num">${fmtW(v)}</b></div>`).join(""));
  });
  hit.addEventListener("pointerleave", () => { if (drag === null) { hideTip(); xh.style.display = xd.style.display = "none"; } });
  hit.addEventListener("pointerdown", ev => { if (ev.pointerType !== "mouse") return; drag = localX(ev); hit.setPointerCapture(ev.pointerId); });
  hit.addEventListener("pointerup", ev => {
    if (drag === null) return;
    const a = Math.min(drag, localX(ev)), b = Math.max(drag, localX(ev)); drag = null; br.style.display = "none";
    if (b - a < 8) return;
    const ha = h0 + (a - m.l) / pw * hs, hb = h0 + (b - m.l) / pw * hs;
    if (hb - ha < 0.2) return;
    S.range = [clamp(ha, 0, 24), clamp(hb, 0, 24)]; renderRangeSeg(); drawDay(el);
  });
  hit.addEventListener("dblclick", () => { S.range = [0, 24]; renderRangeSeg(); drawDay(el); });
}

// ---------- Infobulle
let tip;
function showTip(ev, html) { tip.innerHTML = html; tip.classList.add("on"); moveTip(ev); }
function moveTip(ev) {
  const r = tip.getBoundingClientRect(); let lx = ev.clientX + 14, ly = ev.clientY + 14;
  if (lx + r.width > innerWidth - 8) lx = ev.clientX - r.width - 14;
  if (ly + r.height > innerHeight - 8) ly = ev.clientY - r.height - 14;
  tip.style.left = Math.max(8, lx) + "px"; tip.style.top = Math.max(8, ly) + "px";
}
function hideTip() { tip && tip.classList.remove("on"); }

// ---------- Superposition des courbes d'un groupe
function drawOverlay(el, gid, mini) {
  const evs = members(gid);
  const W = el.clientWidth, H = mini ? 86 : (W < 600 ? 200 : 260);
  const m = mini ? { l: 2, r: 2, t: 4, b: 4 } : { l: 46, r: 10, t: 10, b: 24 };
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  if (pw < 40) return;
  if (!evs.length) { el.innerHTML = `<div class="empty" style="padding:${mini ? 20 : 40}px">Aucune utilisation</div>`; return; }
  const dmax = Math.max(120, Math.min(Math.max(...evs.map(e => e.dur)), quant(evs.map(e => e.dur), 0.85) * 1.35));
  const pmax = niceMax(Math.max(...evs.map(e => Math.max(...e.prof))));
  const x = s => m.l + s / dmax * pw, y = v => m.t + ph - v / pmax * ph;
  const color = gid ? col(G(gid)) : "var(--unk)";
  let svg = `<svg viewBox="0 0 ${W} ${H}" height="${H}">`;
  if (!mini) {
    svg += `<g class="ax">`;
    for (const v of yTicks(pmax)) svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}" class="${v === 0 ? "base" : ""}"/><text x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${fmtTick(v)}</text>`;
    const maxT = Math.max(3, Math.floor(pw / 70)), tstep = [60, 120, 300, 600, 900, 1800, 3600, 7200].find(s => dmax / s <= maxT) || 7200;
    for (let s = 0; s <= dmax; s += tstep) svg += `<text x="${x(s)}" y="${H - 6}" text-anchor="${s === 0 ? "start" : "middle"}">${s === 0 ? "début" : "+" + fmtDur(s)}</text>`;
    svg += `</g>`;
  }
  for (const e of evs) {
    const pts = []; for (let k = 0; k < e.prof.length; k++) { const s = k * e.st; if (s > dmax) break; pts.push(`${x(s).toFixed(1)},${y(e.prof[k]).toFixed(1)}`); }
    svg += `<path data-e="${e.id}" d="M${x(0)},${y(0)}L${pts.join("L")}" style="fill:none;stroke:${isAty(e) ? "var(--muted)" : color};stroke-width:${mini ? 1.25 : 1.5};opacity:${mini ? .55 : .5};stroke-linejoin:round"/>`;
  }
  if (gid && !mini) {
    const st = gstats(gid), good = evs.filter(e => !isAty(e)), med = [];
    for (let s = 0; s < st.D && s <= dmax; s += STEP) { const vs = good.filter(e => s / e.st < e.prof.length).map(e => e.prof[Math.floor(s / e.st)]); if (vs.length) med.push(`${x(s).toFixed(1)},${y(median(vs)).toFixed(1)}`); }
    if (med.length) svg += `<path d="M${med.join("L")}" style="fill:none;stroke:var(--ink);stroke-width:2.25;stroke-linejoin:round"/><text x="${x(0) + 6}" y="${m.t + 12}" style="font-size:11.5px;fill:var(--ink-2)">— courbe moyenne</text>`;
  }
  el.innerHTML = svg + `</svg>`;
}
function highlightLine(eid, on) {
  $$(`path[data-e]`).forEach(p => {
    if (!on) { p.style.strokeWidth = ""; p.style.opacity = p.closest(".mini") ? ".55" : ".5"; return; }
    const me = p.dataset.e === eid; p.style.opacity = me ? "1" : ".12"; p.style.strokeWidth = me ? "2.5" : "";
  });
}
function spark(e, color) {
  const W = 120, H = 30, pr = [0, ...e.prof, 0], n = pr.length, mx = 2500;
  const pts = pr.map((v, k) => `${(k / Math.max(1, n - 1) * (W - 2) + 1).toFixed(1)},${(H - 2 - Math.min(v, mx) / mx * (H - 5)).toFixed(1)}`).join("L");
  return `<svg class="sp" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><path d="M${pts}" style="fill:none;stroke:${color};stroke-width:1.6;stroke-linejoin:round" vector-effect="non-scaling-stroke"/></svg>`;
}
function hoursStrip(hours, color) {
  const mx = Math.max(...hours, 1);
  return `<div class="hours">${hours.map((v, h) => `<i title="${h} h : ${v} fois" style="background:${v ? `color-mix(in srgb,${color} ${Math.round(25 + 75 * v / mx)}%,var(--surface))` : "var(--surface-2)"}"></i>`).join("")}</div><div class="hours-lbl"><span>0 h</span><span>6 h</span><span>12 h</span><span>18 h</span><span>24 h</span></div>`;
}

// ---------- Actions (envoyées à l'intégration)
async function act(msg, okMsg) {
  try {
    applyState(await call({ type: "empreinte/action", ...msg }));
    S.sel.clear();
    toast(okMsg, msg.action !== "undo" && !msg.action.startsWith("energy_"));   // le tableau Énergie se corrige avec « Remettre »
    await render();
  } catch (err) {
    toast("Erreur : " + (err.message || err.code || err));
  }
}
const moveEvents = (ids, target) => act({ action: "move", ids, target }, `${ids.length > 1 ? ids.length + " utilisations déplacées" : "Utilisation déplacée"}${target === "new" ? " dans un nouveau groupe" : target ? ` vers « ${gname(G(target))} »` : " dans « Pas reconnu »"}`);
const confirmEvents = ids => act({ action: "confirm", ids }, ids.length > 1 ? `${ids.length} utilisations confirmées : le programme s'en sert de référence` : "Confirmée : le programme s'en sert de référence");
const labelEvents = (ids, name) => act({ action: "label", ids, name: name.trim() }, `Rangé dans « ${name.trim()} » : le programme s'en souviendra`);
function labelModal(ids, now) {
  const names = [...new Set([...S.groups.filter(g => g.name).map(g => g.name), ...SUGG])];
  const evs = EVENTS.filter(e => (ids || []).includes(e.id));
  const step = now && typeof now === "object" ? now : null;
  const intro = step ? `Le saut de ${step.p > 0 ? "+" : ""}${fmtW(step.p)} à ${fmtHM(step.at * 1000)}. Le programme rangera ce morceau dès que tout sera éteint.`
    : now ? "Ce qui tourne en ce moment, ou vient de s'arrêter. Le programme le rangera dès que l'utilisation sera terminée."
    : `${evs.length > 1 ? `${evs.length} utilisations` : `${fmtWhen(evs[0].start)} · ${fmtW(evs[0].p)} · ${fmtDur(evs[0].dur)}`}. Dis-le au programme : il s'en servira pour reconnaître les prochaines.`;
  const help = `<p class="sub" style="font-size:12.5px;margin-top:12px"><b>C'est sûr</b> : le programme range l'utilisation dans cet appareil. <b>Peut-être</b> : un simple indice, il fera la moyenne de tes « peut-être » sans rien ranger.</p>`;
  openModal(`<h2>${now ? "En ce moment, c'est…" : "C'est quoi ?"}</h2><p class="sub">${intro}</p>
    <input class="field" id="nm" maxlength="40" placeholder="Le nom de l'appareil, par ex. : Douche, Pompe de piscine…">
    <div class="chips">${names.map(n => `<button class="chip" data-s="${esc(n)}">${icon(iconFor(n))}${esc(n)}</button>`).join("")}</div>
    <p class="sub" style="font-size:12.5px;margin-top:10px">${icon("plus")} Pas dans la liste ? Écris simplement son nom : ça crée un nouvel appareil.</p>
    ${help}
    <div class="acts"><button class="btn ghost" data-close>Annuler</button><button class="btn" id="maybe">Peut-être</button><button class="btn primary" id="ok">${icon("check")} C'est sûr</button></div>`, (b, close) => {
    const inp = $("#nm", b); inp.focus();
    $$("[data-s]", b).forEach(c => c.onclick = () => { inp.value = c.dataset.s; inp.focus(); });
    const go_ = maybe => {
      const name = inp.value.trim(); if (!name) return inp.focus(); close();
      if (now && step && !maybe) act({ action: "label_now", name, at: step.at, p: step.p }, `« ${name} » : je le suis jusqu'à ce qu'il s'éteigne`);
      else if (now) act({ action: "label_now", name, maybe, ...(step ? { at: step.at, p: step.p } : {}) }, maybe ? `Noté : « peut-être ${name} ». Je l'ajouterai comme indice à la fin de l'utilisation` : `Noté : « ${name} ». Il sera rangé dès que l'utilisation sera terminée`);
      else if (maybe) act({ action: "maybe", ids, name }, `Noté : « peut-être ${name} ». Ça l'aidera à deviner, sans rien ranger`);
      else labelEvents(ids, name);
    };
    $("#ok", b).onclick = () => go_(false); $("#maybe", b).onclick = () => go_(true);
    inp.onkeydown = e => { if (e.key === "Enter") go_(false); };
  });
}
const renameGroup = (gid, name) => act({ action: "rename", gid, name: name.trim() }, `Groupe nommé « ${name.trim()} »`);
const mergeGroups = async (src, dst) => { const n = gname(G(src)), m = gname(G(dst)); await act({ action: "merge", src, dst }, `« ${n} » fusionné dans « ${m} »`); go("#/groupe/" + dst); };
const splitGroup = (gid, thr) => act({ action: "split", gid, thr }, "Groupe séparé en deux");
// « C'est bon » : nomme si besoin, crée les capteurs et ajoute l'appareil au tableau Énergie.
async function confirmGroup(gid, name) {
  try {
    if (name) applyState(await call({ type: "empreinte/action", action: "rename", gid, name }));
    applyState(await call({ type: "empreinte/action", action: "validate", gid }));
    S.sel.clear();
    toast(`« ${gname(G(gid))} » reconnu : il sera détecté tout seul (capteurs créés)`, true);
    await render();
  } catch (err) {
    toast("Erreur : " + (err.message || err.code || err));
  }
}
const validateGroup = gid => confirmGroup(gid);
const dissolveGroup = async gid => { const n = gname(G(gid)); await act({ action: "delete", gid }, `« ${n} » supprimé, ses utilisations sont non classées`); go("#/decouvertes"); };

// ---------- Fenêtres, menus, toasts
let toastT;
function toast(msg, canUndo) {
  const t = $("#toast"); t.innerHTML = `<span>${esc(msg)}</span>${canUndo ? `<button id="undo">${icon("undo")} Annuler</button>` : ""}`;
  t.classList.add("on"); clearTimeout(toastT); toastT = setTimeout(() => t.classList.remove("on"), 6000);
  if (canUndo) $("#undo").onclick = () => { t.classList.remove("on"); act({ action: "undo" }, "Modification annulée"); };
}
function openModal(html, onMount) {
  closeMenu();
  const back = document.createElement("div"); back.className = "modal-back"; back.innerHTML = `<div class="modal" role="dialog" aria-modal="true">${html}</div>`;
  document.body.appendChild(back);
  const close = () => { back.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = e => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  back.addEventListener("click", e => { if (e.target === back || e.target.closest("[data-close]")) close(); });
  onMount && onMount(back, close);
}
let menuEl = null;
function closeMenu() { if (menuEl) { menuEl.remove(); menuEl = null; } }
function openMenu(anchor, title, items) {
  closeMenu();
  menuEl = document.createElement("div"); menuEl.className = "menu";
  menuEl.innerHTML = (title ? `<div class="mh">${esc(title)}</div>` : "") + items.map((it, i) => `<button data-i="${i}">${it.dot ? `<span class="dot" style="background:${it.dot}"></span>` : icon(it.icon || "plug")}<span>${esc(it.label)}</span></button>`).join("");
  document.body.appendChild(menuEl);
  const r = anchor.getBoundingClientRect(), mr = menuEl.getBoundingClientRect();
  let top = r.bottom + 6; if (top + mr.height > innerHeight - 10) top = Math.max(10, r.top - mr.height - 6);
  menuEl.style.top = top + "px"; menuEl.style.left = clamp(r.right - mr.width, 10, innerWidth - mr.width - 10) + "px";
  menuEl.addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; const it = items[+b.dataset.i]; closeMenu(); it.onClick(); });
  setTimeout(() => document.addEventListener("pointerdown", function h(e) { if (menuEl && !menuEl.contains(e.target)) closeMenu(); document.removeEventListener("pointerdown", h); }), 0);
}
function moveMenu(anchor, ids, exclude) {
  const items = S.groups.filter(g => g.id !== exclude).sort((a, b) => a.slot - b.slot).map(g => ({ label: gname(g), dot: col(g), onClick: () => moveEvents(ids, g.id) }));
  if (exclude !== null) items.push({ label: "Pas reconnu", dot: "var(--unk)", onClick: () => moveEvents(ids, null) });
  items.push({ label: "Nouveau groupe", icon: "plus", onClick: () => moveEvents(ids, "new") });
  openMenu(anchor, ids.length > 1 ? `Déplacer ${ids.length} utilisations vers…` : "C'était plutôt…", items);
}
const SUGG = ["Douche (chauffe-eau)", "Four", "Micro-ondes", "Bouilloire", "Frigo", "Chauffage salle de bain", "Lave-linge", "Sèche-linge", "Lave-vaisselle", "Plaques de cuisson", "Aspirateur", "Fer à repasser", "Sèche-cheveux", "Grille-pain", "Cafetière", "Climatisation"];
// « À telle heure, c'était… » : nommer une utilisation passée (hier, avant-hier…) en donnant son heure
function atModal(ds) {
  const names = [...new Set([...S.groups.filter(g => g.name).map(g => g.name), ...SUGG])];
  const d = new Date(ds), now = new Date();
  const def = S.day === NDAYS - 1 ? `${String(Math.max(0, now.getHours() - 1)).padStart(2, "0")}:00` : "12:00";
  openModal(`<h2>C'était quoi, à quelle heure ?</h2>
    <p class="sub">${esc(dayLabel(S.day))} : donne l'heure à laquelle l'appareil a tourné (à quelques minutes près). Le programme range l'utilisation qui tournait à ce moment-là.</p>
    <input class="field" id="hh" type="time" value="${def}" style="margin-bottom:10px">
    <input class="field" id="nm" maxlength="40" placeholder="Le nom de l'appareil, par ex. : Micro-ondes, Aquarium…">
    <div class="chips">${names.map(n => `<button class="chip" data-s="${esc(n)}">${icon(iconFor(n))}${esc(n)}</button>`).join("")}</div>
    <p class="sub" style="font-size:12.5px;margin-top:10px">${icon("plus")} Pas dans la liste ? Écris simplement son nom : ça crée un nouvel appareil.</p>
    <div class="acts"><button class="btn ghost" data-close>Annuler</button><button class="btn" id="maybe">Peut-être</button><button class="btn primary" id="ok">${icon("check")} C'est sûr</button></div>`, (b, close) => {
    const inp = $("#nm", b), hh = $("#hh", b);
    $$("[data-s]", b).forEach(c => c.onclick = () => { inp.value = c.dataset.s; inp.focus(); });
    const go_ = maybe => {
      const name = inp.value.trim(); if (!name) return inp.focus();
      const [h, m] = (hh.value || "12:00").split(":").map(Number);
      const t = new Date(d.getFullYear(), d.getMonth(), d.getDate(), h, m).getTime() / 1000;
      close();
      act({ action: "label_at", t, name, maybe }, maybe ? `Noté : « peut-être ${name} » à ${hh.value}` : `« ${name} » rangé à ${hh.value}`);
    };
    $("#ok", b).onclick = () => go_(false); $("#maybe", b).onclick = () => go_(true);
  });
}
function nameModal(title, value, done) {
  openModal(`<h2>${esc(title)}</h2>
    <input class="field" id="nm" maxlength="40" placeholder="Ex. : Électroménager, Cuisine…" value="${esc(value || "")}">
    <div class="acts"><button class="btn ghost" data-close>Annuler</button><button class="btn primary" id="ok">Enregistrer</button></div>`, (b, close) => {
    const inp = $("#nm", b); inp.focus(); inp.select();
    const ok = () => { if (!inp.value.trim()) return inp.focus(); close(); done(inp.value.trim()); };
    $("#ok", b).onclick = ok; inp.onkeydown = e => { if (e.key === "Enter") ok(); };
  });
}
function renameModal(gid) {
  const g = G(gid), st = gstats(gid), hyp = hypLabel(g);
  openModal(`<h2>Quel appareil est-ce ?</h2><p class="sub">${st.n} utilisations · environ ${fmtW(st.P)} · ${fmtDur(st.D)}${hyp ? ` · le programme pense à <b>${esc(hyp)}</b>` : ""}</p>
    <input class="field" id="nm" maxlength="40" placeholder="Le nom de l'appareil, par ex. : Four, Spa…" value="${esc(g.name || hyp || "")}">
    <div class="chips">${SUGG.map(s => `<button class="chip" data-s="${esc(s)}">${icon(iconFor(s))}${esc(s)}</button>`).join("")}</div>
    <p class="sub" style="font-size:12.5px;margin-top:10px">${icon("plus")} Pas dans la liste ? Écris simplement son nom : ça crée un nouvel appareil.</p>
    ${g.status === "valide" ? `<p class="sub" style="font-size:12.5px;margin-top:14px">Les capteurs déjà créés gardent leur identifiant. Seul le nom affiché change.</p>` : ""}
    <div class="acts"><button class="btn ghost" data-close>Annuler</button><button class="btn primary" id="ok">Enregistrer</button></div>`, (b, close) => {
    const inp = $("#nm", b); inp.focus(); inp.select();
    $$("[data-s]", b).forEach(c => c.onclick = () => { inp.value = c.dataset.s; inp.focus(); });
    const ok = () => { if (!inp.value.trim()) return inp.focus(); close(); renameGroup(gid, inp.value); };
    $("#ok", b).onclick = ok; inp.onkeydown = e => { if (e.key === "Enter") ok(); };
  });
}
function mergeModal(gid) {
  const g = G(gid), others = S.groups.filter(x => x.id !== gid).sort((a, b) => a.slot - b.slot);
  if (!others.length) return toast("Il n'y a pas d'autre groupe avec lequel fusionner.");
  openModal(`<h2>Fusionner « ${esc(gname(g))} »</h2><p class="sub">Toutes ses utilisations rejoignent le groupe choisi. C'est utile quand le programme a coupé un même appareil en deux.</p>
    ${others.map((o, i) => { const s = gstats(o.id); return `<label class="opt"><input type="radio" name="dst" value="${o.id}" ${i === 0 ? "checked" : ""}><span class="dot" style="background:${col(o)}"></span><span style="flex:1"><b>${esc(gname(o))}</b><br><span class="muted" style="font-size:12px">${s.n} fois · ${fmtW(s.P)} · ${fmtDur(s.D)}</span></span></label>`; }).join("")}
    <div class="acts"><button class="btn ghost" data-close>Annuler</button><button class="btn primary" id="ok">${icon("merge")} Fusionner</button></div>`, (b, close) => {
    $("#ok", b).onclick = () => { const v = $("input[name=dst]:checked", b).value; close(); mergeGroups(gid, v); };
  });
}
function splitModal(gid) {
  const st = gstats(gid);
  if (!st.split) return toast("Pas deux familles nettes dans ce groupe. Sélectionne les utilisations à sortir, puis « Nouveau groupe ».");
  const sp = st.split;
  openModal(`<h2>Séparer en deux groupes</h2><p class="sub">Le programme voit deux niveaux de puissance bien distincts dans ce groupe. Par exemple, un chauffage qui a deux allures.</p>
    <div class="grid g-2" style="margin-top:14px"><div class="card" style="box-shadow:none"><span class="muted" style="font-size:12px">Reste dans ce groupe</span><div style="font-size:20px;font-weight:600">${fmtW(sp.highP)}</div><span class="muted" style="font-size:12.5px">${sp.highN} utilisations</span></div>
    <div class="card" style="box-shadow:none"><span class="muted" style="font-size:12px">Nouveau groupe</span><div style="font-size:20px;font-weight:600">${fmtW(sp.lowP)}</div><span class="muted" style="font-size:12.5px">${sp.lowN} utilisations</span></div></div>
    <div class="acts"><button class="btn ghost" data-close>Annuler</button><button class="btn primary" id="ok">${icon("split")} Séparer</button></div>`, (b, close) => {
    $("#ok", b).onclick = () => { close(); splitGroup(gid, sp.thr); };
  });
}
function validateModal(gid) {
  const g = G(gid), st = gstats(gid), s = slug(gname(g));
  openModal(`<h2>Créer les capteurs « ${esc(gname(g))} »</h2>
    <p class="sub">Le programme reconnaîtra cet appareil tout seul et créera ces trois capteurs dans Home Assistant. Il est aussi ajouté dans le tableau Énergie, sauf s'il y est déjà compté avec sa catégorie. Tu peux l'en retirer dans « Appareils ».</p>
    <div class="ent" style="margin-top:14px">
      <div><b>binary_sensor.empreinte_${s}</b><span>en marche / arrêt</span></div>
      <div><b>sensor.empreinte_${s}_puissance</b><span>W · puissance</span></div>
      <div><b>sensor.empreinte_${s}_energie</b><span>kWh · énergie cumulée</span></div>
    </div>
    <div style="margin-top:14px">${confBar(st.conf, g)}</div>
    ${st.conf < 0.8 ? `<p class="sub" style="font-size:12.5px">La confiance est encore moyenne. Tu peux valider quand même : le programme continuera d'apprendre de tes corrections.</p>` : ""}
    ${g.name ? "" : `<p class="sub" style="font-size:12.5px">Astuce : donne-lui d'abord un nom, les capteurs le prendront dans leur identifiant.</p>`}
    <div class="acts"><button class="btn ghost" data-close>Pas encore</button><button class="btn primary" id="ok">${icon("check")} Créer les capteurs</button></div>`, (b, close) => {
    $("#ok", b).onclick = () => { close(); validateGroup(gid); };
  });
}

// ---------- Navigation
const PAGES = [["apercu", "Aperçu", "home"], ["decouvertes", "Découvertes", "spark"], ["habitudes", "Habitudes", "clock"], ["carte", "Carte", "map"], ["appareils", "Appareils", "zap"], ["rapport", "Rapport", "report"]];
// Navigation interne : HA affiche le panneau dans une iframe où les liens « # » ne marchent pas.
let CUR = "#/apercu";
function go(h) {
  const before = route().page + route().arg;
  CUR = h;
  if (route().page + route().arg !== before) { S.sel.clear(); window.scrollTo(0, 0); }
  render();
}
function route() { const h = CUR.replace(/^#\/?/, "") || "apercu"; const [path, q] = h.split("?"); const parts = path.split("/"); return { page: parts[0], arg: parts[1], q: new URLSearchParams(q || "") }; }
function renderNav() {
  const { page } = route(), cur = page === "groupe" ? "decouvertes" : page;
  const todo = S.groups.filter(g => g.status === "nouveau").length;
  const link = ([id, label, ic]) => `<a href="#/${id}" class="${cur === id ? "on" : ""}">${icon(ic)}<span>${label}</span>${id === "decouvertes" && todo ? `<span class="badge">${todo}</span>` : ""}</a>`;
  $("#nav").innerHTML = PAGES.map(link).join("");
  $("#tabbar").innerHTML = PAGES.map(link).join("");
  const st = S.status, days = st.first_sample ? Math.max(1, Math.round((Date.now() / 1000 - st.first_sample) / 86400)) : 0;
  $("#status").innerHTML = st.error
    ? `<b><span class="dot-live" style="background:var(--crit)"></span>Problème d'analyse</b><span>${esc(st.error).slice(0, 120)}</span>`
    : `<b><span class="dot-live"></span>En marche</b><span>Dernière vérification ${st.last_run ? ago(st.last_run * 1000) : "en cours"}</span><span>${days} jour${days > 1 ? "s" : ""} d'historique · ${st.n_events} utilisations</span><span>Vérifie chaque minute</span>`;
  $("#mstat").textContent = st.error ? "Problème d'analyse" : `En marche · ${days} j d'historique`;
}

// ---------- Page : Aperçu
const fmtH = h => Math.floor(h) + ":" + pad(Math.round((h % 1) * 60) % 60);
function renderRangeSeg() {
  const el = $("#rangeseg"); if (!el) return;
  const R_ = [["Journée", 0, 24], ["Nuit", 0, 7], ["Matin", 6, 10], ["Midi", 11, 15], ["Soir", 17, 24]];
  const cur = S.range;
  el.innerHTML = R_.map(([l, a, b]) => `<button class="${cur[0] === a && cur[1] === b ? "on" : ""}" data-a="${a}" data-b="${b}">${l}</button>`).join("");
  $$("button", el).forEach(b => b.onclick = () => { S.range = [+b.dataset.a, +b.dataset.b]; renderRangeSeg(); drawDay($("#daychart")); });
  const z = $("#zoominfo"); if (z) { const custom = !R_.some(([, a, b]) => cur[0] === a && cur[1] === b); z.innerHTML = custom ? `${icon("zoom")} Zoom ${fmtH(cur[0])} – ${fmtH(cur[1])} · double-clic pour revenir` : "Astuce : glisse sur la courbe pour zoomer"; }
}
// Journal unique : ce qui tourne en ce moment (appareil par appareil) et ce qui s'est allumé ce jour-là.
// « Durée » : combien de temps dure le programme (lave-vaisselle, lave-linge…). Retenu pour la prochaine fois.
function durationModal(tid) {
  const tr = (S.status.tracks || []).find(t => t.id === tid);
  if (!tr) return;
  const elapsed = Math.round((Date.now() / 1000 - tr.at) / 60);
  // programmes déjà vus : on propose le temps qu'il leur resterait maintenant
  const usual = (tr.used || []).map(total => total - elapsed).filter(m => m > 0);
  const opts = [15, 30, 45, 60, 90, 120, 150, 180].filter(m => !usual.some(u => Math.abs(u - m) < 5));
  openModal(`<h2>Il reste combien de temps ?</h2><p class="sub">Indique le temps restant affiché par ${esc(tr.name.toLowerCase())}, à partir de maintenant. Jusque-là, le programme ne le considérera pas comme fini, même pendant une longue pause. Les autres appareils (thé, café…) restent à part.</p>
    ${usual.length ? `<p class="sub" style="font-size:12.5px;margin-top:12px">Comme les autres fois :</p><div class="chips" style="margin-top:6px">${usual.map(m => `<button class="chip" data-m="${m}">${icon("clock")} encore ${fmtDur(m * 60)}</button>`).join("")}</div><p class="sub" style="font-size:12.5px;margin-top:10px">Autre :</p>` : ""}
    <div class="chips">${opts.map(m => `<button class="chip" data-m="${m}">encore ${fmtDur(m * 60)}</button>`).join("")}</div>
    <div style="display:flex;align-items:center;gap:8px;margin-top:12px">encore <input class="field" id="hh" type="number" min="0" max="23" placeholder="h" style="width:80px;margin:0"> h <input class="field" id="mm" type="number" min="0" max="59" placeholder="min" style="width:90px;margin:0"> min</div>
    <div class="acts"><button class="btn ghost" data-close>Annuler</button><button class="btn primary" id="ok">${icon("clock")} Enregistrer</button></div>`, (b, close) => {
    const send = m => { if (!(m > 0)) return; close(); act({ action: "track_duration", tid, minutes: m }, `Fin prévue à ${fmtHM(Date.now() + m * 60000)}`); };
    $$("[data-m]", b).forEach(c => c.onclick = () => send(Number(c.dataset.m)));
    $("#ok", b).onclick = () => send((Number($("#hh", b).value) || 0) * 60 + (Number($("#mm", b).value) || 0));
  });
}
function bindUnhint() {
  $$("[data-unhint]").forEach(b => b.onclick = ev => { ev.stopPropagation(); act({ action: "unmaybe", ids: [b.dataset.unhint] }, "« Peut-être » retiré"); });
}
function journalCard(dd, evs, today) {
  const st = S.status, parts = today ? st.running_parts || [] : [], tracks = today ? st.tracks || [] : [];
  const meas = today ? (st.measured || []).filter(m => m.power > 0) : [];
  const loose = today ? (st.pending || []).filter(p => !p.at) : [];
  const act_ = (label, attrs, primary) => `<button class="btn sm ${primary ? "primary" : ""}" ${attrs}>${label}</button>`;
  const liveRow = x => {
    const when = x.end ? `${fmtHM(x.start * 1000)} – ${fmtHM(x.end * 1000)}` : `depuis ${fmtHM(x.start * 1000)}`;
    const right = x.pending ? `<span class="pill ok">${icon("check")}${esc(x.pending.name)}</span><button class="icon-btn" data-cancel="${x.pending.t}" title="Retirer cette étiquette">${icon("x")}</button>`
      : (x.hint ? act_(`${icon("check")} ${esc(x.hint)} ?`, `data-quick-live="${x.start},${x.p}" data-name="${esc(x.hint)}"`, true) : "") + act_("C'est…", `data-live="${x.start},${x.p}"`);
    return `<div class="row"><span class="dot-live" style="${x.end ? "background:var(--muted);box-shadow:none" : ""}"></span><div class="grow"><div class="t1">${x.end ? "Vient de tourner" : "En marche"} · environ ${fmtW(x.p)}</div><div class="t2">${when}${x.n > 1 ? " · s'allume et s'éteint" : ""}${x.end ? " · sera rangé quand tout sera éteint" : ""}</div></div>${right}</div>`;
  };
  const trackRow = tr => {
    const pause = Math.max(0, Math.round((Date.now() / 1000 - tr.since) / 60));
    // « pas de forte consommation » : il peut laver, rincer, sécher ou attendre son thermostat, sans chauffer
    const state = tr.on ? `chauffe en ce moment · environ ${fmtW(tr.p)}` : `ne chauffe pas depuis ${pause} min (lavage, séchage, thermostat… il peut reprendre)`;
    const left = tr.planned_end ? Math.round((tr.planned_end - Date.now() / 1000) / 60) : null;
    const chrono = tr.planned_end ? `<div class="t2">${icon("clock")} fin prévue à <b>${fmtHM(tr.planned_end * 1000)}</b>${left > 0 ? ` · reste ${fmtDur(left * 60)}` : " · il attend que l'appareil s'arrête"}</div>` : "";
    return `<div class="row"><span class="dot-live" style="${tr.on ? "" : "background:var(--warn);box-shadow:none"}"></span><div class="grow"><div class="t1">${esc(tr.name)} · suivi depuis ${fmtHM(tr.at * 1000)}</div><div class="t2">${state} · allumé ${fmtDur(tr.on_s)} en tout${tr.user ? "" : " · reconnu tout seul"}</div>${chrono}</div><button class="btn sm ghost" data-tdur="${tr.id}" data-min="${tr.minutes || ""}" title="Temps restant du programme">${icon("clock")} Temps restant</button><button class="btn sm" data-tstop="${tr.id}">${icon("check")} C'est fini</button><button class="icon-btn" data-tcancel="${tr.id}" title="Ce n'était pas ça : arrêter le suivi">${icon("x")}</button></div>`;
  };
  const doneRow = e => {
    const g = G(e.gid), hyp = hypLabel(g);
    // morceau provisoire d'un appareil encore suivi : il sera remplacé à la fin du suivi, rien à faire
    // (le programme vérifie qu'elle tombe bien sur les moments où l'appareil suivi chauffe)
    if (e.tracked_by) return `<div class="row"><span class="num muted" style="width:42px">${fmtHM(e.start)}</span><span class="dot" style="background:var(--muted)"></span><div class="grow"><div class="t1">${esc(e.tracked_by)} · suivi en cours</div><div class="t2">${fmtW(e.p)} · rangé tout seul quand il sera éteint</div></div></div>`;
    const right = g && g.name ? `${g.status === "valide" ? `<span class="pill ok">${icon("sensor")}capteur</span>` : ""}${act_("Changer", `data-label="${e.id}"`)}`
      : ((e.guess && e.guess.length ? e.guess : hyp ? [hyp] : []).map((n, i) => act_(`${icon("check")} ${esc(n)} ?`, `data-quick="${e.id}" data-name="${esc(n)}"`, i === 0)).join("")) + act_("C'est…", `data-label="${e.id}"`);
    return `<div class="row click" data-go="#/groupe/${e.gid || "aucun"}?e=${e.id}"><span class="num muted" style="width:42px">${fmtHM(e.start)}</span><span class="dot" style="background:${col(g)}"></span><div class="grow"><div class="t1">${g && g.name ? esc(g.name) : "Pas encore reconnu"}</div><div class="t2">${fmtW(e.p)} · ${fmtDur(e.dur)}${e.cut ? " · arrêté par une coupure de courant" : ""}${e.hint ? ` · peut-être : ${esc(e.hint)} <button class="icon-btn" style="width:24px;height:24px;display:inline-grid;vertical-align:middle" data-unhint="${e.id}" title="Retirer ce « peut-être »">${icon("x")}</button>` : ""}</div></div>${right}</div>`;
  };
  return `<div class="card" style="margin-top:16px"><div class="card-h"><div><h2>Journal</h2><p>${today ? "Ce qui tourne en ce moment, puis ce qui s'est allumé aujourd'hui. Un clic suffit pour dire ce que c'est." : "Ce qui s'est allumé ce jour-là."}</p></div><div style="display:flex;gap:8px;flex-wrap:wrap">${today ? `<button class="btn sm ghost" id="b-now">${icon("edit")} Autre chose en ce moment…</button>` : ""}<button class="btn sm ghost" id="b-at">${icon("clock")} ${today ? "Plus tôt aujourd'hui…" : "Nommer à une heure…"}</button></div></div>
    ${tracks.length ? `<div class="list">${tracks.map(trackRow).join("")}</div>` : ""}
    ${parts.length ? `<div class="list">${parts.map(liveRow).join("")}</div>` : ""}
    ${meas.length ? `<p class="sub" style="font-size:12.5px;margin:6px 0">${meas.map(m => `${esc(m.name)} : ${fmtW(m.power)}`).join(" · ")} : déjà mesuré par sa prise, retiré des calculs.</p>` : ""}
    ${loose.length ? `<div class="changes" style="margin:6px 0">${loose.map(p => `<div>${icon("spark")}<span><b>${p.maybe ? "peut-être " : ""}${esc(p.name)}</b> (dit à ${fmtHM(p.t * 1000)}) : sera rangé à la fin de l'utilisation.</span><button class="icon-btn" data-cancel="${p.t}" title="Retirer cette étiquette">${icon("x")}</button></div>`).join("")}</div>` : ""}
    ${parts.length && evs.length ? `<div style="height:1px;background:var(--border);margin:8px 0"></div>` : ""}
    <div class="list">${evs.length ? evs.map(doneRow).join("") : (parts.length ? "" : `<div class="empty">Rien de gros pour l'instant.</div>`)}</div>
  </div>`;
}
function pageApercu() {
  const dd = dayData(S.day), today = S.day === NDAYS - 1;
  const named = dd.order.filter(k => G(k) && G(k).name).reduce((a, k) => a + dd.kwh[k], 0);
  const groups = dd.order.filter(k => G(k) && !G(k).name).reduce((a, k) => a + dd.kwh[k], 0);
  const unk = dd.kwh.unk || 0, base = dd.kwh.base || 0, tot = dd.tot || 1e-9;
  const pct = v => Math.round(v / tot * 100);
  const nowW = today && dd.limit ? dd.order.reduce((a, k) => a + dd.layers.get(k)[dd.limit - 1], 0) : null;
  const evs = dd.evs.filter(e => e.start >= dd.ds).sort((a, b) => b.start - a.start);
  const todo = evs.filter(e => !e.gid || !G(e.gid) || !G(e.gid).name).length;
  let peak = 0; for (let i = 0; i < dd.limit; i++) { let s = 0; for (const k of dd.order) s += dd.layers.get(k)[i]; peak = Math.max(peak, s); }
  return `
  <div class="page-h"><div><h1>Aperçu</h1><p class="sub">Ce que consomme la maison, appareil par appareil.</p></div>
    <div class="daypick"><button class="icon-btn" id="dprev" ${S.day === 0 ? "disabled" : ""}>${icon("left")}</button><span class="lbl">${dayLabel(S.day)}</span><button class="icon-btn" id="dnext" ${today ? "disabled" : ""}>${icon("right")}</button></div></div>
  <div class="grid g-tiles">
    <div class="card tile"><div class="lbl">${today ? "Consommé depuis minuit" : "Consommé ce jour-là"}</div><div class="val">${nf1.format(dd.tot)}<small>kWh</small></div><div class="foot">${fmtEur(dd.tot)}</div></div>
    <div class="card tile"><div class="lbl">Part expliquée par tes appareils</div><div class="val">${pct(named)}<small>%</small></div>
      <div class="meter" title="Appareils nommés ${pct(named)} %, groupes à nommer ${pct(groups)} %, toujours allumé ${pct(base)} %, pas reconnu ${pct(unk)} %"><span style="width:${pct(named)}%;background:var(--ink-2)"></span><span style="width:${pct(groups)}%;background:var(--muted)"></span><span style="width:${pct(base)}%;background:var(--base)"></span><span style="width:${pct(unk)}%;background:var(--unk);opacity:.5"></span></div>
      <div class="foot">+ ${pct(groups)} % pas encore nommé · toujours allumé ${pct(base)} %</div></div>
    <div class="card tile"><div class="lbl">Gros appareils allumés</div><div class="val">${evs.length}</div><div class="foot">${todo ? `${todo} pas encore identifiés` : evs.length ? "tous identifiés" : "aucun pour l'instant"}</div></div>
    <div class="card tile"><div class="lbl">${nowW !== null ? "Puissance en ce moment" : "Pic de la journée"}</div><div class="val">${nf0.format(nowW !== null ? nowW : peak)}<small>W</small></div><div class="foot">${nowW !== null ? `pic du jour : ${fmtW(peak)} · maximum de l'abonnement : 6 kVA` : "maximum de l'abonnement : 6 kVA"}</div></div>
  </div>
  ${journalCard(dd, evs, today)}
  <div class="card" style="margin-top:16px">
    <div class="card-h"><div><h2>Puissance de la maison</h2><p>Chaque couleur est un groupe d'appareil. En gris clair : ce qui reste toujours allumé (frigo, box internet, veilles…).</p></div><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><button class="btn sm ${S.grp ? "primary" : "ghost"}" id="b-grp" title="Les appareils d'une même catégorie (Électroménager…) en une seule couleur">${icon("check")} Par catégorie</button><div class="seg" id="rangeseg"></div></div></div>
    <div class="legend" style="margin-bottom:10px">${dd.order.map(k => `<span><i class="sw" style="background:${layerCol(k)}"></i>${esc(layerName(k))}</span>`).join("")}</div>
    <div class="chart" id="daychart"></div>
    <div class="lanes" id="lanes"></div>
    <div class="zoom-hint" id="zoominfo" style="margin-top:8px;display:flex;align-items:center;gap:6px"></div>
  </div>
  <div class="grid" style="margin-top:16px">
    <div class="card"><div class="card-h"><div><h2>Répartition</h2><p>Énergie par groupe sur la journée</p></div></div>
      ${[...dd.order].sort((a, b) => dd.kwh[b] - dd.kwh[a]).map(k => { const mx = Math.max(...Object.values(dd.kwh), 1e-9); return `<div class="hbar"><div class="nm"><span class="sw" style="background:${layerCol(k)}"></span><span>${esc(layerName(k))}</span></div><div class="trk"><div class="fill" style="width:${dd.kwh[k] / mx * 100}%;background:${layerCol(k)}"></div></div><div class="v">${fmtKwh(dd.kwh[k])} · ${fmtEur(dd.kwh[k])}</div></div>`; }).join("")}
    </div>
  </div>`;
}
function mountApercu() {
  renderRangeSeg(); drawDay($("#daychart"));
  if ($("#b-now")) $("#b-now").onclick = () => labelModal(null, true);
  if ($("#b-at")) $("#b-at").onclick = () => atModal(dayData(S.day).ds);
  $("#b-grp").onclick = () => { S.grp = !S.grp; try { localStorage.setItem("empreinte-grp", S.grp ? "1" : "0"); } catch (e) { } render(); };
  $$("[data-live]").forEach(b => b.onclick = () => { const [at, p] = b.dataset.live.split(",").map(Number); labelModal(null, { at, p }); });
  $$("[data-quick-live]").forEach(b => b.onclick = () => { const [at, p] = b.dataset.quickLive.split(",").map(Number); act({ action: "label_now", name: b.dataset.name, at, p }, `« ${b.dataset.name} » : je le suis jusqu'à ce qu'il s'éteigne`); });
  $$("[data-quick]").forEach(b => b.onclick = () => labelEvents([b.dataset.quick], b.dataset.name));
  $$("[data-cancel]").forEach(b => b.onclick = () => act({ action: "cancel_pending", t: Number(b.dataset.cancel) }, "Étiquette retirée"));
  $$("[data-tdur]").forEach(b => b.onclick = () => durationModal(b.dataset.tdur));
  $$("[data-tstop]").forEach(b => b.onclick = () => act({ action: "track_stop", tid: b.dataset.tstop }, "Noté : c'est fini. L'appareil est enregistré"));
  $$("[data-tcancel]").forEach(b => b.onclick = () => act({ action: "track_cancel", tid: b.dataset.tcancel }, "Suivi arrêté"));
  bindUnhint();
  $$("[data-label]").forEach(b => b.onclick = () => labelModal([b.dataset.label]));
  $("#dprev").onclick = () => { if (S.day > 0) { S.day--; render(); } };
  $("#dnext").onclick = () => { if (S.day < NDAYS - 1) { S.day++; render(); } };
}

// ---------- Page : Découvertes
function gcard(g) {
  const st = gstats(g.id), hyp = hypLabel(g);
  const pill = g.status === "valide" ? `<span class="pill ok">${icon("sensor")}Capteur actif</span>` : g.status === "nomme" ? (g.auto ? `<span class="pill new">Nommé tout seul</span>` : `<span class="pill">En apprentissage</span>`) : `<span class="pill new">Nouveau</span>`;
  const aty = st.m.filter(isAty).length;
  return `<div class="card gcard" data-go="#/groupe/${g.id}">
    <div class="gtitle"><div class="gicon" style="background:${col(g)}">${icon(gicon(g))}</div><div style="min-width:0;flex:1"><div class="nm">${esc(gname(g))}</div><div class="hy">${hyp ? `Ressemble à : <b style="color:var(--ink)">${esc(hyp)}</b> · ${Math.round((g.hyp_score || 0) * 100)} %` : g.name ? `Groupe ${g.letter}` : "Pas encore d'idée : dis-moi ce que c'est"}</div></div>${pill}</div>
    <div class="chart mini" data-ov="${g.id}"></div>
    <div class="gstats"><span><b>${fmtW(st.P)}</b>puissance</span><span><b>${fmtDur(st.D)}</b>durée</span><span><b>${st.n}</b>fois</span><span><b>${fmtKwh(st.kwhWeek)}</b>sur 7 j</span></div>
    ${hoursStrip(st.hours, col(g))}
    ${confBar(st.conf, g)}
    ${aty ? `<div class="atyp">${icon("alert")} ${aty} utilisation${aty > 1 ? "s" : ""} à vérifier</div>` : ""}
  </div>`;
}
function pageDecouvertes() {
  const nouv = S.groups.filter(g => g.status === "nouveau"), app = S.groups.filter(g => g.status === "nomme"), val = S.groups.filter(g => g.status === "valide");
  const unk = members(null);
  const by = (a, b) => gstats(b.id).n - gstats(a.id).n;
  const empty = !S.groups.length;
  return `
  <div class="page-h"><div><h1>Découvertes</h1><p class="sub">Le programme regroupe tout seul les utilisations qui se ressemblent. Toi, tu dis juste ce que c'est.</p></div></div>
  <div class="card"><div class="how">
    <div><span class="n">1</span><span><b>Il repère</b>chaque appareil qui s'allume (plus de 350 W d'un coup)</span></div>
    <div><span class="n">2</span><span><b>Il regroupe</b>par puissance, durée, forme de courbe et heure</span></div>
    <div><span class="n">3</span><span><b>Tu nommes</b>le groupe, et tu sors ce qui n'a rien à y faire</span></div>
    <div><span class="n">4</span><span><b>Il reconnaît</b>l'appareil tout seul et crée ses capteurs</span></div>
  </div></div>
  ${empty ? `<div class="card" style="margin-top:16px"><h2>Pas encore de groupe</h2><p class="sub">Le programme a repéré ${EVENTS.length} utilisation${EVENTS.length > 1 ? "s" : ""}. Il lui faut au moins 3 utilisations qui se ressemblent pour former un groupe. Laisse-le tourner 2 ou 3 jours : les groupes apparaîtront ici tout seuls.</p></div>` : ""}
  ${nouv.length ? `<div class="section-t"><h2>À identifier</h2><span>${nouv.length} groupe${nouv.length > 1 ? "s" : ""}</span></div><div class="groups">${nouv.sort(by).map(gcard).join("")}</div>` : ""}
  ${app.length ? `<div class="section-t"><h2>En apprentissage</h2><span>nommés, pas encore validés</span></div><div class="groups">${app.sort(by).map(gcard).join("")}</div>` : ""}
  ${unk.length ? `<div class="section-t"><h2>Pas encore reconnus</h2><span>${unk.length} utilisation${unk.length > 1 ? "s" : ""} encore seule${unk.length > 1 ? "s" : ""} ou trop irrégulière${unk.length > 1 ? "s" : ""}</span></div>
    <div class="card gcard" data-go="#/groupe/aucun"><div class="gtitle"><div class="gicon" style="background:var(--unk)">${icon("help")}</div><div style="flex:1;min-width:0"><div class="nm">Pas encore reconnus</div><div class="hy">Tu peux les ranger toi-même, ou attendre que des utilisations semblables arrivent.</div></div></div><div class="chart mini" data-ov="aucun"></div></div>` : ""}
  ${val.length ? `<div class="section-t"><h2>Validés</h2><span>reconnus automatiquement</span></div><div class="groups">${val.sort(by).map(gcard).join("")}</div>` : ""}`;
}
function mountOverlays() { $$("[data-ov]").forEach(el => drawOverlay(el, el.dataset.ov === "aucun" ? null : el.dataset.ov, true)); }

// ---------- Page : un groupe
function pageGroupe(gid) {
  const g = gid === "aucun" ? null : G(gid);
  if (gid !== "aucun" && !g) return `<a class="back" href="#/decouvertes">${icon("left")} Découvertes</a><div class="card empty">Ce groupe n'existe plus (fusionné ou supprimé).</div>`;
  const st = gstats(g ? gid : null);
  const hyp = hypLabel(g);
  const evs = [...st.m].sort((a, b) => (a.sim ?? 1) - (b.sim ?? 1) || b.start - a.start);
  const aty = g ? evs.filter(isAty).length : 0;
  const pill = !g ? "" : g.status === "valide" ? `<span class="pill ok">${icon("sensor")}Capteur actif</span>` : g.status === "nomme" ? `<span class="pill">En apprentissage</span>` : `<span class="pill new">Nouveau</span>`;
  return `
  <a class="back" href="#/decouvertes">${icon("left")} Découvertes</a>
  <div class="ghead"><div class="gicon" style="background:${col(g)}">${icon(gicon(g))}</div>
    <div><h1>${esc(gname(g))} ${pill}</h1><p class="sub">${g ? `Groupe ${g.letter} · ${st.n} utilisation${st.n > 1 ? "s" : ""}${st.last ? " · dernière : " + fmtWhen(st.last).toLowerCase() : ""}` : "Utilisations que le programme n'a pas encore su ranger"}</p></div>
    ${g ? `<div class="acts"><button class="btn" id="b-ren">${icon("edit")} ${g.name ? "Renommer" : "Nommer"}</button><button class="btn" id="b-mer">${icon("merge")} Fusionner</button><button class="btn" id="b-spl">${icon("split")} Séparer</button>${g.status !== "valide" ? `<button class="btn primary" id="b-val">${icon("check")} Valider</button>` : ""}</div>` : ""}
  </div>
  <div class="stack" style="margin-top:18px">
    ${hyp ? `<div class="hyp"><div class="q"><b>Hypothèse : ça ressemble à ${esc(hyp.toLowerCase())}</b> <span class="muted">(${Math.round((g.hyp_score || 0) * 100)} %)</span><ul>${hypReasons(g, st).map(r => `<li>${esc(r)}</li>`).join("")}</ul></div><div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn primary" id="b-yes">${icon("check")} Oui, c'est ça</button><button class="btn" id="b-no">Non, c'est…</button></div></div>` : ""}
    ${g && g.auto && g.status === "nomme" ? `<div class="hyp"><div class="q"><b>Le programme l'a nommé tout seul : ${esc(g.name.toLowerCase())}</b><ul>${hypReasons({ ...g, hyp: g.hyp }, st).map(r => `<li>${esc(r)}</li>`).join("")}</ul></div><div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn primary" id="b-ok">${icon("check")} C'est bon</button><button class="btn" id="b-no">Non, c'est…</button></div></div>` : ""}
    ${g && st.split ? `<div class="banner">${icon("split")}<div class="grow"><b>Deux niveaux de puissance repérés</b> : ${st.split.highN} fois vers ${fmtW(st.split.highP)} et ${st.split.lowN} fois vers ${fmtW(st.split.lowP)}. C'est peut-être un appareil à deux allures, ou deux appareils différents.</div><button class="btn sm" id="b-spl2">Séparer</button></div>` : ""}
    ${aty ? `<div class="banner">${icon("alert")}<div class="grow"><b>${aty} utilisation${aty > 1 ? "s" : ""} ne ressemble${aty > 1 ? "nt" : ""} pas aux autres.</b> Elles sont en haut de la liste et en gris sur le graphe. Si ce n'était pas ${g.name ? esc(g.name.toLowerCase()) : "cet appareil"}, clique sur « Pas ça ».</div></div>` : ""}
    ${proofBlock(g)}
    <div class="card"><div class="card-h"><div><h2>Toutes les courbes superposées</h2><p>Alignées sur leur début. Survole une ligne de la liste pour la retrouver.</p></div></div><div class="chart" id="ov"></div></div>
    ${g ? `<div class="feat"><div><span>Puissance typique</span><b>${fmtW(st.P)}</b></div><div><span>Durée typique</span><b>${fmtDur(st.D)}</b></div><div><span>Énergie par utilisation</span><b>${fmtKwh(st.WH / 1000)}</b></div><div><span>Forme</span><b>${st.shape}</b></div><div><span>Coût sur 7 jours</span><b>${fmtEur(st.kwhWeek)}</b></div></div>
    <div class="card"><div class="card-h"><div><h2>À quelle heure ?</h2><p>Nombre de utilisations par heure</p></div></div>${hourBars(st.hours, col(g))}</div>` : ""}
    <div class="card occ"><div class="card-h"><div><h2>Utilisations</h2><p>${g ? "Les moins ressemblantes en premier" : "Range-les dans un groupe, ou crée-en un nouveau"}</p></div>${evs.length ? `<button class="btn sm ghost" id="b-all">Tout sélectionner</button>` : ""}</div>
      <div class="list">${evs.map(e => { const s = e.sim; return `<div class="row" data-row="${e.id}"><input type="checkbox" data-sel="${e.id}" ${S.sel.has(e.id) ? "checked" : ""} aria-label="Sélectionner">${spark(e, isAty(e) ? "var(--muted)" : col(g))}<div class="grow"><div class="t1">${fmtWhen(e.start)}</div><div class="t2">${isAty(e) ? `<span class="atyp">${icon("alert")}différente des autres</span>` : fmtKwh(e.wh / 1000)}${e.probable ? ` · <span class="muted" title="Rangé tout seul d'après ce qui lui ressemble : corrige-le seulement si c'est faux">probable</span>` : ""}${e.hint ? ` · <span class="muted">peut-être : ${esc(e.hint)}</span><button class="icon-btn" style="width:24px;height:24px;display:inline-grid;vertical-align:middle" data-unhint="${e.id}" title="Retirer ce « peut-être »">${icon("x")}</button>` : ""}${e.split ? ` · <span title="Un autre appareil tournait en même temps : le programme les a séparés">séparée d'un autre appareil</span>` : ""}<span class="monly"> · ${fmtW(e.p)} · ${fmtDur(e.dur)}${s != null ? ` · ${Math.round(s * 100)} %` : ""}</span></div></div><div class="meta num"><span>${fmtW(e.p)}</span><span class="d">${fmtDur(e.dur)}</span></div>${s != null ? `<div class="simbar" title="Ressemblance avec le groupe"><i><b style="width:${Math.round(s * 100)}%"></b></i><span class="num">${Math.round(s * 100)}%</span></div>` : ""}${g ? (e.doubt ? `<button class="btn sm" data-doubt="${e.id}" title="Enlever le doute">${icon("help")} pas sûr</button>` : e.pinned ? `<span class="pill ok">${icon("check")}confirmée</span>` : `<button class="btn sm" data-yes1="${e.id}">${icon("check")} Oui</button><button class="btn sm ghost" data-doubt="${e.id}">Pas sûr</button>`) : `<button class="btn sm" data-label="${e.id}">C'est…</button>`}<button class="btn sm" data-move="${e.id}">${g ? "Pas ça" : "Ranger"}</button></div>`; }).join("") || `<div class="empty">Aucune utilisation.</div>`}</div>
      ${g && g.status !== "nouveau" ? `<div style="margin-top:12px;display:flex;justify-content:flex-end"><button class="btn sm ghost" id="b-del">${icon("trash")} Supprimer ce groupe</button></div>` : ""}
    </div>
  </div>
  <div id="selbar"></div>`;
}
// Les preuves de l'analyse, et ce qu'il manque pour que le programme soit sûr de lui.
const SURE = { n: 8, days: 3, coherence: 0.8, hyp: 0.85 };
function hourRanges(hours) {
  const tot = hours.reduce((a, b) => a + b, 0) || 1, top = hours.map((v, h) => [v, h]).filter(x => x[0]).sort((a, b) => b[0] - a[0]);
  let acc = 0; const keep = [];
  for (const [v, h] of top) { keep.push(h); acc += v; if (acc / tot >= 0.75) break; }
  keep.sort((a, b) => a - b);
  const rs = []; for (const h of keep) { const r = rs[rs.length - 1]; if (r && h === r[1] + 1) r[1] = h; else rs.push([h, h]); }
  return { txt: rs.map(([a, b]) => a === b ? `${a} h` : `${a} h – ${b + 1} h`).join(", "), pct: Math.round(acc / tot * 100) };
}
function proofBlock(g) {
  const p = g && g.proof; if (!p || !p.n) return "";
  const reg = p.p_spread <= 0.15 ? "très régulière" : p.p_spread <= 0.3 ? "assez régulière" : "variable";
  const shapes = Object.entries(p.shapes || {}).sort((a, b) => b[1] - a[1]), sh = shapes[0];
  const hr = hourRanges(p.hours);
  const line = (ok, html) => `<div>${icon(ok ? "check" : "alert")}<span>${html}</span></div>`;
  const facts = [
    line(p.n >= SURE.n, `Vu <b>${p.n} fois</b>, sur <b>${p.days} jour${p.days > 1 ? "s" : ""}</b> différent${p.days > 1 ? "s" : ""}`),
    line(p.p_spread <= 0.3, `Puissance <b>${reg}</b> : entre ${fmtW(p.p_lo)} et ${fmtW(p.p_hi)} la plupart du temps`),
    line(true, `Dure entre <b>${fmtDur(p.d_lo)}</b> et <b>${fmtDur(p.d_hi)}</b>`),
    line(hr.pct >= 60, `<b>${hr.pct} %</b> des utilisations vers <b>${hr.txt}</b>`),
    line(true, `${p.n - p.weekend} fois en semaine, ${p.weekend} fois le week-end`),
    sh ? line(sh[1] / p.n >= 0.7, `Même forme de courbe ${Math.round(sh[1] / p.n * 100)} % du temps : ${SHAPE_TXT[sh[0]].toLowerCase()}`) : "",
    line(p.coherence >= SURE.coherence, `Les utilisations se ressemblent à <b>${Math.round(p.coherence * 100)} %</b> en moyenne`),
    ...Object.entries(p.votes || {}).map(([n, k]) => line(k >= 3, `Tu as dit « peut-être <b>${esc(n)}</b> » pour ${k} utilisation${k > 1 ? "s" : ""} sur ${p.n}`)),
  ].join("");
  const gl = (p.guesses || []).map(([l, v]) => `${esc(l)} <b>${Math.round(v * 100)} %</b>`).join(" · ");
  const miss = [];
  if (p.n < SURE.n) miss.push(`le voir encore ${SURE.n - p.n} fois`);
  if (p.days < SURE.days) miss.push(`le voir sur ${SURE.days - p.days} jour${SURE.days - p.days > 1 ? "s" : ""} de plus`);
  if (p.coherence < SURE.coherence) miss.push("des utilisations qui se ressemblent davantage");
  if ((g.hyp_score || 0) < SURE.hyp) miss.push(`une hypothèse plus nette (aujourd'hui ${Math.round((g.hyp_score || 0) * 100)} %, il faut ${SURE.hyp * 100} %)`);
  const verdict = g.status === "valide" ? "Tu as confirmé cet appareil : il est reconnu automatiquement."
    : g.name && !g.auto ? "Tu as donné le nom : le programme s'en sert pour reconnaître les prochaines utilisations."
    : miss.length ? `Pas encore assez sûr pour le nommer tout seul. Il lui faut : ${miss.join(", ")}.`
    : "Tous les critères sont réunis : le programme est sûr de lui.";
  return `<div class="card"><div class="card-h"><div><h2>Preuves de l'analyse</h2><p>Sur quoi le programme s'appuie pour ce groupe</p></div></div>
    <div class="changes">${facts}</div>
    <div style="margin-top:14px;font-size:13px;color:var(--ink-2)">Appareils possibles : ${gl || "—"}</div>
    <div class="banner" style="margin-top:12px">${icon(miss.length && g.status !== "valide" && !(g.name && !g.auto) ? "alert" : "check")}<div class="grow">${verdict}</div></div></div>`;
}
function hourBars(hours, color) {
  const mx = Math.max(...hours, 1);
  return `<div style="display:grid;grid-template-columns:repeat(24,1fr);gap:3px;align-items:end;height:90px">${hours.map((v, h) => `<div title="${h} h : ${v} fois" style="height:${v ? Math.max(6, v / mx * 100) : 0}%;background:${color};border-radius:4px 4px 0 0;max-width:24px;justify-self:center;width:100%"></div>`).join("")}</div>
  <div style="height:1px;background:var(--axis)"></div><div class="hours-lbl"><span>0 h</span><span>6 h</span><span>12 h</span><span>18 h</span><span>23 h</span></div>`;
}
function renderSelbar(gid) {
  const el = $("#selbar"); if (!el) return;
  if (!S.sel.size) { el.innerHTML = ""; return; }
  el.innerHTML = `<div class="selbar"><b>${S.sel.size} sélectionnée${S.sel.size > 1 ? "s" : ""}</b>${gid !== "aucun" ? `<button class="btn sm" id="s-yes">${icon("check")} Oui, c'est ça</button>` : ""}<button class="btn sm" id="s-label">C'est…</button><button class="btn sm" id="s-move">${icon("move")} Déplacer vers…</button><button class="btn sm" id="s-new">${icon("plus")} Nouveau groupe</button>${gid !== "aucun" ? `<button class="btn sm" id="s-out">${icon("x")} Sortir du groupe</button>` : ""}<button class="btn sm" id="s-clr" aria-label="Désélectionner">${icon("x")}</button></div>`;
  const ids = () => [...S.sel];
  $("#s-move").onclick = e => moveMenu(e.currentTarget, ids(), gid === "aucun" ? null : gid);
  $("#s-new").onclick = () => moveEvents(ids(), "new");
  if ($("#s-yes")) $("#s-yes").onclick = () => confirmEvents(ids());
  $("#s-label").onclick = () => labelModal(ids());
  if ($("#s-out")) $("#s-out").onclick = () => moveEvents(ids(), null);
  $("#s-clr").onclick = () => { S.sel.clear(); $$("[data-sel]").forEach(c => c.checked = false); renderSelbar(gid); };
}
function mountGroupe(gid, q) {
  const g = gid === "aucun" ? null : G(gid);
  if (gid !== "aucun" && !g) return;
  drawOverlay($("#ov"), g ? gid : null, false);
  if (g) {
    $("#b-ren").onclick = () => renameModal(gid);
    $("#b-mer").onclick = () => mergeModal(gid);
    $("#b-spl").onclick = () => splitModal(gid);
    if ($("#b-spl2")) $("#b-spl2").onclick = () => splitModal(gid);
    if ($("#b-val")) $("#b-val").onclick = () => validateModal(gid);
    if ($("#b-yes")) $("#b-yes").onclick = () => confirmGroup(gid, hypLabel(g));
    if ($("#b-ok")) $("#b-ok").onclick = () => confirmGroup(gid);
    if ($("#b-no")) $("#b-no").onclick = () => renameModal(gid);
    if ($("#b-del")) $("#b-del").onclick = () => dissolveGroup(gid);
  }
  $$("[data-row]").forEach(r => { r.onpointerenter = () => highlightLine(r.dataset.row, true); r.onpointerleave = () => highlightLine(null, false); });
  $$("[data-move]").forEach(b => b.onclick = () => moveMenu(b, [b.dataset.move], g ? gid : null));
  $$("[data-yes1]").forEach(b => b.onclick = () => confirmEvents([b.dataset.yes1]));
  bindUnhint();
  $$("[data-doubt]").forEach(b => b.onclick = () => act({ action: "doubt", ids: [b.dataset.doubt] }, EVENTS.find(e => e.id === b.dataset.doubt)?.doubt ? "Doute enlevé" : "Noté « pas sûr » : elle reste là, mais le programme n'apprend pas dessus"));
  $$("[data-label]").forEach(b => b.onclick = () => labelModal([b.dataset.label]));
  $$("[data-sel]").forEach(c => c.onchange = () => { c.checked ? S.sel.add(c.dataset.sel) : S.sel.delete(c.dataset.sel); renderSelbar(gid); });
  if ($("#b-all")) $("#b-all").onclick = () => { const all = $$("[data-sel]"); const on = all.some(c => !c.checked); all.forEach(c => { c.checked = on; on ? S.sel.add(c.dataset.sel) : S.sel.delete(c.dataset.sel); }); renderSelbar(gid); };
  renderSelbar(gid);
  const e = q.get("e");
  if (e) { const r = $(`[data-row="${e}"]`); if (r) { r.scrollIntoView({ block: "center", behavior: "smooth" }); r.classList.add("flash"); highlightLine(e, true); setTimeout(() => highlightLine(null, false), 1800); } }
}

// ---------- Page : Habitudes
const cap = s => s[0].toUpperCase() + s.slice(1);
function pageHabitudes() {
  const hs = findHabits(EVENTS, dayOf, NDAYS, gid => !!(G(gid) && G(gid).name));
  // ce que tu as déjà nommé se range en bas : seules les habitudes encore inconnues restent en grand
  const known = hs.filter(hb => G(hb.gid) && G(hb.gid).name), todo = hs.filter(hb => !(G(hb.gid) && G(hb.gid).name));
  const perHour = Array(24).fill(0); EVENTS.forEach(e => { if (dayOf(e.start) >= 0) perHour[new Date(e.start).getHours()]++; });
  const mx = Math.max(...perHour, 1);
  const rhythm = `<div style="display:grid;grid-template-columns:repeat(24,1fr);gap:3px;align-items:end;height:90px">${perHour.map((v, h) => `<div title="${h} h : ${nf1.format(v / NDAYS)} utilisation(s) par jour en moyenne" style="height:${v ? Math.max(6, v / mx * 100) : 0}%;background:var(--s1);border-radius:4px 4px 0 0;max-width:24px;justify-self:center;width:100%"></div>`).join("")}</div>
    <div style="height:1px;background:var(--axis)"></div><div class="hours-lbl"><span>0 h</span><span>6 h</span><span>12 h</span><span>18 h</span><span>23 h</span></div>`;
  const card = hb => {
    const g = G(hb.gid), hints = habitHints(hb);
    const strip = `<div style="display:flex;gap:3px;flex-wrap:wrap">${Array.from({ length: NDAYS }, (_, d) => `<i title="${dayLabel(d)}" style="width:14px;height:14px;border-radius:3px;background:${hb.dayList.includes(d) ? "var(--s1)" : "var(--surface-2)"}"></i>`).join("")}</div>`;
    return `<div class="card hcard">
      <div class="gtitle"><div class="gicon" style="background:${g ? col(g) : "var(--s0)"}">${icon(g ? gicon(g) : "clock")}</div><div style="flex:1;min-width:0"><div class="q">${cap(periodOf(hb.hour))}, vers ${fmtH(hb.hour)}</div><div class="muted" style="font-size:12.5px">entre ${fmtH(hb.from)} et ${fmtH(hb.to)}</div></div></div>
      <div style="font-size:13.5px">Un appareil d'environ <b>${fmtW(hb.p)}</b> démarre pendant <b>${fmtDur(hb.dur)}</b>.</div>
      ${(() => { const v = {}; EVENTS.filter(e => hb.ids.includes(e.id) && e.hint).forEach(e => v[e.hint] = (v[e.hint] || 0) + 1); const t = Object.entries(v).sort((a, b) => b[1] - a[1])[0]; return t ? `<div style="font-size:13px">Tu as dit « peut-être <b>${esc(t[0])}</b> » ${t[1]} fois.</div>` : ""; })()}
      <div style="font-size:13px;color:var(--ink-2)">Vu <b>${hb.days} jours sur ${hb.nDays}</b> · ${hb.weekdays} en semaine, ${hb.weekend} le week-end</div>
      ${strip}
      <div class="banner">${icon(g && g.name ? "check" : "help")}<div class="grow">${g && g.name ? `C'est ton « <b>${esc(g.name)}</b> ».` : hints.length ? `Peut-être : <b>${hints.join(", ")}</b> ? À toi de me dire.` : "Je ne sais pas ce que c'est. À toi de me dire."}</div></div>
      <div class="acts"><button class="btn sm primary" data-hlabel="${hb.ids.join(",")}">C'est…</button><button class="btn sm" data-go="#/groupe/${hb.gid || "aucun"}?e=${hb.ids[0]}">Voir les utilisations</button></div>
    </div>`;
  };
  return `
  <div class="page-h"><div><h1>Habitudes</h1><p class="sub">Ce qui revient souvent à la même heure, plusieurs jours de suite. Ça aide à deviner ce que c'est.</p></div></div>
  <div class="card"><div class="card-h"><div><h2>Le rythme de la maison</h2><p>Nombre de gros appareils allumés selon l'heure, sur ${NDAYS} jour${NDAYS > 1 ? "s" : ""}</p></div></div>${rhythm}</div>
  ${todo.length ? `<div class="section-t"><h2>À identifier</h2><span>${todo.length}</span></div><div class="groups">${todo.map(card).join("")}</div>` : ""}
  ${known.length ? `<div class="section-t"><h2>Déjà reconnues</h2><span>${known.length}</span></div><div class="card"><div class="list">${known.map(hb => { const g = G(hb.gid); return `<div class="row click" data-go="#/groupe/${hb.gid}"><span class="dot" style="background:${col(g)}"></span><div class="grow"><div class="t1">${esc(g.name)} · ${periodOf(hb.hour)}, vers ${fmtH(hb.hour)}</div><div class="t2">${fmtW(hb.p)} · ${fmtDur(hb.dur)} · vu ${hb.days} jours sur ${hb.nDays}</div></div><span class="pill ok">${icon("check")}reconnu</span></div>`; }).join("")}</div></div>` : ""}
  ${hs.length ? ""
    : `<div class="card" style="margin-top:16px"><h2>Pas encore d'habitude</h2><p class="sub">Pour parler d'habitude, il faut voir le même appareil, à la même heure (à 45 minutes près), au moins ${HABIT.MIN_DAYS} jours différents. ${NDAYS} jour${NDAYS > 1 ? "s" : ""} observé${NDAYS > 1 ? "s" : ""} pour l'instant : reviens dans quelques jours.</p></div>`}`;
}

// ---------- Page : Carte des ressemblances
function pageCarte() {
  return `
  <div class="page-h"><div><h1>Carte des ressemblances</h1><p class="sub">Chaque point est une utilisation d'un appareil. Plus deux points sont proches, plus ils se ressemblent. Les zones sont les groupes trouvés par le programme.</p></div></div>
  <div class="card"><div class="card-h"><div><h2>Puissance et durée de chaque utilisation</h2><p>Clique sur un point pour ouvrir son groupe. Survole la légende pour isoler un groupe.</p></div></div>
    <div class="legend" id="sclegend" style="margin-bottom:10px"></div>
    <div class="chart" id="scatter"></div>
  </div>
  <div class="grid g-2" style="margin-top:16px">
    <div class="card"><h2>Ce que le programme compare</h2><p class="sub" style="margin-bottom:12px">Deux utilisations sont « proches » s'ils se ressemblent sur ces critères. Le poids indique leur importance.</p>
      ${[["Puissance consommée", 45], ["Durée", 20], ["Forme de la courbe (constante, qui varie, qui s'allume et s'éteint)", 25], ["Heure", 10]].map(([l, w]) => `<div class="hbar" style="grid-template-columns:1fr 90px 40px"><div class="nm"><span>${l}</span></div><div class="trk"><div class="fill" style="width:${w / 45 * 100}%;background:var(--s1)"></div></div><div class="v">${w} %</div></div>`).join("")}
    </div>
    <div class="card"><h2>Ses limites</h2><div class="changes" style="margin-top:10px">
      <div>${icon("alert")}<span>Le compteur donne une valeur toutes les ~13 s : un appareil allumé moins de 30 s est invisible.</span></div>
      <div>${icon("alert")}<span>Les petits appareils (box, chargeurs, lampes) sont comptés dans « Toujours allumé ».</span></div>
      <div>${icon("alert")}<span>Deux appareils de même puissance (chauffage soufflant et bouilloire) peuvent se confondre. Tes corrections servent à ça.</span></div>
      <div>${icon("alert")}<span>Quand deux appareils tournent en même temps, le programme les sépare d'après leurs sauts de puissance. Avec des plaques qui changent souvent de puissance, il n'y arrive pas toujours.</span></div>
    </div></div>
  </div>`;
}
function drawScatter(el) {
  const W = el.clientWidth, H = W < 600 ? 320 : 420, m = { l: 50, r: 14, t: 12, b: 30 }, pw = W - m.l - m.r, ph = H - m.t - m.b;
  if (pw < 60) return;
  const dmin = 15, dmax = 6 * 3600, pmax = niceMax(Math.max(2600, ...EVENTS.map(e => e.p)));
  const x = d => m.l + (Math.log(clamp(d, dmin, dmax)) - Math.log(dmin)) / (Math.log(dmax) - Math.log(dmin)) * pw;
  const y = p => m.t + ph - clamp(p, 0, pmax) / pmax * ph;
  let svg = `<svg viewBox="0 0 ${W} ${H}" height="${H}"><g class="ax">`;
  for (const v of yTicks(pmax)) svg += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}" class="${v === 0 ? "base" : ""}"/><text x="${m.l - 8}" y="${y(v) + 4}" text-anchor="end">${fmtTick(v)}</text>`;
  for (const [d, l] of [[30, "30 s"], [60, "1 min"], [300, "5 min"], [900, "15 min"], [3600, "1 h"], [3 * 3600, "3 h"]]) svg += `<line x1="${x(d)}" x2="${x(d)}" y1="${m.t}" y2="${m.t + ph}"/><text x="${x(d)}" y="${H - 8}" text-anchor="middle">${l}</text>`;
  svg += `</g>`;
  const gs = [...S.groups].sort((a, b) => a.slot - b.slot);
  for (const g of gs) {
    const mm = members(g.id); if (mm.length < 2) continue;
    const xs = mm.map(e => x(e.dur)), ys = mm.map(e => y(e.p));
    const mx = xs.reduce((a, b) => a + b) / xs.length, my = ys.reduce((a, b) => a + b) / ys.length;
    const sx = Math.sqrt(xs.reduce((a, b) => a + (b - mx) ** 2, 0) / xs.length), sy = Math.sqrt(ys.reduce((a, b) => a + (b - my) ** 2, 0) / ys.length);
    svg += `<ellipse data-g="${g.id}" cx="${mx}" cy="${my}" rx="${Math.max(14, sx * 2 + 8)}" ry="${Math.max(12, sy * 2 + 8)}" style="fill:${col(g)};opacity:.1"/>`;
  }
  for (const e of EVENTS) svg += `<circle data-e="${e.id}" data-g="${e.gid || "aucun"}" cx="${x(e.dur).toFixed(1)}" cy="${y(e.p).toFixed(1)}" r="5" style="fill:${col(G(e.gid))};stroke:var(--surface);stroke-width:2;cursor:pointer"/>`;
  if (!EVENTS.length) svg += `<text x="${m.l + pw / 2}" y="${m.t + ph / 2}" text-anchor="middle" style="font-size:13px;fill:var(--muted)">Aucune utilisation pour l'instant</text>`;
  el.innerHTML = svg + `</svg>`;
  $$("circle", el).forEach(c => {
    const e = EVENTS.find(x => x.id === c.dataset.e);
    c.addEventListener("pointerenter", ev => { c.setAttribute("r", 7); showTip(ev, `<div class="t">${esc(gname(G(e.gid)))}</div><div class="r"><span>Quand</span><b>${fmtWhen(e.start)}</b></div><div class="r"><span>Puissance</span><b>${fmtW(e.p)}</b></div><div class="r"><span>Durée</span><b>${fmtDur(e.dur)}</b></div>`); });
    c.addEventListener("pointermove", moveTip);
    c.addEventListener("pointerleave", () => { c.setAttribute("r", 5); hideTip(); });
    c.addEventListener("click", () => { hideTip(); go(`#/groupe/${e.gid || "aucun"}?e=${e.id}`); });
  });
  const lg = $("#sclegend");
  lg.innerHTML = gs.map(g => `<span class="chip" data-f="${g.id}"><i class="dot" style="background:${col(g)}"></i>${esc(gname(g))}</span>`).join("") + (members(null).length ? `<span class="chip" data-f="aucun"><i class="dot" style="background:var(--unk)"></i>Pas reconnu</span>` : "");
  const focus = f => $$("[data-g]", el).forEach(n => n.style.opacity = !f ? "" : n.dataset.g === f ? "" : (n.tagName === "ellipse" ? ".02" : ".12"));
  $$("[data-f]", lg).forEach(c => { c.onpointerenter = () => focus(c.dataset.f); c.onpointerleave = () => focus(null); c.onclick = () => go("#/groupe/" + c.dataset.f); });
}

// ---------- Page : Appareils
function stateOf(eid) { const s = hass && hass.states[eid]; return s ? `${s.state}${s.attributes.unit_of_measurement ? " " + s.attributes.unit_of_measurement : ""}` : "—"; }
function energyBlock(g) {
  const c = Math.round((g.conf || 0) * 100), sure = c >= 85;
  if (g.energy_dash) return `<div class="banner">${icon("check")}<div class="grow"><b>Dans le tableau Énergie</b> : visible dans ses graphiques.</div><button class="btn sm" data-energy-off="${g.id}">Retirer</button></div>`;
  return `<div class="banner">${icon(sure ? "check" : "alert")}<div class="grow">Certitude : <b>${c} %</b> (${g.n_uses || 0} utilisation${(g.n_uses || 0) > 1 ? "s" : ""}, dont ${g.n_sure || 0} confirmée${(g.n_sure || 0) > 1 ? "s" : ""} par toi${g.n_fixed ? `, ${g.n_fixed} corrigée${g.n_fixed > 1 ? "s" : ""}` : ""}). ${sure ? "Il est prêt à être affiché dans le tableau Énergie." : (g.n_uses || 0) < 8 ? `Pas encore assez vu : il en faut au moins 8 (encore ${8 - (g.n_uses || 0)}).` : "Pas encore très sûr : confirme quelques utilisations de plus."}</div><button class="btn sm ${sure ? "primary" : ""}" data-energy-on="${g.id}">Ajouter au tableau Énergie</button></div>`;
}
function catCard(c, i) {
  const mem = S.groups.filter(g => g.cat === c.id);
  const days = Math.min(7, NDAYS), d0 = NDAYS - days;
  let wk = 0; for (const g of mem) for (const e of members(g.id)) if (dayOf(e.start) >= d0) wk += e.wh / 1000;
  const btn = c.dash ? `<button class="btn sm" data-cat-off="${c.id}">Retirer du tableau Énergie</button>`
    : `<button class="btn sm ${mem.length ? "primary" : ""}" data-cat-on="${c.id}" ${mem.length ? "" : "disabled"}>Ajouter au tableau Énergie</button>`;
  return `<div class="card stack" style="gap:12px">
    <div class="dev-h"><div class="gicon" style="background:${catCol(c.id)}">${icon("sensor")}</div><div class="grow"><div style="font-weight:600;font-size:15px">${esc(c.name)}</div><div class="muted" style="font-size:12.5px">${mem.length ? mem.map(g => esc(gname(g))).join(" · ") : "Aucun appareil pour l'instant : choisis-la dans la carte d'un appareil."}</div></div>${c.dash ? `<span class="pill ok">${icon("check")}Dans le tableau Énergie</span>` : ""}</div>
    <div class="kv"><div><span>7 jours</span><b>${fmtKwh(wk)}</b></div><div><span>Coût</span><b>${fmtEur(wk)}</b></div><div><span>En ce moment</span><b>${fmtW(c.power || 0)}</b></div>${c.dash ? `<div><span>Depuis l'ajout</span><b>${fmtKwh(c.total || 0)}</b></div>` : ""}</div>
    <p class="sub" style="font-size:12.5px;margin:0">Ses appareils sont toujours reconnus un par un, mais comptés ensemble : s'il les confond entre eux, le total reste juste. ${c.dash ? "Dans le tableau Énergie, ils n'apparaissent qu'ici (pas une deuxième fois chacun)." : "Compté dans le tableau Énergie à partir du moment où tu l'ajoutes."}</p>
    <div style="display:flex;gap:8px;flex-wrap:wrap">${btn}<button class="btn sm ghost" data-cat-ren="${c.id}">${icon("edit")} Renommer</button><button class="btn sm ghost" data-cat-del="${c.id}">${icon("x")} Supprimer</button></div>
  </div>`;
}
function energyLogCard() {
  const log = (S.status.energy_log || []).slice().reverse();
  const inDash = S.groups.some(g => g.energy_dash) || (S.status.categories || []).some(c => c.dash);
  if (!log.length && !inDash) return "";
  // seulement ce qui est vraiment dans le tableau Énergie (un appareil compté avec sa catégorie n'y est pas une 2e fois)
  const now = Date.now() / 1000, show = log.filter(x => now - x.t < 7 * 86400 && (x.in_dash || x.removed));
  const row = x => {
    const use = x.start ? `utilisation de ${fmtHM(x.start * 1000)}${x.end ? ` à ${fmtHM(x.end * 1000)}` : ""}` : "utilisation";
    const btn = x.removed ? `<span class="pill">${x.auto ? "retiré tout seul (corrigé)" : "retiré"}</span><button class="btn sm ghost" data-eredo="${x.id}">Remettre</button>`
      : now - x.t < 600 ? `<span class="muted" style="font-size:12px">retirable dans ${Math.ceil((600 - (now - x.t)) / 60)} min</span>`
      : `<button class="btn sm" data-eundo="${x.id}">${icon("x")} Retirer</button>`;
    return `<div class="row"><span class="num muted" style="width:92px">${fmtWhen(x.t * 1000)}</span><div class="grow"><div class="t1" style="${x.removed ? "text-decoration:line-through;opacity:.6" : ""}">${esc(x.name)} · ${fmtKwh(x.kwh)}</div><div class="t2">${use}${x.entity ? "" : " · capteur introuvable"}</div></div>${btn}</div>`;
  };
  return `<div class="card" style="margin-bottom:22px"><div class="card-h"><div><h2>Envoyé au tableau Énergie</h2><p>Chaque utilisation comptée dans le tableau Énergie, au moment où elle l'a été. Quand tu corriges une utilisation (« C'est… », « Changer »), elle est retirée toute seule du tableau Énergie, puis comptée dans le bon appareil s'il y est. « Retirer » sert si tu veux l'enlever sans la corriger.</p></div></div>
    <div class="list">${show.length ? show.map(row).join("") : `<div class="empty">Rien d'envoyé cette semaine.</div>`}</div></div>`;
}
function catsBlock() {
  const cs = (S.status.categories || []);
  return `<div class="card-h" style="margin:0 0 10px"><div><h2>Catégories</h2><p>Des appareils comptés ensemble, comme tu veux (Électroménager, Cuisine…). Un appareil n'est que dans une seule catégorie.</p></div><button class="btn sm" id="b-cat-new">${icon("plus")} Nouvelle catégorie</button></div>
  <div class="groups" style="grid-template-columns:repeat(auto-fill,minmax(340px,1fr));margin-bottom:22px">${cs.map(catCard).join("")}</div>
  <h2 style="margin:0 0 10px">Appareils</h2>`;
}
// ---------- Prises partagées (lave-linge + sèche-linge sur la même prise…)
function sharedBlock() {
  const plugs = S.status.shared || [];
  if (!plugs.length) return "";
  return plugs.map(pl => {
    const names = pl.apps.map(a => a.name);
    const o = pl.open;
    const now = o ? `<div class="banner">${icon("clock")}<div class="grow">En marche depuis ${fmtHM(o.start * 1000)} · ${fmtW(o.power)} · ${o.guess ? `c'est sans doute <b>${esc(o.guess)}</b>` : "trop tôt pour dire lequel"}</div></div>` : "";
    const apps = pl.apps.map(a => `<div class="card" style="padding:12px;box-shadow:none">
        <div class="dev-h"><div class="gicon" style="background:var(--good)">${icon(iconFor(a.name))}</div><div class="grow"><div style="font-weight:600">${esc(a.name)}${a.running ? ` <span class="pill ok">en marche</span>` : ""}</div><div class="muted" style="font-size:12.5px">${a.n} programme${a.n > 1 ? "s" : ""} reconnu${a.n > 1 ? "s" : ""}</div></div></div>
        <div class="kv" style="margin-top:10px"><div><span>7 jours</span><b>${fmtKwh(a.kwh7)}</b></div><div><span>Coût</span><b>${fmtEur(a.kwh7)}</b></div><div><span>Depuis le début</span><b>${fmtKwh(a.kwh)}</b></div></div>
        <div class="ent" style="margin-top:10px">${(a.entities || []).map(id => `<div><b>${esc(id)}</b><span>${esc(stateOf(id))}</span></div>`).join("") || "<div>Capteurs en cours de création…</div>"}</div>
        <div style="margin-top:10px">${a.dash ? `<button class="btn sm" data-sh-dash="${esc(a.key)}" data-on="0">${icon("x")} Retirer du tableau Énergie</button>` : `<button class="btn sm primary" data-sh-dash="${esc(a.key)}" data-on="1">${icon("plus")} Ajouter au tableau Énergie</button>`}</div>
      </div>`).join("");
    const runs = pl.runs.map(r => {
      const st = r.user ? `<span class="pill ok">${icon("check")}${esc(r.name)}</span>`
        : r.name ? `<span class="muted">${esc(r.name)} · ${Math.round(r.conf * 100)} %</span>`
        : r.guess ? `<span class="atyp">peut-être ${esc(r.guess)} ?</span>` : `<span class="atyp">${icon("help")}à confirmer</span>`;
      const btns = names.map(n => `<button class="btn sm ${(r.user && r.name === n) ? "primary" : ""}" data-sh-run="${r.id}" data-name="${esc(n)}" ${(r.user && r.name === n) ? "disabled" : ""}>${esc(n)}</button>`).join("");
      return `<div class="row"><div class="grow"><div class="t1">${fmtWhen(r.start * 1000)}</div><div class="t2">${fmtDur(r.D)} · ${fmtKwh(r.wh / 1000)} · ${st}</div></div><div style="display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end">${btns}</div></div>`;
    }).join("") || `<div class="empty">Aucun programme terminé pour l'instant : le premier apparaîtra ici dès qu'il sera fini.</div>`;
    return `<div class="card stack" style="gap:14px;margin-bottom:16px">
      <div class="card-h"><div><h2>${icon("plug")} Prise partagée : ${esc(pl.name)}</h2><p>${names.map(esc).join(" ou ")} : jamais en même temps. Empreinte lit la courbe de la prise et reconnaît lequel a tourné. Un clic sur le bon nom corrige, et il apprend.</p></div><span class="pill">${fmtW(pl.power)} en ce moment</span></div>
      ${now}
      <div class="groups" style="grid-template-columns:repeat(auto-fill,minmax(260px,1fr))">${apps}</div>
      <div><p class="sub" style="font-size:12.5px;margin:0 0 6px">Derniers programmes</p><div class="list">${runs}</div></div>
    </div>`;
  }).join("");
}
function bindShared() {
  $$("[data-sh-run]").forEach(b => b.onclick = () => act({ action: "shared_label", rid: b.dataset.shRun, name: b.dataset.name }, `Noté : « ${b.dataset.name} ». Les programmes qui lui ressemblent suivent`));
  $$("[data-sh-dash]").forEach(b => b.onclick = () => act({ action: "shared_dash", key: b.dataset.shDash, on: b.dataset.on === "1" }, b.dataset.on === "1" ? "Ajouté au tableau Énergie" : "Retiré du tableau Énergie"));
}

function pageAppareils() {
  const gs = S.groups.filter(g => g.name || g.status === "valide").sort((a, b) => (a.status === "valide" ? 0 : 1) - (b.status === "valide" ? 0 : 1) || gstats(b.id).kwhWeek - gstats(a.id).kwhWeek);
  if (!gs.length) return `<div class="page-h"><div><h1>Appareils</h1><p class="sub">Les appareils que tu as nommés apparaîtront ici.</p></div></div>${sharedBlock()}<div class="card empty">Aucun appareil nommé pour l'instant. Commence par les Découvertes.</div>`;
  return `
  <div class="page-h"><div><h1>Appareils</h1><p class="sub">Les appareils que tu as nommés. Ceux qui sont validés ont leurs capteurs dans Home Assistant.</p></div></div>
  ${energyLogCard()}
  ${sharedBlock()}
  ${catsBlock()}
  <div class="groups" style="grid-template-columns:repeat(auto-fill,minmax(340px,1fr))">${gs.map(g => {
    const st = gstats(g.id), days = Math.min(7, NDAYS), d0 = NDAYS - days;
    const perDay = Array(days).fill(0); st.m.forEach(e => { const d = dayOf(e.start) - d0; if (d >= 0) perDay[d] += e.wh / 1000; });
    const mx = Math.max(...perDay, 0.01);
    return `<div class="card stack" style="gap:14px">
      <div class="dev-h"><div class="gicon" style="background:${col(g)}">${icon(gicon(g))}</div><div class="grow"><div style="font-weight:600;font-size:15px">${esc(gname(g))}</div><div class="muted" style="font-size:12.5px">Dernière marche : ${st.last ? fmtWhen(st.last).toLowerCase() : "—"}</div></div>${g.status === "valide" ? `<span class="pill ok">${icon("sensor")}Capteur actif</span>` : `<span class="pill">En apprentissage</span>`}</div>
      <div class="kv"><div><span>7 jours</span><b>${fmtKwh(st.kwhWeek)}</b></div><div><span>Coût</span><b>${fmtEur(st.kwhWeek)}</b></div><div><span>Utilisations</span><b>${st.n}</b></div><div><span>Puissance</span><b>${fmtW(st.P)}</b></div></div>
      <div><div style="display:grid;grid-template-columns:repeat(${days},1fr);gap:6px;align-items:end;height:60px">${perDay.map((v, i) => `<div title="${dayLabel(d0 + i)} : ${fmtKwh(v)}" style="height:${v ? Math.max(5, v / mx * 100) : 0}%;background:${col(g)};border-radius:4px 4px 0 0;width:100%;max-width:24px;justify-self:center"></div>`).join("")}</div><div style="height:1px;background:var(--axis)"></div>
        <div style="display:grid;grid-template-columns:repeat(${days},1fr);font-size:10.5px;color:var(--muted);text-align:center;margin-top:3px">${perDay.map((v, i) => `<span>${dfDay.format(dayStartMs(d0 + i)).split(" ")[0].replace(".", "")}</span>`).join("")}</div></div>
      ${confBar(st.conf, g)}
      <label class="muted" style="display:flex;gap:8px;align-items:center;font-size:13px">Catégorie <select data-cat-of="${g.id}" style="font:inherit;padding:4px 8px;border-radius:8px;border:1px solid var(--border);background:var(--card);color:var(--ink)"><option value="">Aucune</option>${(S.status.categories || []).map(c => `<option value="${c.id}" ${g.cat === c.id ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select></label>
      ${((S.status.categories || []).find(c => c.id === g.cat) || {}).dash
        ? `<div class="banner">${icon("check")}<div class="grow">Compté dans le tableau Énergie avec « <b>${esc(((S.status.categories || []).find(c => c.id === g.cat) || {}).name)}</b> », pas une deuxième fois tout seul. Pour l'y mettre à part, change sa catégorie en « Aucune ».</div></div>`
        : g.status === "valide" ? energyBlock(g) : ""}
      ${g.status === "valide" ? `<div class="ent">${(g.entities || []).map(id => `<div><b>${esc(id)}</b><span>${esc(stateOf(id))}</span></div>`).join("") || "<div>Capteurs en cours de création…</div>"}</div>`
        : `<div style="display:flex;gap:8px"><a class="btn sm" href="#/groupe/${g.id}">Voir les utilisations</a><button class="btn sm primary" data-val="${g.id}">${icon("check")} Valider</button></div>`}
    </div>`;
  }).join("")}</div>`;
}

// ---------- Page : Rapport
function reportDays() { const n = Math.min(7, NDAYS); return Array.from({ length: n }, (_, i) => NDAYS - n + i); }
function pageRapport() {
  const ds = reportDays(), days = ds.map(dayData);
  const tot = days.reduce((a, d) => a + d.tot, 0) || 1e-9;
  const per = {}; days.forEach(d => d.order.forEach(k => per[k] = (per[k] || 0) + d.kwh[k]));
  const keys = Object.keys(per).sort((a, b) => per[b] - per[a]);
  const mx = Math.max(...Object.values(per), 1e-9);
  const named = keys.filter(k => G(k) && G(k).name).reduce((a, k) => a + per[k], 0);
  const base = per.base || 0;
  const hours = days.reduce((a, d) => a + d.hours, 0) || 1;
  const heat = days.map(d => { const out = Array(24).fill(null); const ph = 3600 / STEP; for (let h = 0; h < 24; h++) { if (h * ph >= d.limit) break; let s = 0; for (let i = h * ph; i < Math.min((h + 1) * ph, d.limit); i++) for (const k of d.order) s += d.layers.get(k)[i]; out[h] = s * STEP / 3600 / 1000; } return out; });
  const hmax = Math.max(1e-9, ...heat.flat().filter(v => v !== null));
  const qcol = v => v === null ? "transparent" : `var(--q${clamp(Math.ceil(v / hmax * 7), 1, 7)})`;
  const hyps = S.groups.filter(g => g.status === "nouveau");
  const ready = S.groups.filter(g => g.status === "nomme" && (g.auto || g.conf >= 0.8));
  const weekAgo = Date.now() - 7 * 864e5;
  const newG = S.groups.filter(g => g.created * 1000 > weekAgo).length;
  const valid = S.groups.filter(g => g.status === "valide");
  const atyG = S.groups.filter(g => g.name && members(g.id).some(isAty));
  const splits = S.groups.filter(g => gstats(g.id).split);
  const baseW = base / hours * 1000;
  return `
  <div class="page-h"><div><h1>Rapport de la semaine</h1><p class="sub">Du ${dfDay.format(dayStartMs(ds[0]))} au ${dfDay.format(dayStartMs(ds[ds.length - 1])).replace(/\.$/, "")}. Ce sont des hypothèses, pas des certitudes : à toi de confirmer.</p></div><button class="btn" id="b-print">${icon("report")} Imprimer</button></div>
  <div class="grid g-tiles">
    <div class="card tile"><div class="lbl">Consommation</div><div class="val">${nf1.format(tot)}<small>kWh</small></div><div class="foot">${fmtEur(tot)} sur ${ds.length} jour${ds.length > 1 ? "s" : ""}</div></div>
    <div class="card tile"><div class="lbl">Attribuée à un appareil nommé</div><div class="val">${Math.round(named / tot * 100)}<small>%</small></div><div class="foot">${fmtKwh(named)}</div></div>
    <div class="card tile"><div class="lbl">Toujours allumé</div><div class="val">${nf0.format(baseW)}<small>W</small></div><div class="foot">${fmtKwh(base)} · ${fmtEur(base)} consommés par ce qui ne s'éteint jamais</div></div>
    <div class="card tile"><div class="lbl">Groupes à confirmer</div><div class="val">${hyps.length}</div><div class="foot">${ready.length ? ready.length + " prêt(s) à devenir capteur" : "aucun prêt à valider"}</div></div>
  </div>
  ${(() => {
    // fiabilité mesurée : ce que le programme avait rangé tout seul, et ce que tu en as dit ensuite
    const v = S.status.verdicts || { n: 0, ok: 0, errors: [] };
    if (!v.n) return `<div class="banner" style="margin-top:16px">${icon("check")}<div class="grow"><b>Fiabilité mesurée</b> : pas encore de mesure. Chaque fois que tu confirmes (« Oui, c'est ça ») ou corriges une utilisation rangée toute seule, le programme note s'il avait raison.</div></div>`;
    return `<div class="banner" style="margin-top:16px">${icon(v.ok === v.n ? "check" : "alert")}<div class="grow"><b>Fiabilité mesurée sur tes réponses</b> (30 jours) : <b>${v.ok} juste${v.ok > 1 ? "s" : ""} sur ${v.n}</b> rangement${v.n > 1 ? "s" : ""} automatique${v.n > 1 ? "s" : ""} que tu as vérifié${v.n > 1 ? "s" : ""}.${v.errors.length ? ` Erreurs : ${v.errors.map(x => `${fmtWhen(x.start * 1000)}, « ${esc(x.auto)} » au lieu de « ${esc(x.user || "pas reconnu")} »`).join(" ; ")}.` : ""}</div></div>`;
  })()}
  ${hyps.length || ready.length ? `<div class="section-t"><h2>Ce que le programme pense avoir trouvé</h2><span>réponds en un clic</span></div>
  <div class="groups">${ready.map(g => `<div class="card hcard"><div class="gtitle"><div class="gicon" style="background:${col(g)}">${icon(gicon(g))}</div><div style="flex:1;min-width:0"><div class="q">${g.auto ? `Le programme pense que c'est : ${esc(g.name.toLowerCase())}` : `${esc(g.name)} est prêt`}</div><div class="muted" style="font-size:12.5px">${gstats(g.id).n} utilisations qui se ressemblent</div></div></div>${confBar(g.conf)}<div class="acts"><button class="btn sm primary" data-ok="${g.id}">${icon("check")} C'est bon</button>${g.auto ? `<button class="btn sm" data-no="${g.id}">Non, c'est…</button>` : ""}<a class="btn sm" href="#/groupe/${g.id}">Voir</a></div></div>`).join("")}
  ${hyps.map(g => { const st = gstats(g.id), h = hypLabel(g); return `<div class="card hcard"><div class="gtitle"><div class="gicon" style="background:${col(g)}">${icon("help")}</div><div style="flex:1;min-width:0"><div class="q">Groupe ${g.letter}${h ? ` ressemble à : ${esc(h.toLowerCase())}` : " : appareil inconnu"}</div><div class="muted" style="font-size:12.5px">${st.n} fois · ${fmtW(st.P)} · ${fmtDur(st.D)}</div></div></div><div class="chart mini" data-ov="${g.id}"></div><ul>${hypReasons(g, st).slice(0, 3).map(r => `<li>${esc(r)}</li>`).join("")}</ul>${confBar(st.conf, g)}<div class="acts">${h ? `<button class="btn sm primary" data-yes="${g.id}">${icon("check")} Oui</button><button class="btn sm" data-no="${g.id}">Non, c'est…</button>` : `<button class="btn sm primary" data-no="${g.id}">${icon("edit")} Nommer</button>`}<a class="btn sm ghost" href="#/groupe/${g.id}">Détails</a></div></div>`; }).join("")}</div>` : ""}
  <div class="grid g-2" style="margin-top:16px">
    <div class="card"><div class="card-h"><div><h2>Énergie par groupe</h2><p>${ds.length} jour${ds.length > 1 ? "s" : ""}, au prix de ${String(PRIX).replace(".", ",")} €/kWh</p></div></div>
      ${keys.map(k => `<div class="hbar"><div class="nm"><span class="sw" style="background:${layerCol(k)}"></span><span>${esc(layerName(k))}</span></div><div class="trk"><div class="fill" style="width:${per[k] / mx * 100}%;background:${layerCol(k)}"></div></div><div class="v">${fmtKwh(per[k])} · ${fmtEur(per[k])}</div></div>`).join("")}
    </div>
    <div class="card"><div class="card-h"><div><h2>Quand la maison consomme</h2><p>kWh par heure, plus foncé = plus consommé</p></div></div>
      <div class="heat" id="heat">${heat.map((row, i) => `<span class="dl">${dfDay.format(dayStartMs(ds[i])).split(" ")[0].replace(".", "")}</span>${row.map((v, h) => `<i data-v="${v === null ? "" : v}" data-l="${dayLabel(ds[i])}, ${h} h" style="background:${qcol(v)}"></i>`).join("")}`).join("")}
      <span></span>${Array.from({ length: 24 }, (_, h) => `<span style="text-align:center;font-size:10px">${h % 6 === 0 ? h : ""}</span>`).join("")}</div>
      <div style="display:flex;align-items:center;gap:6px;margin-top:10px;font-size:11.5px;color:var(--muted)">peu ${[1, 2, 3, 4, 5, 6, 7].map(i => `<i style="width:16px;height:10px;border-radius:2px;background:var(--q${i})"></i>`).join("")} beaucoup</div>
    </div>
  </div>
  <div class="grid g-2" style="margin-top:16px">
    <div class="card"><h2>Détail</h2><table class="t" style="margin-top:8px"><thead><tr><th>Groupe</th><th class="r">Utilisations</th><th class="r">kWh</th><th class="r">Coût</th></tr></thead><tbody>
      ${keys.map(k => `<tr><td><span style="display:inline-flex;align-items:center;gap:7px"><i class="sw" style="background:${layerCol(k)}"></i>${esc(layerName(k))}</span></td><td class="r">${k === "base" ? "—" : days.reduce((a, d) => a + d.evs.filter(e => (e.gid || "unk") === k && e.start >= d.ds).length, 0)}</td><td class="r">${nf2.format(per[k])}</td><td class="r">${fmtEur(per[k])}</td></tr>`).join("")}
      <tr><td><b>Total</b></td><td></td><td class="r"><b>${nf2.format(tot)}</b></td><td class="r"><b>${fmtEur(tot)}</b></td></tr></tbody></table></div>
    <div class="card"><h2>Ce qui a changé</h2><div class="changes" style="margin-top:10px">
      <div>${icon("spark")}<span><b>${newG} nouveau${newG > 1 ? "x" : ""} groupe${newG > 1 ? "s" : ""}</b> repéré${newG > 1 ? "s" : ""} cette semaine.</span></div>
      ${valid.length ? `<div>${icon("sensor")}<span><b>${valid.map(g => esc(g.name || "Groupe " + g.letter)).join(", ")}</b> reconnu${valid.length > 1 ? "s" : ""} automatiquement.</span></div>` : ""}
      ${atyG.map(g => `<div>${icon("alert")}<span><b>${esc(g.name)}</b> : ${members(g.id).filter(isAty).length} utilisation(s) ne ressemblent pas aux autres. À vérifier.</span></div>`).join("")}
      ${splits.map(g => { const sp = gstats(g.id).split; return `<div>${icon("split")}<span><b>${esc(gname(g))}</b> semble avoir deux allures (environ ${fmtW(sp.lowP)} et ${fmtW(sp.highP)}).</span></div>`; }).join("")}
      <div>${icon("zap")}<span>Ce qui reste toujours allumé consomme environ <b>${nf0.format(baseW)} W</b>, soit environ ${fmtEur(baseW / 1000 * 24 * 365)} par an. C'est souvent la première piste d'économie.</span></div>
    </div></div>
  </div>`;
}
function mountRapport() {
  mountOverlays();
  $("#b-print").onclick = () => window.print();
  $$("[data-val]").forEach(b => b.onclick = () => validateModal(b.dataset.val));
  $$("[data-yes]").forEach(b => b.onclick = () => confirmGroup(b.dataset.yes, hypLabel(G(b.dataset.yes))));
  $$("[data-ok]").forEach(b => b.onclick = () => confirmGroup(b.dataset.ok));
  $$("[data-no]").forEach(b => b.onclick = () => renameModal(b.dataset.no));
  $$("#heat i[data-l]").forEach(c => { if (c.dataset.v === "") return; c.onpointerenter = ev => showTip(ev, `<div class="t">${esc(c.dataset.l)}</div><div class="r"><span>Consommé</span><b>${fmtKwh(+c.dataset.v)}</b></div>`); c.onpointermove = moveTip; c.onpointerleave = hideTip; });
}

// ---------- Rendu
let renderSeq = 0;
async function render() {
  hideTip(); closeMenu();
  const seq = ++renderSeq;
  const { page, arg, q } = route();
  const v = $("#view");
  if (S.error) { v.innerHTML = `<div class="card err" style="margin-top:20px"><h2>Impossible de lire les données</h2><p class="sub">${esc(S.error)}</p></div>`; return; }
  if (!S.loaded) return;
  renderNav();
  try {
    if (page === "apercu" || page === "") await ensureDay(S.day);
    if (page === "rapport") await Promise.all(reportDays().map(ensureDay));
  } catch (err) { v.innerHTML = `<div class="card err"><h2>Erreur</h2><p class="sub">${esc(err.message || err)}</p></div>`; return; }
  if (seq !== renderSeq) return;
  if (page === "groupe") { v.innerHTML = pageGroupe(arg); mountGroupe(arg, q); }
  else if (page === "decouvertes") { v.innerHTML = pageDecouvertes(); mountOverlays(); }
  else if (page === "habitudes") { v.innerHTML = pageHabitudes(); $$("[data-hlabel]").forEach(b => b.onclick = () => labelModal(b.dataset.hlabel.split(","))); }
  else if (page === "carte") { v.innerHTML = pageCarte(); drawScatter($("#scatter")); }
  else if (page === "appareils") {
    v.innerHTML = pageAppareils();
    bindShared();
    $$("[data-val]").forEach(b => b.onclick = () => validateModal(b.dataset.val));
    $$("[data-energy-on]").forEach(b => b.onclick = () => act({ action: "energy_add", gid: b.dataset.energyOn }, `« ${gname(G(b.dataset.energyOn))} » ajouté dans le tableau Énergie`));
    $$("[data-energy-off]").forEach(b => b.onclick = () => act({ action: "energy_remove", gid: b.dataset.energyOff }, `« ${gname(G(b.dataset.energyOff))} » retiré du tableau Énergie`));
    const cname = cid => ((S.status.categories || []).find(c => c.id === cid) || {}).name || "";
    $$("[data-cat-on]").forEach(b => b.onclick = () => act({ action: "cat_dash", cid: b.dataset.catOn, on: true }, `« ${cname(b.dataset.catOn)} » ajouté dans le tableau Énergie`));
    $$("[data-cat-off]").forEach(b => b.onclick = () => act({ action: "cat_dash", cid: b.dataset.catOff, on: false }, `« ${cname(b.dataset.catOff)} » retiré du tableau Énergie`));
    $$("[data-cat-of]").forEach(b => b.onchange = () => act({ action: "cat_set", gid: b.dataset.catOf, cid: b.value || null }, b.value ? `« ${gname(G(b.dataset.catOf))} » rangé dans « ${cname(b.value)} »` : `« ${gname(G(b.dataset.catOf))} » sans catégorie`));
    $$("[data-cat-ren]").forEach(b => b.onclick = () => nameModal("Renommer la catégorie", cname(b.dataset.catRen), n => act({ action: "cat_rename", cid: b.dataset.catRen, name: n }, "Catégorie renommée")));
    $$("[data-cat-del]").forEach(b => b.onclick = () => act({ action: "cat_delete", cid: b.dataset.catDel }, `« ${cname(b.dataset.catDel)} » supprimée (ses appareils restent nommés)`));
    $$("[data-eundo]").forEach(b => b.onclick = () => act({ action: "energy_undo", lid: b.dataset.eundo }, "Retiré du tableau Énergie"));
    $$("[data-eredo]").forEach(b => b.onclick = () => act({ action: "energy_redo", lid: b.dataset.eredo }, "Remis dans le tableau Énergie"));
    if ($("#b-cat-new")) $("#b-cat-new").onclick = () => nameModal("Nouvelle catégorie", "", n => act({ action: "cat_create", name: n }, `Catégorie « ${n} » créée`));
  }
  else if (page === "rapport") { v.innerHTML = pageRapport(); mountRapport(); }
  else { v.innerHTML = pageApercu(); mountApercu(); }
  $$("[data-go]", v).forEach(el => el.addEventListener("click", e => { if (e.target.closest("button,a,input")) return; go(el.dataset.go); }));
}
function redrawCharts() {
  if ($("#daychart")) drawDay($("#daychart"));
  if ($("#scatter")) drawScatter($("#scatter"));
  if ($("#ov")) drawOverlay($("#ov"), route().arg === "aucun" ? null : route().arg, false);
  mountOverlays();
}

// ---------- Thème (« auto » suit celui de Home Assistant)
const THEMES = ["auto", "light", "dark"];
let theme = "auto";
function applyTheme() {
  const dark = theme === "auto" ? !!(hass && hass.themes && hass.themes.darkMode) : theme === "dark";
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
  const ic = theme === "auto" ? "auto" : theme === "light" ? "sun" : "moon", t = { auto: "Thème : comme Home Assistant", light: "Thème : clair", dark: "Thème : sombre" }[theme];
  ["#theme", "#theme2"].forEach(s => { $(s).innerHTML = icon(ic); $(s).title = t; });
}

// ---------- Accueil : montré une fois, au premier lancement (et à tout moment avec le bouton ?)
function introModal() {
  try { localStorage.setItem("empreinte-intro", "1"); } catch (e) { }
  const step = (n, t, d) => `<div style="display:flex;gap:12px;margin-top:14px"><div class="gicon" style="background:var(--good);flex:none;color:#fff;font-weight:700">${n}</div><div><b>${t}</b><p class="sub" style="font-size:13px">${d}</p></div></div>`;
  openModal(`<h2>Bienvenue dans Empreinte</h2>
    <p class="sub">Empreinte reconnaît les appareils de la maison à partir de la seule courbe de consommation. Il ne connaît pas encore les tiens : il apprend avec toi, en quelques jours.</p>
    ${step(1, "Il repère", "Chaque appareil de plus de 350 W qui s'allume est noté : sa puissance, sa durée, sa forme. Les allumages qui se ressemblent forment des « découvertes ».")}
    ${step(2, "Tu nommes", "Quand tu allumes un appareil, clique sur <b>« En ce moment, c'est… »</b> dans l'Aperçu. Ou, après coup, sur une utilisation → <b>C'est…</b>. Ton appareil n'est pas dans la liste ? Écris son nom : il est créé.")}
    ${step(3, "Il reconnaît", "Après 2 ou 3 réponses par appareil, il le reconnaît seul et affiche sa certitude. Une erreur ? « Non, c'est… », Renommer, Supprimer ou Annuler : tout se corrige.")}
    ${step(4, "Tu valides", "Quand il est assez sûr, <b>Valider</b> crée ses capteurs et l'ajoute au tableau Énergie. Rien n'y est ajouté sans ton clic.")}
    <p class="sub" style="font-size:12.5px;margin-top:16px">Tout reste dans ton Home Assistant. Pour revoir ce message : le bouton ${icon("help")}.</p>
    <div class="acts"><button class="btn primary" data-close>C'est parti</button></div>`);
}

// ---------- Démarrage
export function start(h) {
  hass = h;
  tip = $("#tip");
  try { theme = localStorage.getItem("empreinte-theme") || "auto"; } catch (e) { }
  ["#theme", "#theme2"].forEach(s => $(s).onclick = () => { theme = THEMES[(THEMES.indexOf(theme) + 1) % 3]; try { localStorage.setItem("empreinte-theme", theme); } catch (e) { } applyTheme(); });
  ["#logo1", "#logo2"].forEach(s => $(s).innerHTML = icon("logo"));
  ["#help1", "#help2"].forEach(s => { $(s).innerHTML = icon("help"); $(s).onclick = introModal; });
  $("#hamenu").innerHTML = icon("menu");
  $("#hamenu").onclick = () => { try { window.frameElement.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true })); } catch (e) { } };
  applyTheme();
  document.addEventListener("click", e => {
    const a = e.target.closest('a[href^="#/"]');
    if (!a || e.ctrlKey || e.metaKey) return;
    e.preventDefault();
    go(a.getAttribute("href"));
  });
  let rzT, lastW = innerWidth; window.addEventListener("resize", () => { if (innerWidth === lastW) return; lastW = innerWidth; clearTimeout(rzT); rzT = setTimeout(redrawCharts, 120); });
  refresh().then(render).then(() => {
    let seen = false; try { seen = localStorage.getItem("empreinte-intro") === "1"; } catch (e) { seen = true; }
    if (!seen && !S.error && !S.groups.some(g => g.name)) introModal();
  }).catch(err => { S.error = err.message || err.code || String(err); render(); });
  // Mise à jour discrète toutes les minutes (sauf si l'utilisateur est en train de sélectionner)
  setInterval(async () => {
    if (document.hidden || S.sel.size || $(".modal-back") || menuEl) return;
    try { await refresh(); const y = window.scrollY; await render(); window.scrollTo(0, y); } catch (e) { }
  }, 60e3);
  return {
    setHass(nh) {
      const wasDark = hass && hass.themes && hass.themes.darkMode;
      hass = nh;
      if ((nh.themes && nh.themes.darkMode) !== wasDark) applyTheme();
    },
  };
}
