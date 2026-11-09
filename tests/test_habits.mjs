// Habitudes : vérifie qu'une habitude ne mélange jamais deux appareils nommés différemment.
// Lancer :  node tests/test_habits.mjs
import { findHabits } from "../custom_components/empreinte/frontend/habits.js";

const DAY = 864e5, T0 = new Date(2026, 9, 20).getTime();
const at = (d, h, m) => T0 + d * DAY + (h * 60 + m) * 60e3;
const dayOf = ms => Math.floor((ms - T0) / DAY);
let n = 0, ok = 0;
const check = (label, cond, detail = "") => { n++; if (cond) ok++; console.log(`${cond ? "OK    " : "ÉCHEC "}${label} ${detail}`); };
const names = { caf: "Cafetière", mo: "Micro-ondes" };
const named = gid => !!names[gid];

// Vers 13 h, trois jours de suite : un appareil inconnu, la cafetière, puis le micro-ondes (1,3 kW chacun).
let evs = [
  { id: "e1", start: at(0, 13, 4), p: 1340, dur: 140, gid: "j" },
  { id: "e2", start: at(1, 13, 12), p: 1290, dur: 110, gid: "caf" },
  { id: "e3", start: at(2, 12, 38), p: 1310, dur: 70, gid: "mo" },
];
let hs = findHabits(evs, dayOf, 3, named);
check("Cafetière et micro-ondes à la même heure ne font pas une habitude", hs.length === 0, `(${hs.length})`);

// Le café tous les matins, dont un pas encore nommé : une habitude « Cafetière ».
evs = [0, 1, 2, 3].map(d => ({ id: `c${d}`, start: at(d, 6, 15 + 7 * d), p: 1280 + 20 * d, dur: 120, gid: d === 3 ? null : "caf" }));
hs = findHabits(evs, dayOf, 4, named);
check("Le café de chaque matin est une habitude", hs.length === 1 && hs[0].days === 4, `(${hs.map(h => h.days)})`);
check("… rangée dans la cafetière (il ne reste plus rien à identifier)", hs[0] && hs[0].gid === "caf");

// Deux appareils nommés, 3 jours chacun à la même heure : deux habitudes, pas une seule mélangée.
evs = [0, 1, 2].flatMap(d => [
  { id: `a${d}`, start: at(d, 12, 20), p: 1300, dur: 120, gid: "caf" },
  { id: `b${d}`, start: at(d, 12, 30), p: 1320, dur: 70, gid: "mo" },
]);
hs = findHabits(evs, dayOf, 3, named);
check("Deux appareils nommés à la même heure donnent deux habitudes distinctes",
  hs.length === 2 && hs.every(h => h.ids.every(id => evs.find(e => e.id === id).gid === h.gid)), `(${hs.map(h => h.gid)})`);

console.log(`\n${ok}/${n} vérifications réussies`);
process.exit(ok === n ? 0 : 1);
