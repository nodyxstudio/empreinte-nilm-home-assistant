// Recherche d'habitudes : un appareil de même puissance qui démarre à peu près à la même heure,
// plusieurs jours différents. Fonctions pures, testables sans Home Assistant.

const circ = (a, b) => { const d = Math.abs(a - b) % 24; return Math.min(d, 24 - d); };
const hourOf = ms => { const d = new Date(ms); return d.getHours() + d.getMinutes() / 60; };
const median = a => { const s = [...a].sort((x, y) => x - y); return s.length ? s[s.length >> 1] : 0; };

export const HABIT = {
  MIN_DAYS: 3,        // vu au moins 3 jours différents…
  MIN_SHARE: 0.4,     // … et au moins 40 % des jours observés
  WINDOW_H: 0.75,     // à ± 45 minutes de la même heure
  POWER_TOL: 0.15,    // puissance à ± 15 %
  DUR_TOL: 1.2,       // durée du même ordre (facteur ~3)
};

// events : [{id, start (ms), p (W), dur (s), gid}] ; dayOf(ms) -> numéro du jour ; nDays : jours observés ;
// named(gid) : ce groupe a-t-il un nom ? Deux appareils nommés différemment ne forment jamais une même habitude
// (micro-ondes et cafetière ont presque la même puissance) ; une utilisation pas encore nommée peut rejoindre l'un ou l'autre.
export function findHabits(events, dayOf, nDays, named = gid => !!gid) {
  const cand = events.filter(e => e.p >= 350 && dayOf(e.start) >= 0).map(e => ({ e, h: hourOf(e.start), d: dayOf(e.start) }));
  const used = new Set(), out = [];
  const minDays = Math.max(HABIT.MIN_DAYS, Math.ceil(HABIT.MIN_SHARE * nDays));
  for (;;) {
    let best = null;
    for (const a of cand) {
      if (used.has(a.e.id)) continue;
      const near = cand.filter(b => !used.has(b.e.id) && circ(a.h, b.h) <= HABIT.WINDOW_H
        && Math.abs(Math.log(b.e.p / a.e.p)) <= HABIT.POWER_TOL && Math.abs(Math.log(b.e.dur / a.e.dur)) <= HABIT.DUR_TOL);
      const nm = b => named(b.e.gid) ? b.e.gid : null;
      // un seul appareil nommé par habitude : celui de a s'il en a un, sinon on essaie chacun de ses voisins (ou aucun)
      const targets = nm(a) ? [nm(a)] : [null, ...new Set(near.map(nm).filter(Boolean))];
      for (const T of targets) {
        const perDay = new Map();
        for (const b of near) {
          if (nm(b) && nm(b) !== T) continue;
          const cur = perDay.get(b.d);
          if (!cur || circ(a.h, b.h) < circ(a.h, cur.h)) perDay.set(b.d, b);
        }
        if (perDay.size >= minDays && (!best || perDay.size > best.members.length)) best = { members: [...perDay.values()] };
      }
    }
    if (!best) break;
    best.members.forEach(m => used.add(m.e.id));
    out.push(summarize(best.members, nDays));
  }
  return out.sort((a, b) => a.hour - b.hour);
}

function summarize(ms, nDays) {
  const ang = ms.map(m => m.h / 24 * 2 * Math.PI);
  const hour = (Math.atan2(ang.reduce((s, a) => s + Math.sin(a), 0), ang.reduce((s, a) => s + Math.cos(a), 0)) / (2 * Math.PI) * 24 + 24) % 24;
  const offs = ms.map(m => { let d = m.h - hour; if (d > 12) d -= 24; if (d < -12) d += 24; return d; });
  const weekend = ms.filter(m => [0, 6].includes(new Date(m.e.start).getDay())).length;
  const gids = {}; ms.forEach(m => { if (m.e.gid) gids[m.e.gid] = (gids[m.e.gid] || 0) + 1; });
  const gid = Object.entries(gids).sort((a, b) => b[1] - a[1])[0];
  return {
    hour, from: (hour + Math.min(...offs) + 24) % 24, to: (hour + Math.max(...offs) + 24) % 24,
    p: median(ms.map(m => m.e.p)), dur: median(ms.map(m => m.e.dur)),
    days: ms.length, nDays, weekend, weekdays: ms.length - weekend,
    dayList: ms.map(m => m.d).sort((a, b) => a - b),
    ids: ms.map(m => m.e.id), gid: gid && gid[1] >= ms.length / 2 ? gid[0] : null,
  };
}

export function periodOf(h) {
  if (h < 5) return "la nuit";
  if (h < 10.5) return "le matin";
  if (h < 14.5) return "vers midi";
  if (h < 18) return "l'après-midi";
  if (h < 22) return "le soir";
  return "en fin de soirée";
}

// Pistes selon l'heure, la puissance et la durée. Ce sont des questions, jamais des certitudes.
export function habitHints(hb) {
  const h = hb.hour, kw = hb.p / 1000, min = hb.dur / 60;
  const morning = h >= 5 && h < 10.5, meal = (h >= 11 && h < 14.5) || (h >= 18.5 && h < 21.5), evening = h >= 18 && h < 24;
  const out = [];
  if (kw >= 1.7 && kw <= 2.6 && min <= 6) out.push("bouilloire");
  if (kw >= 1 && kw <= 1.7 && min <= 5 && morning) out.push("machine à café", "sèche-cheveux");
  if (kw >= 0.7 && kw <= 1.3 && min <= 5 && morning) out.push("grille-pain");
  if (kw >= 0.7 && kw <= 1.6 && min <= 10 && meal) out.push("micro-ondes");
  if (kw >= 1 && min >= 8 && min <= 90 && meal) out.push("plaques de cuisson", "four");
  if (kw >= 0.7 && kw <= 2.5 && min >= 5 && min <= 40 && (morning || evening)) out.push("chauffage de salle de bain");
  if (kw >= 1.7 && min >= 10 && !meal && !(morning && min < 40)) out.push("lave-linge", "lave-vaisselle");
  return [...new Set(out)].slice(0, 3);
}
