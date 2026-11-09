"""Stockage SQLite d'Empreinte (fichier /config/empreinte/empreinte.db).

Toutes les fonctions sont bloquantes : à appeler dans un exécuteur.
"""
from __future__ import annotations

import json
import os
import functools
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (ts REAL PRIMARY KEY, p REAL NOT NULL);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  start REAL NOT NULL, end REAL NOT NULL, base REAL NOT NULL,
  prof TEXT NOT NULL, f TEXT NOT NULL,
  gid TEXT, pinned INTEGER NOT NULL DEFAULT 0, excluded TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS events_start ON events(start);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


def _locked(fn):
    @functools.wraps(fn)
    def run(self, *a, **kw):
        with self.lock:
            return fn(self, *a, **kw)
    return run


class Store:
    """Une seule connexion SQLite, partagée par plusieurs fils d'exécution : un verrou les fait passer un par un
    (sinon « bad parameter or other API misuse » quand l'affichage d'un jour tombe pendant l'analyse)."""

    def __init__(self, path: str):
        self.lock = threading.RLock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(samples)")]
        if "sub" not in cols:   # puissance des prises mesurées au même instant (PC…), soustraite de l'analyse
            self.db.execute("ALTER TABLE samples ADD COLUMN sub REAL NOT NULL DEFAULT 0")
        self.db.commit()

    @_locked
    def close(self):
        self.db.close()

    # --- échantillons
    @_locked
    def add_samples(self, rows):
        self.db.executemany("INSERT OR REPLACE INTO samples(ts, p, sub) VALUES (?, ?, ?)",
                            [(r[0], r[1], r[2] if len(r) > 2 else 0) for r in rows])
        self.db.commit()

    @_locked
    def last_sample_ts(self):
        r = self.db.execute("SELECT MAX(ts) FROM samples").fetchone()
        return r[0]

    @_locked
    def first_sample_ts(self):
        r = self.db.execute("SELECT MIN(ts) FROM samples").fetchone()
        return r[0]

    @_locked
    def samples(self, t0, t1):
        """Échantillons entre t0 et t1, plus le dernier connu avant t0."""
        prev = self.db.execute("SELECT ts, p, sub FROM samples WHERE ts < ? ORDER BY ts DESC LIMIT 1", (t0,)).fetchall()
        rows = self.db.execute("SELECT ts, p, sub FROM samples WHERE ts >= ? AND ts < ? ORDER BY ts", (t0, t1)).fetchall()
        return prev + rows

    @_locked
    def sample_times(self):
        return [r[0] for r in self.db.execute("SELECT ts FROM samples ORDER BY ts")]

    @_locked
    def set_sub(self, rows):
        self.db.executemany("UPDATE samples SET sub = ? WHERE ts = ?", [(s, t) for t, s in rows])
        self.db.commit()

    @_locked
    def purge_samples(self, keep_days):
        self.db.execute("DELETE FROM samples WHERE ts < ?", (time.time() - keep_days * 86400,))
        self.db.commit()

    # --- événements
    @_locked
    def add_event(self, start, end, base, prof, f, gid=None):
        cur = self.db.execute(
            "INSERT INTO events(start, end, base, prof, f, gid) VALUES (?, ?, ?, ?, ?, ?)",
            (start, end, base, json.dumps(prof), json.dumps(f), gid))
        self.db.commit()
        return cur.lastrowid

    @_locked
    def events(self):
        rows = self.db.execute("SELECT id, start, end, base, prof, f, gid, pinned, excluded FROM events ORDER BY start").fetchall()
        return [dict(id=r[0], start=r[1], end=r[2], base=r[3], prof=json.loads(r[4]), f=json.loads(r[5]),
                     gid=r[6], pinned=bool(r[7]), excluded=set(json.loads(r[8]))) for r in rows]

    @_locked
    def update_event(self, eid, start, end, prof, f):
        self.db.execute("UPDATE events SET start = ?, end = ?, prof = ?, f = ? WHERE id = ?",
                        (start, end, json.dumps(prof), json.dumps(f), eid))
        self.db.commit()

    @_locked
    def delete_events(self, ids):
        self.db.executemany("DELETE FROM events WHERE id = ?", [(i,) for i in ids])
        self.db.commit()

    @_locked
    def save_assignments(self, evs):
        self.db.executemany("UPDATE events SET gid = ?, pinned = ?, excluded = ? WHERE id = ?",
                            [(e.gid, int(e.pinned), json.dumps(sorted(e.excluded)), e.id) for e in evs])
        self.db.commit()

    # --- méta (groupes, réglages)
    @_locked
    def get_meta(self, k, default=None):
        r = self.db.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
        return json.loads(r[0]) if r else default

    @_locked
    def set_meta(self, k, v):
        self.db.execute("INSERT OR REPLACE INTO meta(k, v) VALUES (?, ?)", (k, json.dumps(v)))
        self.db.commit()
