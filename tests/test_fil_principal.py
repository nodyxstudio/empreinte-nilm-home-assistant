"""Le fil principal de Home Assistant ne doit jamais attendre la base de données d'Empreinte.

Ce qui tourne sur ce fil (l'affichage du panneau, les capteurs, les fonctions `async`) ne lit ni n'écrit la base
SQLite : seulement ce qui est envoyé dans `async_add_executor_job`. Deux vérifications :
1. une analyse du code : aucune méthode atteignable depuis le fil principal n'appelle `self.store` directement ;
2. l'affichage du panneau fonctionne avec une base qui refuse toute lecture.

Lancer :  python tests/test_fil_principal.py
"""
import ast
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
from ha_stub import C, make  # noqa: E402

ok_all = True


def check(label, cond, detail=""):
    global ok_all
    ok_all &= bool(cond)
    print(f"{'OK    ' if cond else 'ÉCHEC '}{label} {detail}")


# 1. Analyse du code
src = open(os.path.join(HERE, "..", "custom_components", "empreinte", "coordinator.py"), encoding="utf-8").read()
cls = next(n for n in ast.parse(src).body if isinstance(n, ast.ClassDef) and n.name == "Coordinator")
funcs = {f.name: f for f in cls.body if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))}


def in_executor(f):
    """Lignes passées à async_add_executor_job (elles tournent hors du fil principal)."""
    out, names = set(), set()
    for n in ast.walk(f):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "async_add_executor_job":
            for a in n.args:
                if isinstance(a, ast.Attribute):
                    names.add(a.attr)
                for m in ast.walk(a):
                    if hasattr(m, "lineno"):
                        out.add(m.lineno)
    return out, names


def visit(f):
    lines, sent = in_executor(f)
    called, store = set(), []
    for n in ast.walk(f):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            v = n.func.value
            if isinstance(v, ast.Name) and v.id == "self" and n.func.attr not in sent:
                called.add(n.func.attr)
            if (isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) and v.value.id == "self" and v.attr == "store"
                    and n.lineno not in lines):
                store.append((n.lineno, n.func.attr))
    return called, store


loop_side = [n for n, f in funcs.items() if isinstance(f, ast.AsyncFunctionDef)] + [
    "state_payload", "_on_state", "_on_measured", "ensure_entities", "push_entities", "live_for", "cat_power",
    "line_cost", "extra_lines", "entity_ids", "_shared_payload", "remove_entities"]
seen, todo, bad = set(), list(loop_side), []
while todo:
    name = todo.pop()
    if name in seen or name not in funcs:
        continue
    seen.add(name)
    called, store = visit(funcs[name])
    bad += [(name, *x) for x in store]
    todo += [c for c in called if c in funcs]
check("Aucun accès à la base depuis le fil principal (analyse du code)", not bad, f"({sorted(set(bad))})" if bad else f"({len(seen)} méthodes vérifiées)")

# 2. En vrai : un appareil suivi, des réponses enregistrées, puis l'affichage avec une base qui refuse tout
co = make()
co._set_meta("program_history", {"lave-linge": [{"minutes": 95, "end": 0}]})
co._set_meta("program_minutes", {"lave-linge": [90]})
co._set_meta("verdicts", [{"t": C.time.time(), "ok": True, "auto": True}])
co.tracks = [{"id": "t1", "name": "Lave-linge", "at": C.time.time() - 600, "p": 2000, "on": True, "since": C.time.time() - 600,
              "on_s": 600, "user": True, "planned_end": None, "minutes": None, "ivs": []}]
co.hass = type("H", (), {"states": type("S", (), {"get": staticmethod(lambda e: None)})()})()


class NoDisk:
    def __getattr__(self, k):
        raise AssertionError(f"lecture de la base sur le fil principal : store.{k}")


real, co.store = co.store, NoDisk()
try:
    pay = co.state_payload()
    check("Panneau affiché sans toucher à la base", True)
    t = pay["status"]["tracks"][0]
    check("… avec les durées connues de l'appareil suivi (gardées en mémoire)", t["used"] == [90, 95], f"({t['used']})")
    check("… et le bilan des réponses", pay["status"]["verdicts"]["n"] >= 1, f"({pay['status']['verdicts']})")
except AssertionError as err:
    check("Panneau affiché sans toucher à la base", False, f"({err})")
finally:
    co.store = real

# 3. La mémoire et la base disent la même chose après un redémarrage
co2 = C.Coordinator(co.hass, type(co.entry)())
co2.store = co.store
co2._load()
check("Après un redémarrage, les valeurs gardées en mémoire sont relues", co2._meta("program_minutes") == {"lave-linge": [90]}
      and len(co2._meta("verdicts", [])) == 1)

print("\nTout est bon" if ok_all else "\nIl y a des échecs")
sys.exit(0 if ok_all else 1)
