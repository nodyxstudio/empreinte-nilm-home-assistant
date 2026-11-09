"""Teste la partie « Home Assistant » (analyse, suivi d'un appareil, étiquettes) sans Home Assistant.

Lancer :  python tests/test_coordinator.py
Les modules de HA sont remplacés par de petites imitations ; la base SQLite est un fichier temporaire.
"""
import datetime
import os
import sys
import tempfile
import types

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)


from ha_stub import C, E, Store, FakeEntry, make, stub, TZ  # noqa: E402
import test_tracking as TT  # noqa: E402
import test_scenarios as S  # noqa: E402


ok_all = True


def check(label, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(f"{'OK    ' if cond else 'ÉCHEC '}{label} {detail}")


# Soirée : four (2 200 W, thermostat) à 60 min, bouilloire pendant qu'il chauffe, on suit le four.
ts, ps, truth = TT.build([("four", "four", 3600), ("b", "bouilloire", 3700)], 3)
co = make()
real_time = C.time.time
cut = S.T0 + 3600 + 300          # 5 min après le démarrage : l'utilisateur dit « c'est le four »
C.time.time = lambda: cut
co.store.add_samples([(t, p, 0) for t, p in zip(ts, ps) if t <= cut])
co.processed_until = ts[0]
co._process()
part = next((x for x in co.running_parts if abs(x["p"] - 2200) < 400), None)
check("Le four apparaît dans « En cours »", part, f"({[(round(x['p'])) for x in co.running_parts]})")
co._action({"action": "label_now", "name": "Four", "at": part["start"], "p": part["p"]})
check("« C'est… Four » démarre un suivi", len(co.tracks) == 1)
# pendant le suivi : la bouilloire (2 kW) ne doit jamais s'afficher « Four · suivi en cours »
co._first = ts[0]
co.hass = types.SimpleNamespace(states=types.SimpleNamespace(get=lambda e: None))
mislabeled = False
for minute in range(6, 12):
    now = S.T0 + 3600 + minute * 60
    C.time.time = lambda now=now: now
    co.store.add_samples([(t, p, 0) for t, p in zip(ts, ps) if now - 60 < t <= now])
    co._process()
    for e in co.state_payload()["events"]:
        if e["tracked_by"] and abs(e["p"] - 2000) < 150 and e["dur"] <= 400:
            mislabeled = True
check("Pendant le suivi, la bouilloire n'est pas affichée comme « Four »", not mislabeled)
# la soirée continue, minute par minute
end_true = S.T0 + truth["four"][-1][1]
for minute in range(12, 60):
    now = S.T0 + 3600 + minute * 60
    C.time.time = lambda now=now: now
    co.store.add_samples([(t, p, 0) for t, p in zip(ts, ps) if now - 60 < t <= now])
    co._process()
check("Le suivi s'est terminé tout seul", not co.tracks)
four = [e for e in co.evs if co.groups.get(e.gid, {}).get("name") == "Four"]
check("Une seule utilisation « Four »", len(four) == 1, f"({len(four)})")
if four:
    r = co.rows[four[0].id]
    on_true = sum(b - a for a, b in truth["four"])
    on_found = sum(1 for v in r["prof"] if v) * E.STEP
    check("Fin du four au bon moment", abs(r["end"] - end_true) <= 180, f"(écart {r['end'] - end_true:+.0f} s)")
    check("Temps allumé du four juste", abs(on_found - on_true) / on_true <= 0.15, f"({on_found / 60:.1f} min, vrai {on_true / 60:.1f})")
others = [e for e in co.evs if e not in four]
check("La bouilloire reste à part (pas dans le four)", any(abs(e.f["P"] - 2000) < 300 and e.f["D"] <= 400 for e in others),
      f"({[(e.f['P'], e.f['D']) for e in others]})")
# Plus tard, un autre four : personne ne dit rien, il doit être reconnu et suivi tout seul.
ts2, ps2, truth2 = TT.build([("four", "four", 9000)], 5)
started = False
for minute in range(1, 100):
    now = S.T0 + 8400 + minute * 60
    C.time.time = lambda now=now: now
    co.store.add_samples([(t, p, 0) for t, p in zip(ts2, ps2) if now - 60 < t <= now and t > S.T0 + 8400])
    co._process()
    started |= any(not t["user"] for t in co.tracks)
    if os.environ.get("DETAIL"):
        print("   min", minute + 140, "parts", [(round((x["start"] - S.T0) / 60, 1), x["p"], x["hint"], bool(x["end"])) for x in co.running_parts],
              "suivis", [(round((t["at"] - S.T0) / 60, 1), t["p"], t["on"]) for t in co.tracks])
check("Le four suivant est reconnu et suivi tout seul", started)
four2 = [e for e in co.evs if co.groups.get(e.gid, {}).get("name") == "Four" and co.rows[e.id]["start"] > S.T0 + 8400]
check("… et enregistré comme une seule utilisation « Four »", len(four2) == 1, f"({len(four2)})")
if four2:
    end2 = S.T0 + truth2["four"][-1][1]
    check("… qui s'arrête au bon moment", abs(co.rows[four2[0].id]["end"] - end2) <= 180,
          f"(écart {co.rows[four2[0].id]['end'] - end2:+.0f} s)")
    check("… sans être verrouillé (tu peux encore corriger)", not four2[0].pinned)

# Lave-vaisselle de 3 h 30 avec une pause de 2 h entre ses chauffes, un thé et un café au milieu.
def dishwasher(minutes):
    ts3, ps3, truth3 = TT.build([("lv", "lv_long", 3600), ("the", "bouilloire", 5400), ("cafe", "cafe", 7200),
                                 ("the2", "bouilloire", 12000)], 7)
    co3 = make()
    cut = S.T0 + 3600 + 240
    C.time.time = lambda: cut
    co3.store.add_samples([(t, p, 0) for t, p in zip(ts3, ps3) if t <= cut])
    co3.processed_until = ts3[0]
    co3._process()
    part = next(x for x in co3.running_parts if abs(x["p"] - 2000) < 400)
    co3._action({"action": "label_now", "name": "Lave-vaisselle", "at": part["start"], "p": part["p"]})
    if minutes:
        # « il reste N minutes » à partir du moment où on le dit (ici 4 min après le début)
        co3._action({"action": "track_duration", "tid": co3.tracks[0]["id"], "minutes": minutes - 4})
    for minute in range(5, 300):
        now = S.T0 + 3600 + minute * 60
        C.time.time = lambda now=now: now
        co3.store.add_samples([(t, p, 0) for t, p in zip(ts3, ps3) if now - 60 < t <= now])
        co3._process()
    lv = [e for e in co3.evs if co3.groups.get(e.gid, {}).get("name") == "Lave-vaisselle"]
    small = [e for e in co3.evs if e not in lv and e.f["D"] <= 400]
    return co3, lv, small, S.T0 + truth3["lv"][-1][1]


co3, lv, small, end3 = dishwasher(210)
check("Lave-vaisselle 3 h 30 avec « Durée » : une seule utilisation", len(lv) == 1, f"({len(lv)})")
if lv:
    check("… qui finit à la vraie fin de la 2e chauffe", abs(co3.rows[lv[0].id]["end"] - end3) <= 180,
          f"(écart {co3.rows[lv[0].id]['end'] - end3:+.0f} s)")
check("… le thé, le café et le 2e thé restent à part", len(small) >= 3, f"({[(e.f['P'], e.f['D']) for e in small]})")
check("… la durée est proposée la prochaine fois (pas imposée)", co3._used_minutes("Lave-vaisselle")[:1] == [210])
hist = co3.store.get_meta("program_history", {}).get("lave-vaisselle", [])
check("… et la signature du programme est enregistrée", hist and hist[-1]["chauffes"] >= 2, f"({hist[-1] if hist else None})")
co4, lv4, _, _ = dishwasher(None)
print(f"      (sans « Durée », pour comparer : {len(lv4)} utilisation(s) lave-vaisselle — la pause de 2 h dépasse 75 min)")

# « C'est fini » pendant que le lave-vaisselle tourne encore : le chauffage ne doit plus être compté.
ts5, ps5, _ = TT.build([("lv", "lave_vaisselle", 3000), ("ch", "chauffage_ceramique", 3700)], 9)
co5 = make()
t5 = S.T0 + 3700 + 180
C.time.time = lambda: t5
co5.store.add_samples([(t, p, 0) for t, p in zip(ts5, ps5) if t <= t5])
co5.processed_until = ts5[0]
co5._process()
part = next(x for x in co5.running_parts if abs(x["p"] - 850) < 200)
co5._action({"action": "label_now", "name": "Chauffage salle de bain", "at": part["start"], "p": part["p"]})
restarted = False
for minute in range(4, 40):
    now = S.T0 + 3700 + minute * 60
    C.time.time = lambda now=now: now
    co5.store.add_samples([(t, p, 0) for t, p in zip(ts5, ps5) if now - 60 < t <= now])
    if minute == 8:
        co5._action({"action": "track_stop", "tid": co5.tracks[0]["id"]})
    co5._process()
    restarted |= minute > 8 and bool(co5.tracks)
check("« C'est fini » : le chauffage n'est plus suivi ni compté ensuite", not restarted)
ch = [e for e in co5.evs if co5.groups.get(e.gid, {}).get("name") == "Chauffage salle de bain"]
check("… une seule utilisation « Chauffage », arrêtée quand tu l'as dit", len(ch) == 1 and co5.rows[ch[0].id]["end"] <= S.T0 + 3700 + 8 * 60 + 60,
      f"({[(round((co5.rows[e.id]['start'] - S.T0) / 60), round((co5.rows[e.id]['end'] - S.T0) / 60)) for e in ch]})")

crumbs = [e for e in co5.evs if e not in ch and e.f["D"] <= 75 and abs(co5.rows[e.id]["start"] - (S.T0 + 3700)) <= 90]
check("… et son pic de démarrage ne reste pas à part comme une petite utilisation", not crumbs, f"({[(e.f['P'], e.f['D']) for e in crumbs]})")

# Plaque à ~1 kW suivie, puis le micro-ondes (1,32 kW, puissance voisine) lancé 1 min après : il reste à part.
ts8, ps8, _ = TT.build([("pl", "plaque_fixe", 3600), ("mo", "micro", 3660)], 11)
co8 = make()
t8 = S.T0 + 3600 + 30
C.time.time = lambda: t8
co8.store.add_samples([(t, p, 0) for t, p in zip(ts8, ps8) if t <= t8])
co8.processed_until = ts8[0]
co8._process()
part = next(x for x in co8.running_parts if abs(x["p"] - 1000) < 200)
co8._action({"action": "label_now", "name": "Plaques de cuisson", "at": part["start"], "p": part["p"]})
co8.hass = types.SimpleNamespace(states=types.SimpleNamespace(get=lambda e: None))
seen_micro = False
for minute in range(1, 50):
    now = S.T0 + 3600 + minute * 60
    C.time.time = lambda now=now: now
    co8.store.add_samples([(t, p, 0) for t, p in zip(ts8, ps8) if now - 60 < t <= now])
    co8._process()
    if minute == 3:
        seen_micro = any(abs(x["p"] - 1320) < 200 for x in co8.state_payload()["status"]["running_parts"])
        mo_part = next(x for x in co8.running_parts if abs(x["p"] - 1320) < 200)
        # pile 60 s après le démarrage de la plaque, puissance à 25 % près
        co8._action({"action": "label_now", "name": "Micro-ondes", "at": part["start"] + 60, "p": mo_part["p"]})
check("Micro-ondes lancé pendant la plaque suivie : il s'affiche à part pendant qu'elle tourne", seen_micro)
mo = [e for e in co8.evs if abs(e.f["P"] - 1320) < 200 and e.f["D"] <= 180]
labeled = len(mo) == 1 and co8.groups.get(mo[0].gid, {}).get("name") == "Micro-ondes"
pl = [e for e in co8.evs if co8.groups.get(e.gid, {}).get("name") == "Plaques de cuisson"]
check("… il reste une utilisation à part, pas avalée par la plaque", len(mo) == 1, f"({[(e.f['P'], e.f['D']) for e in co8.evs]})")
check("… et « C'est Micro-ondes » dit pendant la plaque n'est pas perdu, il est rangé à la fin", labeled)
check("… et la plaque garde sa puissance (~1 kW, pas 1,3)", len(pl) == 1 and abs(pl[0].f["P"] - 1000) < 150, f"({[(e.f['P'], e.f['D']) for e in pl]})")

# Le chauffage à céramique démarre par un pic de 1,6 kW (la puissance du lave-vaisselle) : jamais suivi comme lave-vaisselle.
co10 = make()
for d in range(3):                       # un lave-vaisselle déjà nommé : ~1,65 kW, long
    pr = [1650] * 400
    eid = co10.store.add_event(S.T0 - (d + 1) * 86400, S.T0 - (d + 1) * 86400 + 6000, 300, pr, E.features(pr, 20))
    co10.rows[eid] = {"start": S.T0 - (d + 1) * 86400, "end": S.T0 - (d + 1) * 86400 + 6000, "base": 300, "prof": pr}
    co10.evs.append(E.Ev(eid, E.features(pr, 20)))
co10._label({e.id for e in co10.evs}, "Lave-vaisselle")
co10._cluster()
ts10, ps10, _ = TT.build([("ch", "chauffage_ceramique", 3600)], 5)
ts10, ps10 = list(ts10), list(ps10)
for k in range(3):                       # un pic de démarrage tel qu'un compteur l'envoie : 1,6 kW, puis ~850 W
    ts10.append(S.T0 + 3601 + 7 * k); ps10.append(330 + 1600)
ts10, ps10 = zip(*sorted(zip(ts10, ps10)))
co10.store.add_samples([(t, p, 0) for t, p in zip(ts10, ps10) if t <= S.T0 + 3500])
co10.processed_until = S.T0 + 3000
wrong = False
for k in range(1, 80):
    now = S.T0 + 3500 + k * 15
    C.time.time = lambda now=now: now
    co10.store.add_samples([(t, p, 0) for t, p in zip(ts10, ps10) if now - 15 < t <= now])
    co10._process()
    wrong |= any(t["name"] == "Lave-vaisselle" and now - t["at"] > 120 for t in co10.tracks)
check("Pic de démarrage du chauffage à 1,6 kW : pas suivi comme lave-vaisselle", not wrong,
      f"({[(t['name'], t['p']) for t in co10.tracks]})")
check("… et aucune utilisation « Lave-vaisselle » ajoutée", sum(1 for e in co10.evs if co10.groups.get(e.gid, {}).get("name") == "Lave-vaisselle") == 3)

# Coupure de courant de 30 min pendant le suivi du lave-vaisselle : il s'arrête à la coupure, on repart de zéro.
ts6, ps6, _ = TT.build([("lv", "lave_vaisselle", 3000)], 4)     # 1re chauffe de 60 à 75 min
gap = (S.T0 + 3600 + 600, S.T0 + 3600 + 2400)
ts6, ps6 = zip(*[(t, p) for t, p in zip(ts6, ps6) if not gap[0] <= t < gap[1]])
co6 = make()
t6 = S.T0 + 3600 + 240
C.time.time = lambda: t6
co6.store.add_samples([(t, p, 0) for t, p in zip(ts6, ps6) if t <= t6])
co6.processed_until = ts6[0]
co6._process()
part = next(x for x in co6.running_parts if abs(x["p"] - 2000) < 400)
co6._action({"action": "label_now", "name": "Lave-vaisselle", "at": part["start"], "p": part["p"]})
for minute in range(5, 200):
    now = S.T0 + 3600 + minute * 60
    if gap[0] <= now < gap[1]:
        continue                  # HA est éteint pendant la coupure
    C.time.time = lambda now=now: now
    co6.store.add_samples([(t, p, 0) for t, p in zip(ts6, ps6) if now - 60 < t <= now])
    co6._process()
lv6 = [e for e in co6.evs if co6.groups.get(e.gid, {}).get("name") == "Lave-vaisselle" and co6.rows[e.id]["start"] < gap[0]]
check("Coupure pendant le lave-vaisselle : le suivi s'arrête à la coupure", len(lv6) == 1
      and co6.rows[lv6[0].id]["end"] <= gap[0] + 60 and lv6[0].f.get("cut"),
      f"({[(round((co6.rows[e.id]['start'] - S.T0) / 60), round((co6.rows[e.id]['end'] - S.T0) / 60)) for e in lv6]})")
check("… et ce programme coupé ne sert pas à proposer des durées",
      not any(h["start"] < gap[0] for h in co6.store.get_meta("program_history", {}).get("lave-vaisselle", [])))
after = [e for e in co6.evs if gap[1] - 60 <= co6.rows[e.id]["start"] <= gap[1] + 300]
check("… rien n'est pris pour un appareil au retour du courant", not after, f"({[(e.f['P'], e.f['D']) for e in after]})")

# Catégories : Électroménager reçoit d'office four, plaques, lave-vaisselle, lave-linge, sèche-linge (pas le café)
names = {g["name"]: gid for gid, g in co3.groups.items() if g.get("name")}
check("Le lave-vaisselle est dans l'électroménager", co3.cat_of(names["Lave-vaisselle"]) == "menager")
co3._label({co3.evs[0].id}, "Cafetière")
caf = next(gid for gid, g in co3.groups.items() if g.get("name") == "Cafetière")
check("… la cafetière non", co3.cat_of(caf) is None)
co3._action({"action": "cat_dash", "cid": "menager", "on": True})
men = co3._cat("menager")
co3._cats_update()
check("… ajouté dans le tableau Énergie : compté à partir de ce moment-là", men["total"] == 0)
lv = next(e for e in co3.evs if e.gid == names["Lave-vaisselle"])
co3.rows[lv.id]["end"] = men["since"] + 60
co3._cats_update()
check("… une utilisation qui finit ensuite est ajoutée", abs(men["total"] - lv.f["wh"] / 1000) < 1e-3, f"({men['total']} kWh)")
co3._action({"action": "cat_create", "name": "Cuisine"})
cui = co3.cats[-1]["id"]
co3._action({"action": "cat_set", "gid": names["Lave-vaisselle"], "cid": cui})
co3._action({"action": "cat_set", "gid": caf, "cid": cui})
co3._cats_update()
check("Une catégorie créée par toi reçoit les appareils que tu y mets", co3.cat_of(names["Lave-vaisselle"]) == cui and co3.cat_of(caf) == cui)
check("… le lave-vaisselle n'est plus dans l'électroménager (jamais compté deux fois)", co3.cat_of(names["Lave-vaisselle"]) != "menager")
check("… et le compteur de l'électroménager ne baisse pas", men["total"] > 0)
co3._action({"action": "cat_delete", "cid": cui})
check("Supprimer une catégorie laisse ses appareils sans catégorie (et nommés)", co3.cat_of(caf) is None and co3.groups[caf]["name"] == "Cafetière")

# « Envoyé au tableau Énergie » : chaque ajout au tableau Énergie est noté, et peut être retiré des statistiques de HA.
adjusted = []
stub("homeassistant.components")
stub("homeassistant.components.recorder", get_instance=lambda h: types.SimpleNamespace(
    async_adjust_statistics=lambda sid, t, kwh, unit: adjusted.append((sid, t, kwh, unit))))
co7 = co3
gch = names["Lave-vaisselle"]
co7.groups[gch]["status"] = "valide"
co7.entities[gch] = [types.SimpleNamespace(entity_id="sensor.empreinte_lave_vaisselle_energie")]
co7.hass = types.SimpleNamespace(loop=types.SimpleNamespace(call_soon_threadsafe=lambda f, *a: f(*a)))
lv = next(e for e in co7.evs if e.gid == gch)
co7._add_energy(gch, lv.id, 1.25)
x = co7.energy_log[-1]
check("Un ajout au tableau Énergie est noté (appareil, kWh, utilisation)", x["name"] == "Lave-vaisselle" and x["kwh"] == 1.25 and x["start"])
try:
    co7._action({"action": "energy_undo", "lid": x["id"]})
    too_soon = False
except ValueError:
    too_soon = True
check("… pas retirable avant que HA l'ait enregistré (10 min)", too_soon and not adjusted)
x["t"] -= 3600
co7._action({"action": "energy_undo", "lid": x["id"]})
check("… « Retirer » enlève ses kWh des statistiques de HA, à partir du moment où il avait été ajouté",
      adjusted and adjusted[-1][0] == "sensor.empreinte_lave_vaisselle_energie" and adjusted[-1][2] == -1.25
      and adjusted[-1][1].timestamp() <= x["t"] and x["removed"], f"({adjusted})")
co7._action({"action": "energy_undo", "lid": x["id"]})
check("… le retirer deux fois ne fait rien de plus", len(adjusted) == 1)
co7._action({"action": "energy_redo", "lid": x["id"]})
check("… et « Remettre » le remet", adjusted[-1][2] == 1.25 and not x["removed"])
co7.store.set_meta("energy_log", co7.energy_log)
check("… la liste est gardée (redémarrage de HA)", co7.store.get_meta("energy_log")[-1]["id"] == x["id"])

# Four validé et ajouté dans le tableau Énergie, puis l'électroménager aussi : le four n'y est plus compté tout seul.
co7.groups[gch]["energy_dash"] = True
co7._action({"action": "cat_set", "gid": gch, "cid": "menager"})
check("Appareil et sa catégorie dans le tableau Énergie : l'appareil n'y est pas une deuxième fois",
      co7._in_dash(("cat", "menager")) and not co7._in_dash(("g", gch)))
co7._action({"action": "cat_set", "gid": gch, "cid": None})
check("… et il y revient tout seul s'il quitte la catégorie", co7._in_dash(("g", gch)))

# Filet de sécurité : le four de samedi 20 h 17 était dans deux utilisations « Four » à la fois (compté deux fois).
co9 = make()
T = S.T0
big = co9.store.add_event(T, T + 795, 300, [2156] * 20 + [0] * 29 + [2156] * 4, E.features([2156] * 20 + [0] * 29 + [2156] * 4, 20))
small = co9.store.add_event(T + 735, T + 795, 300, [2250] * 4, E.features([2250] * 4, 20))
co9.evs, co9.rows = [], {}
for r in co9.store.events():
    co9.rows[r["id"]] = {k: r[k] for k in ("start", "end", "base", "prof")}
    co9.evs.append(E.Ev(r["id"], r["f"], "g1", r["id"] == small, set()))
co9.groups["g1"] = {"id": "g1", "name": "Four", "status": "nomme"}
co9._dedupe_overlaps(list(co9.evs))
rb = co9.rows.get(big)
check("Un même appareil dans deux utilisations qui se chevauchent : compté une seule fois",
      rb and rb["end"] <= T + 735 + E.STEP and len(co9.rows) == 2, f"({rb and (rb['start'] - T, rb['end'] - T)})")
check("… la partie commune reste dans celle que tu as confirmée", co9.rows[small]["prof"] == [2250] * 4)

# Fiabilité mesurée : « Oui, c'est ça » sur un rangement automatique = juste ; le déplacer ailleurs = faux.
co11 = make()
for k in range(3):
    pr = [1300] * 8
    eid = co11.store.add_event(S.T0 + k * 3600, S.T0 + k * 3600 + 120, 300, pr, E.features(pr, 7))
    co11.rows[eid] = {"start": S.T0 + k * 3600, "end": S.T0 + k * 3600 + 120, "base": 300, "prof": pr}
    co11.evs.append(E.Ev(eid, E.features(pr, 7)))
co11._label({co11.evs[0].id}, "Cafetière")
gcaf = co11.evs[0].gid
for e in co11.evs[1:]:
    e.gid = gcaf                                  # rangées toutes seules
co11._action({"action": "confirm", "ids": [f"e{co11.evs[1].id}"]})
co11._action({"action": "label", "ids": [f"e{co11.evs[2].id}"], "name": "Micro-ondes"})
v = co11._verdict_summary()
check("Fiabilité mesurée : 1 juste (confirmé), 1 faux (c'était le micro-ondes)", v["n"] == 2 and v["ok"] == 1
      and v["errors"][0]["auto"] == "Cafetière" and v["errors"][0]["user"] == "Micro-ondes", f"({v})")

# Pas reconnu : on propose les 2 appareils nommés les plus proches (un clic pour trancher), sans rien ranger.
pr = [1310] * 8
eid = co11.store.add_event(S.T0 + 9 * 3600, S.T0 + 9 * 3600 + 120, 300, pr, E.features(pr, 7))
co11.rows[eid] = {"start": S.T0 + 9 * 3600, "end": S.T0 + 9 * 3600 + 120, "base": 300, "prof": pr}
ev12 = E.Ev(eid, E.features(pr, 7))
co11.evs.append(ev12)
co11._recompute()
g12 = co11._guess_names(ev12)
check("Une utilisation pas reconnue propose les appareils nommés les plus proches", "Cafetière" in g12 and ev12.gid is None, f"({g12})")

# Une utilisation déjà comptée dans une catégorie du tableau Énergie, puis corrigée : retirée toute seule (après 10 min).
adjusted.clear()
co13 = make()
co13.hass = types.SimpleNamespace(loop=types.SimpleNamespace(call_soon_threadsafe=lambda f, *a: f(*a)))
pr = [2000] * 40
eid = co13.store.add_event(S.T0, S.T0 + 600, 300, pr, E.features(pr, 12))
co13.rows[eid] = {"start": S.T0, "end": S.T0 + 600, "base": 300, "prof": pr}
co13.evs.append(E.Ev(eid, E.features(pr, 12)))
C.time.time = lambda: S.T0 - 60
co13._action({"action": "cat_dash", "cid": "menager", "on": True})
co13.entities["cat:menager"] = [types.SimpleNamespace(entity_id="sensor.empreinte_categorie_electromenager_energie")]
co13._label({eid}, "Four")
C.time.time = lambda: S.T0 + 700
co13._cats_update()
logged = [x for x in co13.energy_log if x["eid"] == eid]
check("Utilisation du four comptée dans l'électroménager (tableau Énergie)", len(logged) == 1 and not adjusted)
co13._action({"action": "label", "ids": [f"e{eid}"], "name": "Chauffage salle de bain"})
co13._cats_update()
check("… corrigée tout de suite : pas encore retirée (HA n'a pas fini de l'enregistrer)", not adjusted)
C.time.time = lambda: S.T0 + 700 + 700
co13._cats_update()
check("… 10 min après : retirée toute seule du tableau Énergie", adjusted and adjusted[-1][2] < 0 and logged[0]["removed"] and logged[0].get("auto"),
      f"({adjusted})")
co13._cats_update()
check("… une seule fois", len(adjusted) == 1)

# « Valider » un appareil l'ajoute aussi dans le tableau Énergie (sauf s'il y est déjà avec sa catégorie : voir _in_dash).
gv = next(gid for gid, g in co11.groups.items() if g.get("name") == "Cafetière")
co11._action({"action": "validate", "gid": gv})
check("Valider un appareil l'ajoute dans le tableau Énergie", co11.groups[gv]["status"] == "valide" and co11.groups[gv].get("energy_dash"))

# Deux utilisations « Four » qui se chevauchent : on regarde le compteur avant de corriger.
def overlap_case(extra):
    co = make()
    pr1, pr2 = [2400] * 88, [1290] * 8
    t = S.T0
    co.store.add_samples([(t + k * 20, 300 + 2400 * (k * 20 < 1320) + (extra if 420 <= k * 20 < 540 else 0), 0) for k in range(80)])
    e1 = co.store.add_event(t, t + 1320, 300, pr1, E.features(pr1, 19))
    e2 = co.store.add_event(t + 420, t + 540, 300, pr2, E.features(pr2, 19))
    for eid, pr, a0 in ((e1, pr1, t), (e2, pr2, t + 420)):
        co.rows[eid] = {"start": a0, "end": a0 + len(pr) * E.STEP, "base": 300, "prof": pr}
        co.evs.append(E.Ev(eid, E.features(pr, 19)))
    co._label({e1}, "Four")
    co._action({"action": "label", "ids": [f"e{e2}"], "name": "Four"})
    k = round(450 / E.STEP)
    return co.rows[e1]["prof"][k] + co.rows[e2]["prof"][0]


dup = overlap_case(0)          # le compteur ne montre que le four : le morceau de 1,3 kW est déjà dedans
real = overlap_case(1290)      # le compteur montre le four ET 1,3 kW de plus : deux appareils
check("Même consommation comptée deux fois (le compteur n'en montre qu'une) : corrigé", abs(dup - 2400) < 50, f"({dup} W)")
check("Deux appareils réellement allumés ensemble (le compteur les montre) : rien n'est effacé", abs(real - 3690) < 50, f"({real} W)")

# « À telle heure, c'était… » : nommer une utilisation d'hier en donnant son heure.
co15 = make()
pr = [870] * 52
eid = co15.store.add_event(S.T0, S.T0 + 780, 300, pr, E.features(pr, 19))
co15.rows[eid] = {"start": S.T0, "end": S.T0 + 780, "base": 300, "prof": pr}
co15.evs.append(E.Ev(eid, E.features(pr, 19)))
co15._action({"action": "label_at", "t": S.T0 + 300, "name": "Micro-ondes"})
check("Nommer à une heure : l'utilisation qui tournait à ce moment-là est rangée", co15.groups.get(co15.evs[0].gid, {}).get("name") == "Micro-ondes")
try:
    co15._action({"action": "label_at", "t": S.T0 + 5 * 3600, "name": "Four"})
    told = False
except KeyError:
    told = True
check("… et s'il n'y a rien à cette heure, on le dit (rien n'est rangé au hasard)", told)

# Les messages de HA ont déjà un champ « id » (numéro du message) : nos actions ne doivent jamais l'utiliser.
init_src = open(os.path.join(HERE, "..", "custom_components", "empreinte", "__init__.py"), encoding="utf-8").read()
check("Aucune action n'utilise le champ réservé « id »", 'vol.Optional("id")' not in init_src and 'vol.Required("id")' not in init_src)

if os.environ.get("DETAIL") or not ok_all:
    for e in co.evs:
        r = co.rows[e.id]
        print("   ", round((r["start"] - S.T0) / 60, 1), "→", round((r["end"] - S.T0) / 60, 1), "min", e.f["P"], "W",
              co.groups.get(e.gid, {}).get("name"), "".join("#" if v >= 1500 else "+" if v >= 200 else "." for v in r["prof"]))
    print("    vrai bouilloire", [(round(a / 60, 1), round(b / 60, 1)) for a, b in truth["b"]])
C.time.time = real_time
print("\nTout est bon" if ok_all else "\nIl y a des échecs")
sys.exit(0 if ok_all else 1)
