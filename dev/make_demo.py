"""Fabrique une maison imaginaire (dev/demo_data.js) pour les images du README et le banc de test.

Lancer :  python dev/make_demo.py
Aucune donnée réelle : les appareils, les heures et les puissances sont inventés.
"""
import datetime as dt
import json
import math
import os
import random

STEP = 15
DAYS = 7
rng = random.Random(7)
today = dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
day0 = today - dt.timedelta(days=DAYS - 1)

# (nom, slot de couleur, catégorie, forme)
APPS = {
    "g1": ("Chauffage salle de bain", 1, None, "stable"),
    "g2": ("Four", 2, "menager", "cycles"),
    "g3": ("Plaques de cuisson", 3, "menager", "paliers"),
    "g4": ("Lave-vaisselle", 4, "menager", "paliers"),
    "g5": ("Lave-linge", 5, "menager", "paliers"),
    "g6": ("Bouilloire", 6, None, "stable"),
    "g7": ("Cafetière", 8, None, "stable"),
}


def profile(gid):
    """Puissance (W) toutes les 15 s pendant une utilisation."""
    if gid == "g1":
        return [1600] * 2 + [800 + rng.randint(-30, 30)] * rng.randint(40, 70)
    if gid == "g2":
        out = [2200] * 40
        for _ in range(rng.randint(5, 9)):
            out += [2200] * rng.randint(4, 7) + [0] * rng.randint(6, 12)
        return out
    if gid == "g3":
        out = []
        for _ in range(rng.randint(5, 9)):
            out += [rng.choice([700, 1200, 1800])] * rng.randint(6, 16)
        return out
    if gid == "g4":
        return [90] * 40 + [2000] * 60 + [90] * 160 + [2000] * 40 + [25] * 100
    if gid == "g5":
        return [200] * 20 + [2100] * 50 + [300] * 140 + [500] * 30
    if gid == "g6":
        return [2200] * rng.randint(9, 13)
    if gid == "g7":
        return [1300] * rng.randint(6, 9)


def plan(d):
    """Ce qui tourne ce jour-là : [(gid, heure décimale)]."""
    out = [("g7", 7.1 + rng.random() * 0.3), ("g1", 7.4 + rng.random() * 0.3), ("g6", 16 + rng.random())]
    if d % 2 == 0:
        out += [("g2", 19.2 + rng.random() * 0.4), ("g3", 19.5)]
    else:
        out += [("g3", 12.3 + rng.random() * 0.3)]
    if d % 3 == 1:
        out += [("g4", 20.8)]
    if d % 3 == 2:
        out += [("g5", 10.5)]
    if d % 2 == 1:
        out += [("g7", 13.5)]
    return out


events, days, eid = [], {}, 1
for d in range(DAYS):
    ds = day0 + dt.timedelta(days=d)
    t0 = ds.timestamp()
    n = 86400 // STEP
    now_k = n if d < DAYS - 1 else int((dt.datetime.now().timestamp() - t0) / STEP)
    load = [0.0] * n
    for gid, hour in plan(d):
        prof = profile(gid)
        k0 = int(hour * 3600 / STEP)
        if k0 + len(prof) > now_k:
            continue
        for i, w in enumerate(prof):
            if k0 + i < n:
                load[k0 + i] += w
        heat = [w for w in prof]
        wh = sum(heat) * STEP / 3600
        events.append({"id": f"e{eid}", "start": round((t0 + k0 * STEP) * 1000), "dur": len(prof) * STEP,
                       "p": max(set(prof), key=prof.count) if gid not in ("g2", "g3") else max(prof),
                       "wh": round(wh, 1), "shape": APPS[gid][3], "split": False, "hint": None, "doubt": False,
                       "tracked_by": None, "cut": False, "steps": None, "gid": gid, "sim": 0.9, "pinned": eid % 3 != 0,
                       "st": STEP, "prof": prof})
        eid += 1
    vals = []
    for k in range(n):
        if k >= now_k:
            vals.append(None)
            continue
        t = k * STEP
        fridge = 95 if (t % 2700) < 720 else 0
        night = 230 if (t < 6.5 * 3600 or t > 23 * 3600) else 320
        vals.append(round(night + fridge + load[k] + rng.gauss(0, 6)))
    days[ds.strftime("%Y-%m-%d")] = {"start": round(t0 * 1000), "step": STEP, "values": vals, "sub": [0] * n}

groups = []
for i, (gid, (name, slot, cat, shape)) in enumerate(APPS.items()):
    mem = [e for e in events if e["gid"] == gid]
    p = sorted(e["p"] for e in mem)[len(mem) // 2] if mem else 0
    dur = sorted(e["dur"] for e in mem)[len(mem) // 2] if mem else 0
    n_sure = sum(e["pinned"] for e in mem)
    conf = round(min(1, len(mem) / 8) ** 0.5 * (n_sure + 0.85 * (len(mem) - n_sure)) / max(1, len(mem)), 3)
    groups.append({"id": gid, "letter": chr(65 + i), "slot": slot, "name": name, "status": "valide" if gid == "g1" else "nomme",
                   "hyp": None, "hyp_score": 0, "alt": None, "energy": 0.0, "created": day0.timestamp(),
                   "proof": {"n": len(mem), "days": DAYS, "weekend": 0, "p_lo": p, "p_hi": p, "p_spread": 0.05,
                             "d_lo": dur, "d_hi": dur, "coherence": 0.9, "shapes": {shape: len(mem)},
                             "hours": [0] * 24, "guesses": [[name, 0.9]], "votes": {}},
                   "conf": conf, "n_uses": len(mem), "n_sure": n_sure, "n_fixed": 0, "cat": cat, "P": p, "D": dur,
                   "shape": shape, "entities": [], "energy_dash": False})

def shared_demo():
    """Une prise partagée inventée : lave-linge et sèche-linge, quelques programmes de la semaine."""
    plug = "sensor.prise_buanderie_puissance"
    progs = [(-6, 9.5, "Lave-linge", 5100, 680, True, 1.0), (-6, 11.2, "Sèche-linge", 4600, 2900, False, 0.97),
             (-4, 18.0, "Lave-linge", 3900, 310, False, 0.91), (-2, 10.0, "Lave-linge", 5200, 700, False, 0.88),
             (-2, 12.0, None, 4200, 1900, False, 0.55), (-1, 20.5, "Sèche-linge", 6900, 1250, False, 0.93)]
    runs = []
    for i, (d, h, name, dur, wh, user, conf) in enumerate(progs):
        start = (today + dt.timedelta(days=d, hours=h)).timestamp()
        runs.append({"id": f"p{i + 1}", "start": start, "end": start + dur, "wh": wh, "name": name,
                     "guess": name or "Sèche-linge", "conf": conf, "user": user, "D": dur})
    runs.reverse()
    week = (today - dt.timedelta(days=7)).timestamp()
    apps = []
    for n in ("Lave-linge", "Sèche-linge"):
        mine = [r for r in runs if r["name"] == n]
        apps.append({"name": n, "key": f"{plug}|{n}", "kwh": round(sum(r["wh"] for r in mine) / 1000, 3), "n": len(mine),
                     "kwh7": round(sum(r["wh"] for r in mine if r["start"] >= week) / 1000, 3), "dash": n == "Lave-linge",
                     "entities": [f"sensor.empreinte_prise_{'lave_linge' if n == 'Lave-linge' else 'seche_linge'}_energie"],
                     "running": False})
    return {"entity": plug, "name": "Prise buanderie", "power": 2, "apps": apps, "open": None, "runs": runs}


state = {"groups": groups, "events": events, "kinds": {g[0]: g[0] for g in APPS.values()},
         "status": {"categories": [{"id": "menager", "name": "Électroménager", "dash": False, "since": None, "total": 0, "power": 0}],
                    "last_run": dt.datetime.now().timestamp(), "error": None, "n_events": len(events),
                    "first_sample": day0.timestamp(), "power_entity": "sensor.linky_puissance", "live": [], "can_undo": False,
                    "pending": [], "running_since": None, "running_parts": [], "tracks": [], "measured": [],
                    "shared": [shared_demo()]}}
out = os.path.join(os.path.dirname(__file__), "demo_data.js")
open(out, "w", encoding="utf-8").write("// Maison imaginaire (python dev/make_demo.py) : aucune donnée réelle.\nwindow.MOCK_STATE = "
                                       + json.dumps(state, ensure_ascii=False) + ";\nwindow.MOCK_DAYS = " + json.dumps(days) + ";\n")
print(len(events), "utilisations inventées sur", DAYS, "jours ->", out)
