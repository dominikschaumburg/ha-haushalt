#!/usr/bin/env python3
"""Haushalt – Home-Assistant-App zum Erfassen und Auswerten von Hausarbeit.

Bewusst nur Standardbibliothek: HTTP-Server, SQLite, urllib für die HA-API.
"""
import base64
import json
import logging
import mimetypes
import os
import re
import socket
import sqlite3
import struct
import threading
import time
import unicodedata
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DATA_DIR = os.environ.get("DATA_DIR", "/data")
PORT = int(os.environ.get("PORT", "8099"))
DEV = os.environ.get("HAUSHALT_DEV") == "1"
SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
SEED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed.json")
TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates.json")
# 172.30.32.2 = Ingress-Proxy des Supervisors, 172.30.32.1 = Home Assistant Core (rest_command)
LEARN_WINDOW = 10  # so viele letzte Einträge fließen in den gelernten Aufwand ein
LEARN_MIN = 3  # ab so vielen Einträgen ersetzt der gelernte Wert den Standard
LEARN_GAPS_MIN = 3  # ab so vielen Abständen zwischen Erledigungen gibt es einen gelernten Rhythmus
REACTIONS = ("❤️", "🙏", "👏", "🎉", "💪", "🤩")
INGRESS_IP = "172.30.32.2"
CORE_IP = "172.30.32.1"
SENSOR_MODES = ("below", "above", "increase")
SOON_FRAC = 0.8  # ab diesem Anteil gilt eine Sensor-Regel als „bald fällig“
ACTION_SETTLE = 10  # Sekunden, bis Integrationen nach einer Aktion neue Werte melden (Roborock braucht ~7 s)
# zone.home zählt die Personen zu Hause und existiert in jeder Installation
DEFAULT_SETTINGS = {"notify_time": "18:00", "notify_require_on": "zone.home", "notify_require_off": "", "notify_done": "",
                    "onboarding_done": "", "notified_on": ""}
DONE_NOTIFY_HOURS = 6  # nachgetragene Einträge, die älter sind, lösen keine Erledigt-Nachricht aus

log = logging.getLogger("haushalt")


def load_options():
    try:
        with open(os.path.join(DATA_DIR, "options.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


OPTIONS = load_options()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value):
    """ISO-Zeitstempel (mit oder ohne Zone) → UTC-String im Speicherformat."""
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(text):
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "person"


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------------------------------------------------------------- Datenbank

SCHEMA = """
CREATE TABLE IF NOT EXISTS persons (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  ha_user_id TEXT UNIQUE,
  color INTEGER NOT NULL DEFAULT 1,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  icon TEXT NOT NULL DEFAULT '',
  sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  icon TEXT NOT NULL DEFAULT '',
  category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
  minutes INTEGER NOT NULL DEFAULT 10,
  interval_days INTEGER,
  active INTEGER NOT NULL DEFAULT 1,
  sort INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  minutes_changed_at TEXT
);
CREATE TABLE IF NOT EXISTS completions (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  done_at TEXT NOT NULL,
  minutes REAL NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  created_by TEXT,
  deleted_at TEXT,
  deleted_by TEXT
);
CREATE TABLE IF NOT EXISTS completion_persons (
  completion_id INTEGER NOT NULL REFERENCES completions(id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES persons(id),
  PRIMARY KEY (completion_id, person_id)
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY,
  at TEXT NOT NULL,
  actor TEXT,
  action TEXT NOT NULL,
  ref INTEGER,
  data TEXT
);
CREATE TABLE IF NOT EXISTS reactions (
  completion_id INTEGER NOT NULL REFERENCES completions(id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES persons(id),
  emoji TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (completion_id, person_id, emoji)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS sensor_peaks (
  entity_id TEXT PRIMARY KEY,
  peak REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_completions_done ON completions(done_at);
CREATE INDEX IF NOT EXISTS idx_completions_task ON completions(task_id);
"""


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("PRAGMA journal_mode = WAL")
        self.db.executescript(SCHEMA)
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            self._seed()
        if version < 2:
            cols = {r[1] for r in self.db.execute("PRAGMA table_info(tasks)")}
            if "minutes_changed_at" not in cols:
                self.db.execute("ALTER TABLE tasks ADD COLUMN minutes_changed_at TEXT")
        if version < 3:
            for table, col in (("tasks", "sensor"), ("tasks", "action"), ("tasks", "notify"),
                               ("completions", "sensor_base"), ("persons", "notify_service")):
                if col not in {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
        if version < 4:
            if "interval_changed_at" not in {r[1] for r in self.db.execute("PRAGMA table_info(tasks)")}:
                self.db.execute("ALTER TABLE tasks ADD COLUMN interval_changed_at TEXT")
        if version < 5:
            if "area_id" not in {r[1] for r in self.db.execute("PRAGMA table_info(tasks)")}:
                self.db.execute("ALTER TABLE tasks ADD COLUMN area_id TEXT")
        self.db.execute("PRAGMA user_version = 5")

    def _seed(self):
        with open(SEED) as f:
            seed = json.load(f)
        cat_ids = {}
        for i, cat in enumerate(seed["categories"]):
            cur = self.db.execute(
                "INSERT INTO categories (name, icon, sort) VALUES (?, ?, ?)", (cat["name"], cat["icon"], i))
            cat_ids[cat["name"]] = cur.lastrowid
        for i, task in enumerate(seed["tasks"]):
            self.db.execute(
                "INSERT INTO tasks (name, icon, category_id, minutes, interval_days, sort, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task["name"], task["icon"], cat_ids.get(task["category"]), task["minutes"],
                 task.get("interval_days"), i, now_iso()))

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, args).fetchall()]

    def x(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args)

    def audit(self, actor, action, ref, data=None):
        self.x("INSERT INTO audit (at, actor, action, ref, data) VALUES (?, ?, ?, ?, ?)",
               (now_iso(), actor, action, ref, json.dumps(data, ensure_ascii=False) if data else None))

    # ---- Einstellungen

    def settings(self):
        out = dict(DEFAULT_SETTINGS)
        out.update({r["key"]: r["value"] for r in self.q("SELECT * FROM settings")})
        return out

    def save_settings(self, data, actor):
        fields = {k: str(data[k] or "").strip() for k in DEFAULT_SETTINGS if k in data and k != "notified_on"}
        if "notify_time" in fields and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", fields["notify_time"]):
            raise ApiError(400, "Uhrzeit im Format HH:MM angeben")
        for k, v in fields.items():
            self.set_setting(k, v)
        self.audit(actor, "settings.save", None, fields)

    def set_setting(self, key, value):
        self.x("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
               (key, value))

    def peaks(self):
        return {r["entity_id"]: r["peak"] for r in self.q("SELECT * FROM sensor_peaks")}

    def set_peak(self, entity_id, value):
        self.x("INSERT INTO sensor_peaks VALUES (?, ?) ON CONFLICT(entity_id) DO UPDATE SET peak = excluded.peak",
               (entity_id, value))

    # ---- Personen

    def persons(self):
        return self.q("SELECT * FROM persons ORDER BY id")

    def ensure_person(self, ha_user_id, name):
        """Legt eine Person für einen HA-Benutzer an, falls es sie noch nicht gibt."""
        with self.lock:
            row = self.q("SELECT * FROM persons WHERE ha_user_id = ?", (ha_user_id,))
            if row:
                return row[0]
            used = {p["color"] for p in self.persons()}
            color = next((c for c in range(1, 9) if c not in used), 1)
            cur = self.x("INSERT INTO persons (name, ha_user_id, color, created_at) VALUES (?, ?, ?, ?)",
                         (name.split(" ")[0] or name, ha_user_id, color, now_iso()))
            log.info("Neue Person %s für HA-Benutzer %s", name, ha_user_id)
            return self.q("SELECT * FROM persons WHERE id = ?", (cur.lastrowid,))[0]

    def create_person(self, data, actor):
        name = str(data.get("name", "")).strip()
        if not name:
            raise ApiError(400, "Name fehlt")
        used = {p["color"] for p in self.persons()}
        color = int(data.get("color") or next((c for c in range(1, 9) if c not in used), 1))
        cur = self.x("INSERT INTO persons (name, color, created_at) VALUES (?, ?, ?)", (name, color, now_iso()))
        self.audit(actor, "person.create", cur.lastrowid, data)
        return cur.lastrowid

    def update_person(self, pid, data, actor):
        fields = {k: data[k] for k in ("name", "color", "active", "notify_service") if k in data}
        if "name" in fields and not str(fields["name"]).strip():
            raise ApiError(400, "Name fehlt")
        if "notify_service" in fields:
            fields["notify_service"] = str(fields["notify_service"] or "").strip()
        if fields.get("notify_service"):
            if not re.fullmatch(r"notify\.[a-z0-9_]+", fields["notify_service"]):
                raise ApiError(400, "Benachrichtigungsdienst im Format notify.name angeben")
        self._update("persons", pid, fields)
        self.audit(actor, "person.update", pid, fields)

    # ---- Kategorien und Aufgaben

    def _update(self, table, rid, fields):
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        cur = self.x(f"UPDATE {table} SET {sets} WHERE id = ?", (*fields.values(), rid))
        if cur.rowcount == 0:
            raise ApiError(404, "Nicht gefunden")

    def categories(self):
        return self.q("SELECT * FROM categories ORDER BY sort, id")

    def category_for(self, name, icon, actor):
        """ID einer Kategorie nach Namen, legt sie bei Bedarf an."""
        found = [c for c in self.categories() if c["name"].lower() == name.lower()]
        return found[0]["id"] if found else self.save_category(None, {"name": name, "icon": icon}, actor)

    def save_category(self, cid, data, actor):
        fields = {k: data[k] for k in ("name", "icon", "sort") if k in data}
        if cid is None:
            if not str(fields.get("name", "")).strip():
                raise ApiError(400, "Name fehlt")
            fields.setdefault("sort", len(self.categories()))
            cols = ", ".join(fields)
            cid = self.x(f"INSERT INTO categories ({cols}) VALUES ({', '.join('?' * len(fields))})",
                         tuple(fields.values())).lastrowid
        else:
            self._update("categories", cid, fields)
        self.audit(actor, "category.save", cid, fields)
        return cid

    def delete_category(self, cid, actor):
        self.x("DELETE FROM categories WHERE id = ?", (cid,))
        self.audit(actor, "category.delete", cid)

    def tasks(self):
        tasks = self.q("SELECT * FROM tasks ORDER BY sort, id")
        last = {}
        for row in self.q(
                "SELECT c.id, c.task_id, c.done_at, c.sensor_base FROM completions c"
                " JOIN (SELECT task_id, MAX(done_at) m FROM completions WHERE deleted_at IS NULL GROUP BY task_id) l"
                " ON l.task_id = c.task_id AND l.m = c.done_at WHERE c.deleted_at IS NULL"):
            last[row["task_id"]] = row
        persons = self._persons_for([r["id"] for r in last.values()])
        counts = {r["task_id"]: r["n"] for r in self.q(
            "SELECT task_id, COUNT(*) n FROM completions WHERE deleted_at IS NULL GROUP BY task_id")}
        history = {}
        for r in self.q("SELECT task_id, minutes, created_at, done_at FROM completions WHERE deleted_at IS NULL"
                        " ORDER BY done_at DESC"):
            history.setdefault(r["task_id"], []).append(r)
        for t in tasks:
            l = last.get(t["id"])
            t["last_done"] = l["done_at"] if l else None
            t["last_persons"] = persons.get(l["id"], []) if l else []
            t["last_sensor_base"] = json.loads(l["sensor_base"]) if l and l["sensor_base"] else None
            for k in ("sensor", "action", "notify"):
                t[k] = json.loads(t[k]) if t[k] else None
            t["count"] = counts.get(t["id"], 0)
            t["learned_minutes"], t["learned_from"] = self._learned(t, history.get(t["id"], []))
            t["effective_minutes"] = t["learned_minutes"] if t["learned_minutes"] is not None else t["minutes"]
            t["learned_interval"], t["learned_interval_from"] = self._learned_interval(t, history.get(t["id"], []))
            # Nur ein fester Rhythmus wird durch den gelernten ersetzt; ohne Rhythmus bleibt es ein Vorschlag
            t["effective_interval"] = t["learned_interval"] if t["interval_days"] and t["learned_interval"] else t["interval_days"]
        return tasks

    @staticmethod
    def _learned(task, rows):
        """Median der letzten Einträge seit der letzten Änderung des Standardaufwands."""
        since = task.get("minutes_changed_at") or ""
        values = sorted([r["minutes"] for r in rows if (r["created_at"] or "") >= since][:LEARN_WINDOW])
        if len(values) < LEARN_MIN:
            return None, len(values)
        mid = len(values) // 2
        median = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
        return int(round(median)), len(values)

    @staticmethod
    def _learned_interval(task, rows):
        """Median der letzten Abstände (in Tagen) zwischen Erledigungen seit der letzten Änderung des Rhythmus."""
        since = task.get("interval_changed_at") or ""
        days = sorted({datetime.fromisoformat(r["done_at"].replace("Z", "+00:00")).astimezone().date()
                       for r in rows if r["done_at"] >= since}, reverse=True)[:LEARN_WINDOW + 1]
        gaps = sorted((a - b).days for a, b in zip(days, days[1:]))
        if len(gaps) < LEARN_GAPS_MIN:
            return None, len(gaps)
        mid = len(gaps) // 2
        median = gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2
        return max(1, int(median + 0.5)), len(gaps)

    def save_task(self, tid, data, actor):
        fields = {k: data[k] for k in ("name", "icon", "category_id", "minutes", "interval_days", "active", "sort",
                                       "sensor", "action", "notify", "area_id") if k in data}
        if "name" in fields:
            fields["name"] = str(fields["name"]).strip()
            if not fields["name"]:
                raise ApiError(400, "Name fehlt")
        if "minutes" in fields:
            fields["minutes"] = max(0, int(fields["minutes"] or 0))
            current = self.q("SELECT minutes FROM tasks WHERE id = ?", (tid,)) if tid else []
            if not current or current[0]["minutes"] != fields["minutes"]:
                fields["minutes_changed_at"] = now_iso()  # Lernen beginnt neu ab dem neuen Standard
        if "interval_days" in fields:
            fields["interval_days"] = int(fields["interval_days"]) if fields["interval_days"] else None
            current = self.q("SELECT interval_days FROM tasks WHERE id = ?", (tid,)) if tid else []
            if not current or current[0]["interval_days"] != fields["interval_days"]:
                fields["interval_changed_at"] = now_iso()  # Lernen beginnt neu ab dem neuen Rhythmus
        if "area_id" in fields:
            fields["area_id"] = str(fields["area_id"]).strip() or None if fields["area_id"] else None
        if "sensor" in fields:
            fields["sensor"] = self._clean_sensor(fields["sensor"])
        if "action" in fields:
            fields["action"] = self._clean_action(fields["action"])
        if "notify" in fields:
            n = fields["notify"]
            fields["notify"] = json.dumps(sorted({int(p) for p in n})) if isinstance(n, list) else None
        if tid is None:
            if "name" not in fields:
                raise ApiError(400, "Name fehlt")
            fields.setdefault("sort", (self.q("SELECT COALESCE(MAX(sort), 0) + 1 s FROM tasks")[0]["s"]))
            fields["created_at"] = now_iso()
            cols = ", ".join(fields)
            tid = self.x(f"INSERT INTO tasks ({cols}) VALUES ({', '.join('?' * len(fields))})",
                         tuple(fields.values())).lastrowid
        else:
            self._update("tasks", tid, fields)
        self.audit(actor, "task.save", tid, fields)
        return tid

    @staticmethod
    def _entities(value):
        ids = [str(e).strip() for e in (value or []) if str(e).strip()]
        for e in ids:
            if not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", e):
                raise ApiError(400, f"Ungültige Entität {e!r}")
        return ids

    def _clean_sensor(self, sensor):
        """Sensor-Regel: {mode, entities, threshold, base}; ohne Entitäten wird sie entfernt."""
        if not sensor or not sensor.get("entities"):
            return None
        if sensor.get("mode") not in SENSOR_MODES:
            raise ApiError(400, "Unbekannte Sensor-Regel")
        out = {"mode": sensor["mode"], "entities": self._entities(sensor["entities"]),
               "threshold": float(sensor.get("threshold") or 0)}
        if not out["entities"]:
            return None
        if out["mode"] == "increase":
            if out["threshold"] <= 0:
                raise ApiError(400, "Zuwachs muss größer als 0 sein")
            out["base"] = {k: float(v) for k, v in (sensor.get("base") or {}).items() if k in out["entities"]}
        return json.dumps(out)

    def _clean_action(self, action):
        if not action or not str(action.get("service") or "").strip():
            return None
        service = str(action["service"]).strip()
        if not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", service):
            raise ApiError(400, "Dienst im Format domain.dienst angeben, z. B. button.press")
        return json.dumps({"service": service, "entities": self._entities(action.get("entities"))})

    def delete_task(self, tid, actor):
        """Aufgaben mit Einträgen werden nur archiviert, damit die Statistik erhalten bleibt."""
        if self.q("SELECT 1 FROM completions WHERE task_id = ? LIMIT 1", (tid,)):
            self._update("tasks", tid, {"active": 0})
            self.audit(actor, "task.archive", tid)
            return "archived"
        self.x("DELETE FROM tasks WHERE id = ?", (tid,))
        self.audit(actor, "task.delete", tid)
        return "deleted"

    # ---- Erledigungen

    def _persons_for(self, completion_ids):
        out = {}
        for i in range(0, len(completion_ids), 500):
            chunk = completion_ids[i:i + 500]
            if not chunk:
                continue
            for r in self.q(f"SELECT completion_id, person_id FROM completion_persons WHERE completion_id IN"
                            f" ({', '.join('?' * len(chunk))}) ORDER BY person_id", chunk):
                out.setdefault(r["completion_id"], []).append(r["person_id"])
        return out

    def _reactions_for(self, completion_ids):
        out = {}
        for i in range(0, len(completion_ids), 500):
            chunk = completion_ids[i:i + 500]
            if not chunk:
                continue
            for r in self.q(f"SELECT * FROM reactions WHERE completion_id IN ({', '.join('?' * len(chunk))})"
                            " ORDER BY created_at", chunk):
                out.setdefault(r.pop("completion_id"), []).append(r)
        return out

    def completions(self, since=None, until=None, include_deleted=False, task_id=None, limit=None):
        where, args = [], []
        if since:
            where.append("done_at >= ?")
            args.append(parse_iso(since))
        if until:
            where.append("done_at < ?")
            args.append(parse_iso(until))
        if not include_deleted:
            where.append("deleted_at IS NULL")
        if task_id:
            where.append("task_id = ?")
            args.append(int(task_id))
        sql = "SELECT * FROM completions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY done_at DESC, id DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        rows = self.q(sql, args)
        ids = [r["id"] for r in rows]
        persons = self._persons_for(ids)
        reactions = self._reactions_for(ids)
        for r in rows:
            r["person_ids"] = persons.get(r["id"], [])
            r["reactions"] = reactions.get(r["id"], [])
        return rows

    def completion(self, cid):
        rows = self.q("SELECT * FROM completions WHERE id = ?", (cid,))
        if not rows:
            raise ApiError(404, "Eintrag nicht gefunden")
        rows[0]["person_ids"] = self._persons_for([cid]).get(cid, [])
        rows[0]["reactions"] = self._reactions_for([cid]).get(cid, [])
        return rows[0]

    def _check_persons(self, person_ids):
        ids = sorted({int(p) for p in person_ids})
        if not ids:
            raise ApiError(400, "Mindestens eine Person auswählen")
        known = {p["id"] for p in self.persons()}
        if not set(ids) <= known:
            raise ApiError(400, "Unbekannte Person")
        return ids

    def create_completion(self, data, actor):
        task = self.q("SELECT * FROM tasks WHERE id = ?", (int(data.get("task_id") or 0),))
        if not task:
            raise ApiError(400, "Unbekannte Aufgabe")
        task = task[0]
        ids = self._check_persons(data.get("person_ids") or [])
        done_at = parse_iso(data["done_at"]) if data.get("done_at") else now_iso()
        if data.get("minutes") not in (None, ""):
            minutes = float(data["minutes"])
        else:
            minutes = float(next(t["effective_minutes"] for t in self.tasks() if t["id"] == task["id"]))
        with self.lock:
            base = json.dumps(data["sensor_base"]) if data.get("sensor_base") else None
            cur = self.x("INSERT INTO completions (task_id, done_at, minutes, note, created_at, created_by, sensor_base)"
                         " VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (task["id"], done_at, max(0.0, minutes), str(data.get("note") or ""), now_iso(), actor, base))
            cid = cur.lastrowid
            for pid in ids:
                self.x("INSERT INTO completion_persons VALUES (?, ?)", (cid, pid))
        self.audit(actor, "completion.create", cid, data)
        return self.completion(cid)

    def update_completion(self, cid, data, actor):
        before = self.completion(cid)
        fields = {}
        if "done_at" in data:
            fields["done_at"] = parse_iso(data["done_at"])
        if "minutes" in data:
            fields["minutes"] = max(0.0, float(data["minutes"] or 0))
        if "note" in data:
            fields["note"] = str(data["note"] or "")
        if "task_id" in data:
            if not self.q("SELECT 1 FROM tasks WHERE id = ?", (int(data["task_id"]),)):
                raise ApiError(400, "Unbekannte Aufgabe")
            fields["task_id"] = int(data["task_id"])
        with self.lock:
            self._update("completions", cid, fields)
            if "person_ids" in data:
                ids = self._check_persons(data["person_ids"])
                self.x("DELETE FROM completion_persons WHERE completion_id = ?", (cid,))
                for pid in ids:
                    self.x("INSERT INTO completion_persons VALUES (?, ?)", (cid, pid))
        self.audit(actor, "completion.update", cid, {"before": before, "changes": data})
        return self.completion(cid)

    def delete_completion(self, cid, actor):
        self.completion(cid)
        self.x("UPDATE completions SET deleted_at = ?, deleted_by = ? WHERE id = ?", (now_iso(), actor, cid))
        self.audit(actor, "completion.delete", cid)
        return self.completion(cid)

    def restore_completion(self, cid, actor):
        self.completion(cid)
        self.x("UPDATE completions SET deleted_at = NULL, deleted_by = NULL WHERE id = ?", (cid,))
        self.audit(actor, "completion.restore", cid)
        return self.completion(cid)

    def toggle_reaction(self, cid, person, emoji):
        """Setzt oder entfernt eine Reaktion; gibt True zurück, wenn sie neu ist."""
        if emoji not in REACTIONS:
            raise ApiError(400, "Unbekannte Reaktion")
        c = self.completion(cid)
        if c["deleted_at"]:
            raise ApiError(400, "Eintrag ist gelöscht")
        if person["id"] in c["person_ids"]:
            raise ApiError(400, "Auf eigene Aufgaben kann man nicht reagieren")
        with self.lock:
            cur = self.x("DELETE FROM reactions WHERE completion_id = ? AND person_id = ? AND emoji = ?",
                         (cid, person["id"], emoji))
            added = cur.rowcount == 0
            if added:
                self.x("INSERT INTO reactions VALUES (?, ?, ?, ?)", (cid, person["id"], emoji, now_iso()))
        self.audit(person["name"], "reaction.add" if added else "reaction.remove", cid, {"emoji": emoji})
        return added, self.completion(cid)

    def export(self):
        return {
            "exported_at": now_iso(),
            "persons": self.persons(),
            "categories": self.categories(),
            "tasks": self.q("SELECT * FROM tasks ORDER BY id"),
            "completions": self.completions(include_deleted=True),
            "audit": self.q("SELECT * FROM audit ORDER BY id"),
            "reactions": self.q("SELECT * FROM reactions ORDER BY created_at"),
            "settings": self.q("SELECT * FROM settings WHERE key != 'notified_on'"),
        }

    def import_(self, data, actor):
        """Ersetzt den gesamten Bestand durch einen Export (IDs bleiben erhalten)."""
        tables = {
            "persons": ("id", "name", "ha_user_id", "color", "active", "created_at", "notify_service"),
            "categories": ("id", "name", "icon", "sort"),
            "tasks": ("id", "name", "icon", "category_id", "minutes", "interval_days", "active", "sort", "created_at",
                      "minutes_changed_at", "sensor", "action", "notify", "interval_changed_at", "area_id"),
            "completions": ("id", "task_id", "done_at", "minutes", "note", "created_at", "created_by",
                            "deleted_at", "deleted_by", "sensor_base"),
            "audit": ("id", "at", "actor", "action", "ref", "data"),
            "reactions": ("completion_id", "person_id", "emoji", "created_at"),
            "settings": ("key", "value"),
        }
        for key in ("persons", "categories", "tasks", "completions"):
            if not isinstance(data.get(key), list):
                raise ApiError(400, f"Export unvollständig: {key} fehlt")
        with self.lock:
            self.db.execute("BEGIN")
            try:
                for table in ("reactions", "completion_persons", "completions", "tasks", "categories", "persons", "audit",
                              "settings"):
                    self.db.execute(f"DELETE FROM {table}")
                for table, cols in tables.items():
                    for row in data.get(table) or []:
                        self.db.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                                        tuple(row.get(c) for c in cols))
                for c in data["completions"]:
                    for pid in c.get("person_ids") or []:
                        self.db.execute("INSERT INTO completion_persons VALUES (?, ?)", (c["id"], pid))
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
        self.audit(actor, "import", None, {k: len(data.get(k) or []) for k in tables})
        return {k: len(data.get(k) or []) for k in tables}


# ---------------------------------------------------------------- Home Assistant

CONTEXT_TEMPLATE = """
{%- set ns = namespace(e=[], a=[]) -%}
{%- for s in states -%}{%- set d = device_id(s.entity_id) -%}
{%- set ns.e = ns.e + [[s.entity_id, d, ((device_attr(d, 'name_by_user') or device_attr(d, 'name')) if d else none), area_id(s.entity_id)]] -%}
{%- endfor -%}
{%- for a in areas() -%}{%- set ns.a = ns.a + [[a, area_name(a)]] -%}{%- endfor -%}
{{ {"areas": ns.a, "entities": ns.e} | tojson }}
"""


def fmt_num(v):
    """Zahl für Texte: deutsches Komma, echtes Minus, sinnvolle Nachkommastellen."""
    text = f"{v:.0f}" if abs(v) >= 10 else f"{v:.2f}".rstrip("0").rstrip(".") if abs(v) < 1 else f"{v:.1f}".rstrip("0").rstrip(".")
    return text.replace(".", ",").replace("-", "−")


def interval_due(t, today):
    """Fälligkeit aus dem Rhythmus (wie due() im Frontend)."""
    interval = t.get("effective_interval") or t["interval_days"]
    if not interval or not t["last_done"]:
        return None
    last = datetime.fromisoformat(t["last_done"].replace("Z", "+00:00")).astimezone().date()
    elapsed = (today - last).days
    diff = interval - elapsed
    frac = min(1, elapsed / interval)
    if diff < 0:
        return {"status": "overdue", "frac": frac, "label": f"Seit {-diff} {'Tag' if diff == -1 else 'Tagen'} überfällig"}
    if diff == 0:
        return {"status": "due", "frac": frac, "label": "Heute fällig"}
    soon = diff <= max(1, round(interval * 0.2))
    return {"status": "soon" if soon else "ok", "frac": frac, "label": "Morgen fällig" if diff == 1 else f"Fällig in {diff} Tagen"}


RANK = {"overdue": 4, "due": 3, "soon": 2, "ok": 1}


def combined_due(t, today):
    """Dringendere von Rhythmus und Sensor-Regel."""
    parts = [d for d in (interval_due(t, today), t.get("sensor_state")) if d and d["status"] in RANK]
    return max(parts, key=lambda d: (RANK[d["status"]], d["frac"])) if parts else None


def short_names(names):
    """Gemeinsamen Gerätenamen am Anfang weglassen („Roborock S5 Max Verbleibende Filterzeit“ → „Verbleibende Filterzeit“)."""
    words = [n.split() for n in names]
    if len(words) < 2:
        return names
    common = 0
    while all(len(w) > common + 1 and w[common] == words[0][common] for w in words):
        common += 1
    words = [w[common:] for w in words]
    while all(len(w) > 1 and w[-1] == words[0][-1] for w in words):  # gemeinsames Ende („… Batterie“)
        words = [w[:-1] for w in words]
    return [" ".join(w) for w in words]


class WebSocket:
    """Minimaler WebSocket-Client (nur Text-Frames), damit die App ohne Abhängigkeiten auskommt."""

    def __init__(self, host, path, port=80):
        self.sock = socket.create_connection((host, port), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while not head.endswith(b"\r\n\r\n"):
            chunk = self.sock.recv(1)
            if not chunk:
                raise ConnectionError("Verbindung beim Handshake geschlossen")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.split(b"\r\n", 1)[0].decode(errors="replace"))
        self.sock.settimeout(120)

    def _read(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Verbindung geschlossen")
            buf += chunk
        return buf

    def _send(self, opcode, payload):
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | opcode]) + (bytes([0x80 | n]) if n < 126 else
                                         struct.pack("!BH", 0x80 | 126, n) if n < 65536 else
                                         struct.pack("!BQ", 0x80 | 127, n))
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send_json(self, data):
        self._send(0x1, json.dumps(data).encode())

    def recv_json(self):
        message = b""
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._read(8))[0]
            mask = self._read(4) if b2 & 0x80 else None
            payload = self._read(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            opcode = b1 & 0x0F
            if opcode == 0x8:
                raise ConnectionError("Verbindung vom Server geschlossen")
            if opcode == 0x9:
                self._send(0xA, payload)
                continue
            if opcode in (0x0, 0x1, 0x2):
                message += payload
                if b1 & 0x80:
                    return json.loads(message)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class HomeAssistant:
    """Gleicht Personen mit HA ab, wertet Sensor-Regeln aus, veröffentlicht Sensoren, Ereignisse und Benachrichtigungen."""

    def __init__(self, store):
        self.store = store
        self.wake = threading.Event()
        self.enabled = bool(SUPERVISOR_TOKEN)
        self.sensors = OPTIONS.get("sensors", True)
        self._states, self._states_at = {}, 0
        self.notified = {}  # task_id → Dienste, die heute eine Nachricht bekommen haben (zum Wegräumen)
        # Panel-Pfad für Tipp auf die Nachricht: Hostname „f8832456-haushalt“ → „/f8832456_haushalt“
        self.panel = "/" + os.environ.get("HOSTNAME", "").replace("-", "_") if "-" in os.environ.get("HOSTNAME", "") else None

    def call(self, method, path, body=None):
        req = urllib.request.Request(
            "http://supervisor/core/api" + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
        return json.loads(raw) if raw else None

    def states(self, max_age=15):
        """Alle Zustände aus HA, kurz zwischengespeichert. Im Dev-Modus optional aus HAUSHALT_DEV_STATES."""
        if not self.enabled:
            path = os.environ.get("HAUSHALT_DEV_STATES")
            if DEV and path and os.path.exists(path):
                with open(path) as f:
                    return {s["entity_id"]: s for s in json.load(f)}
            return {}
        if time.time() - self._states_at > max_age:
            try:
                self._states = {s["entity_id"]: s for s in self.call("GET", "/states") or []}
                self._states_at = time.time()
            except Exception as e:  # noqa: BLE001 – lieber alte Werte als gar keine
                log.warning("Zustände nicht abrufbar: %s", e)
        return self._states

    @staticmethod
    def value(state):
        """Zahl aus einem Zustand; an/aus (auch home, open, true) zählt als 1/0."""
        try:
            return float(state["state"])
        except (TypeError, ValueError, KeyError):
            pass
        flag = {"on": 1.0, "home": 1.0, "open": 1.0, "true": 1.0, "off": 0.0, "not_home": 0.0, "closed": 0.0,
                "false": 0.0}
        return flag.get(str((state or {}).get("state")).lower())

    def context(self, max_age=600):
        """Bereiche und Geräte zu jeder Entität über die Template-API (REST kennt keine Registries)."""
        if not self.enabled:
            path = os.environ.get("HAUSHALT_DEV_CONTEXT")
            if DEV and path and os.path.exists(path):
                with open(path) as f:
                    raw = json.load(f)
            else:
                return {"areas": {}, "entities": {}}
        elif time.time() - getattr(self, "_context_at", 0) > max_age:
            try:
                raw = self.call("POST", "/template", {"template": CONTEXT_TEMPLATE})
                self._context_raw, self._context_at = raw, time.time()
            except Exception as e:  # noqa: BLE001
                log.warning("Bereiche und Geräte nicht abrufbar: %s", e)
                raw = getattr(self, "_context_raw", {"areas": [], "entities": []})
        else:
            raw = self._context_raw
        return {"areas": dict(raw["areas"]),
                "entities": {e[0]: {"device": e[1], "device_name": e[2], "area": e[3]} for e in raw["entities"]}}

    def areas(self):
        return [{"id": k, "name": v} for k, v in sorted(self.context()["areas"].items(), key=lambda a: a[1])]

    def catalog(self):
        """Entitäten und Benachrichtigungsdienste für die Auswahl im Editor."""
        domains = ("sensor", "binary_sensor", "counter", "input_number", "number", "button", "input_button", "script",
                   "switch", "input_boolean", "calendar", "person", "group", "scene")
        entities = sorted(({"id": e, "name": s["attributes"].get("friendly_name") or e,
                            "unit": s["attributes"].get("unit_of_measurement") or "", "state": s["state"]}
                           for e, s in self.states().items() if e.split(".")[0] in domains), key=lambda x: x["id"])
        notify = []
        if self.enabled:
            try:
                notify = sorted(self.notify_services())
            except Exception as e:  # noqa: BLE001
                log.warning("Dienste nicht abrufbar: %s", e)
        return {"entities": entities, "notify": notify}

    # ---- Sensor-Regeln

    def sensor_state(self, t, states, peaks):
        rule = t.get("sensor")
        if not rule:
            return None
        thr, mode = rule["threshold"], rule["mode"]
        base = t.get("last_sensor_base") or rule.get("base") or {}
        names = [re.sub(r"\s+(Batterie|Battery\+?|Akku|Battery level)$", "", n, flags=re.I) for n in
                 short_names([(states.get(e) or {}).get("attributes", {}).get("friendly_name") or e
                              for e in rule["entities"]])]
        items = []
        for e, name in zip(rule["entities"], names):
            st = states.get(e)
            v = self.value(st)
            if v is None:
                continue
            unit = st["attributes"].get("unit_of_measurement") or ""
            if mode == "below":
                peak = peaks.get(e)
                if peak is None or v > peak:
                    self.store.set_peak(e, v)
                    peak = v
                hit = v <= thr
                frac = 1 if hit else (peak - v) / (peak - thr) if peak > thr else 0
                shown = v
            elif mode == "above":
                hit = v >= thr
                frac = 1 if hit else v / thr if thr > 0 else 0
                shown = v
            else:
                b = base.get(e, v)
                shown = v - b if v >= b else v  # Zähler zurückgesetzt → ab null weiterzählen
                hit = shown >= thr
                frac = shown / thr
            flag = re.fullmatch(r"-?[\d.]+(e-?\d+)?", str(st["state"])) is None  # an/aus statt Zahl
            items.append({"name": name, "value": shown, "unit": unit, "hit": hit, "frac": max(0, min(1, frac)),
                          "flag": flag})
        if not items:
            return {"status": "unknown", "frac": 0, "label": "Sensor nicht verfügbar"}
        if all(i["flag"] for i in items):
            hits = [i["name"] for i in items if i["hit"]]
            return {"status": "due" if hits else "ok", "frac": 1 if hits else 0, "items": items,
                    "label": f"Fällig: {', '.join(hits)}" if hits else "Nicht fällig"}
        u = lambda i: f"{fmt_num(i['value'])}{' ' + i['unit'] if i['unit'] else ''}"  # noqa: E731
        with_name = len(items) > 1
        hits = [i for i in items if i["hit"]]
        if hits:
            what = ", ".join(f"{i['name']} {u(i)}" if with_name else u(i) for i in hits)
            label = f"Fällig: {what}" + (" seit dem Abhaken" if mode == "increase" else "")
            return {"status": "due", "frac": 1, "label": label, "items": items}
        # Am nächsten an der Grenze; bei Gleichstand (z. B. alles frisch zurückgesetzt) der kleinste Restwert
        top = max(items, key=lambda i: (i["frac"], -i["value"] if mode == "below" else i["value"]))
        unit = f" {top['unit']}" if top["unit"] else ""
        if mode == "below":
            label = f"Noch {u(top)}" + (f" · {top['name']}" if with_name else "")
        elif mode == "above":
            label = f"{fmt_num(top['value'])} von {fmt_num(thr)}{unit}" + (f" · {top['name']}" if with_name else "")
        else:
            label = f"{fmt_num(top['value'])} von {fmt_num(thr)}{unit} seit dem Abhaken"
        return {"status": "soon" if top["frac"] >= SOON_FRAC else "ok", "frac": top["frac"], "label": label, "items": items}

    def annotate(self, tasks):
        if any(t.get("sensor") for t in tasks):
            states, peaks = self.states(), self.store.peaks()
            for t in tasks:
                t["sensor_state"] = self.sensor_state(t, states, peaks)
        return tasks

    def sensor_base(self, task, keep=None):
        """Aktuelle Zählerstände für Zuwachs-Regeln (vorhandene Werte aus keep bleiben)."""
        rule = task.get("sensor") if task else None
        if not rule or rule.get("mode") != "increase":
            return None
        states = self.states(max_age=0)
        out = dict(keep or {})
        for e in rule.get("entities") or []:
            v = self.value(states.get(e))
            if e not in out and v is not None:
                out[e] = v
        return out or None

    # ---- Abhaken: Aktion, Ereignis, Nachrichten wegräumen

    def after_change(self, completion=None):
        self.wake.set()
        if completion:
            threading.Thread(target=self.on_completion, args=(completion,), daemon=True).start()

    def on_completion(self, completion):
        task = next((t for t in self.store.tasks() if t["id"] == completion["task_id"]), {})
        names = {p["id"]: p["name"] for p in self.store.persons()}
        self.fire("haushalt_erledigt", {
            "aufgabe": task.get("name"), "personen": [names.get(i) for i in completion["person_ids"]],
            "minuten": completion["minutes"], "zeitpunkt": completion["done_at"],
        })
        if not self.enabled:
            return
        action = task.get("action")
        if action:
            domain, service = action["service"].split(".", 1)
            try:
                self.call("POST", f"/services/{domain}/{service}",
                          {"entity_id": action["entities"]} if action["entities"] else {})
                log.info("Aktion %s für %s ausgeführt", action["service"], task.get("name"))
            except Exception as e:  # noqa: BLE001
                log.warning("Aktion %s fehlgeschlagen: %s", action["service"], e)
        for service in self.notified.pop(task.get("id"), set()):
            self.notify(service, {"message": "clear_notification", "data": {"tag": f"haushalt_{task['id']}"}})
        self.notify_done(completion, task, names)
        self._states_at = 0  # Sensoren haben sich durch die Aktion vermutlich geändert
        if action:  # manche Integrationen melden die neuen Werte erst nach ein paar Sekunden
            time.sleep(ACTION_SETTLE)
            self._states_at = 0
            self.wake.set()

    def fire(self, event, data):
        if not self.enabled:
            return
        try:
            self.call("POST", f"/events/{event}", data)
        except Exception as e:  # noqa: BLE001 – HA nicht erreichbar darf die App nicht stören
            log.warning("Ereignis %s fehlgeschlagen: %s", event, e)

    # ---- Benachrichtigungen

    def notify(self, service, payload):
        try:
            self.call("POST", f"/services/notify/{service.split('.', 1)[1]}", payload)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("Benachrichtigung über %s fehlgeschlagen: %s", service, e)
            return False

    def push(self, person, title, message, tag=None, actions=None):
        data = {}
        if tag:
            data["tag"] = tag
        if actions:
            data["actions"] = actions
        if self.panel:
            data["url"] = self.panel
            data["clickAction"] = self.panel
        return self.notify(person["notify_service"], {"title": title, "message": message, "data": data})

    def notify_done(self, completion, task, names):
        """Alle anderen benachrichtigen, wenn jemand etwas erledigt hat (Einstellung „notify_done“)."""
        if self.store.settings()["notify_done"] != "1" or not completion.get("id"):
            return
        done_at = datetime.fromisoformat(completion["done_at"].replace("Z", "+00:00"))
        if datetime.now(timezone.utc) - done_at > timedelta(hours=DONE_NOTIFY_HOURS):
            return
        who = [names.get(i) for i in completion["person_ids"] if names.get(i)]
        verb = "haben" if len(who) > 1 else "hat"
        for p in self.store.persons():
            if p["active"] and p["notify_service"] and p["id"] not in completion["person_ids"]:
                self.push(p, f"✅ {task.get('icon') or ''} {task.get('name')}".replace("  ", " "),
                          f"{' & '.join(who)} {verb} das erledigt.", tag=f"haushalt_erledigt_{completion['id']}")

    def maybe_notify(self):
        """Einmal am Tag zur eingestellten Uhrzeit (bis 3 Stunden danach, falls die App gerade neu startet)."""
        st = self.store.settings()
        now = datetime.now().astimezone()
        today = now.date().isoformat()
        if st["notified_on"] == today:
            return
        hh, mm = map(int, st["notify_time"].split(":"))
        at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if not at <= now < at + timedelta(hours=3):
            return
        self.store.set_setting("notified_on", today)
        states = self.states(max_age=0)
        need_on, need_off = st["notify_require_on"], st["notify_require_off"]
        if need_on and not (self.value(states.get(need_on)) or 0) > 0:
            log.info("Keine Benachrichtigung: %s ist nicht an", need_on)
            return
        if need_off and (self.value(states.get(need_off)) or 0) > 0:
            log.info("Keine Benachrichtigung: %s ist an", need_off)
            return
        self.notify_due()

    def notify_due(self):
        persons = {p["id"]: p for p in self.store.persons() if p["active"] and p["notify_service"]}
        today = datetime.now().astimezone().date()
        sent = 0
        for t in self.annotate(self.store.tasks()):
            d = combined_due(t, today)
            if not t["active"] or not t["notify"] or not d or d["status"] not in ("due", "overdue"):
                continue
            for pid in t["notify"]:
                p = persons.get(pid)
                if p and self.push(p, f"⏰ {t['icon']} {t['name']}".replace("  ", " "), d["label"],
                                   tag=f"haushalt_{t['id']}",
                                   actions=[{"action": f"HAUSHALT_DONE_{t['id']}_{pid}", "title": "Erledigt ✅"}]):
                    self.notified.setdefault(t["id"], set()).add(p["notify_service"])
                    sent += 1
        log.info("%s Benachrichtigungen verschickt", sent)

    def on_action(self, action):
        """Erledigt-Knopf aus einer Benachrichtigung."""
        m = re.fullmatch(r"HAUSHALT_DONE_(\d+)_(\d+)", action or "")
        if not m:
            return
        tid, pid = int(m.group(1)), int(m.group(2))
        task = next((t for t in self.annotate(self.store.tasks()) if t["id"] == tid), None)
        p = next((p for p in self.store.persons() if p["id"] == pid), None)
        if not task or not p:
            return
        d = combined_due(task, datetime.now().astimezone().date())
        if not d or d["status"] not in ("due", "overdue"):
            log.info("%s ist schon erledigt, Knopf von %s ignoriert", task["name"], p["name"])
            for service in self.notified.pop(tid, set()):
                self.notify(service, {"message": "clear_notification", "data": {"tag": f"haushalt_{tid}"}})
            return
        data = {"task_id": tid, "person_ids": [pid], "sensor_base": self.sensor_base(task)}
        completion = self.store.create_completion(data, p["name"])
        log.info("%s per Benachrichtigung von %s erledigt", task["name"], p["name"])
        self.after_change(completion)

    def listen(self):
        """Hört über die WebSocket-API auf Antworten aus Benachrichtigungen."""
        while True:
            ws = None
            try:
                ws = WebSocket("supervisor", "/core/websocket")
                ws.recv_json()
                ws.send_json({"type": "auth", "access_token": SUPERVISOR_TOKEN})
                if ws.recv_json().get("type") != "auth_ok":
                    raise ConnectionError("Anmeldung an der WebSocket-API fehlgeschlagen")
                ws.send_json({"id": 1, "type": "subscribe_events", "event_type": "mobile_app_notification_action"})
                log.info("Höre auf Antworten aus Benachrichtigungen")
                n, idle = 1, 0
                while True:
                    try:
                        msg = ws.recv_json()
                        idle = 0
                    except socket.timeout:
                        idle += 1
                        if idle > 1:
                            raise ConnectionError("keine Antwort auf Ping")
                        n += 1
                        ws.send_json({"id": n, "type": "ping"})
                        continue
                    if msg.get("type") == "event":
                        try:
                            self.on_action(msg["event"]["data"].get("action"))
                        except Exception:  # noqa: BLE001
                            log.exception("Antwort aus Benachrichtigung nicht verarbeitet")
            except Exception as e:  # noqa: BLE001
                log.warning("WebSocket getrennt: %s", e)
            finally:
                if ws:
                    ws.close()
            time.sleep(20)

    # ---- Sensoren in HA

    def notify_services(self):
        services = {d["domain"]: d["services"] for d in self.call("GET", "/services") or []}
        return {f"notify.{n}" for n in services.get("notify", {}) if n not in ("send_message", "persistent_notification")}

    def sync_persons(self):
        """Personen aus HA anlegen und ihnen den Dienst ihrer Companion-App zuordnen (notify.mobile_app_<tracker>)."""
        services = None
        for state in self.call("GET", "/states") or []:
            if not (state["entity_id"].startswith("person.") and state["attributes"].get("user_id")):
                continue
            attrs = state["attributes"]
            person = self.store.ensure_person(attrs["user_id"], attrs.get("friendly_name") or state["entity_id"][7:])
            if person["notify_service"] is not None:  # schon gesetzt oder bewusst geleert ("")
                continue
            services = self.notify_services() if services is None else services
            service = self.guess_notify(person, attrs.get("device_trackers") or [], services)
            if service:
                self.store.update_person(person["id"], {"notify_service": service}, "Home Assistant")
                log.info("Benachrichtigungen für %s über %s", person["name"], service)

    def guess_notify(self, person, trackers, services):
        """Dienst der Companion-App: über die Tracker der Person, sonst über ein Gerät, das nach ihr heißt."""
        used = {p["notify_service"] for p in self.store.persons() if p["notify_service"]}
        free = sorted(x for x in services if x.startswith("notify.mobile_app_") and x not in used)
        for tracker in trackers:
            service = "notify.mobile_app_" + tracker.split(".", 1)[1]
            if service in free:
                return service
        first = slugify(person["name"]).split("_")[0]
        entities = self.context()["entities"]
        for service in free:
            obj = service[len("notify.mobile_app_"):]
            names = {i["device_name"] for e, i in entities.items() if i["device_name"]
                     and (e.split(".", 1)[1] == obj or e.split(".", 1)[1].startswith(obj + "_"))}
            if any(first in slugify(n).split("_") for n in names):  # „iPhone von Sarah“
                return service
        return None

    def publish(self):
        local_today = datetime.now().astimezone().date()
        due = []
        for t in self.annotate(self.store.tasks()):
            d = combined_due(t, local_today) if t["active"] else None
            if d and d["status"] in ("due", "overdue"):
                due.append({"name": t["name"], "status": d["label"]})
        self.call("POST", "/states/sensor.haushalt_faellig", {
            "state": len(due),
            "attributes": {"friendly_name": "Haushalt fällig", "icon": "mdi:broom", "unit_of_measurement": "Aufgaben",
                           "aufgaben": [d["name"] for d in due], "details": due},
        })
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        week = self.store.completions(since=since)
        total = sum(c["minutes"] for c in week) or 0
        for p in self.store.persons():
            if not p["active"]:
                continue
            mine = [c for c in week if p["id"] in c["person_ids"]]
            minutes = sum(c["minutes"] / len(c["person_ids"]) for c in mine)
            self.call("POST", f"/states/sensor.haushalt_{slugify(p['name'])}_7_tage", {
                "state": round(minutes),
                "attributes": {"friendly_name": f"Haushalt {p['name']} (7 Tage)", "icon": "mdi:account-clock",
                               "unit_of_measurement": "min", "device_class": "duration", "state_class": "measurement",
                               "aufgaben": len(mine), "anteil": round(100 * minutes / total) if total else 0},
            })

    def run(self):
        if not self.enabled:
            log.info("Kein SUPERVISOR_TOKEN – HA-Anbindung deaktiviert")
            return
        threading.Thread(target=self.listen, daemon=True).start()
        last_sync = last_publish = 0
        while True:
            woken = self.wake.is_set()
            self.wake.clear()
            try:
                if time.time() - last_sync > 600:
                    self.sync_persons()
                    last_sync = time.time()
                if self.sensors and (woken or time.time() - last_publish > 300):
                    self.publish()
                    last_publish = time.time()
                self.maybe_notify()
            except Exception as e:  # noqa: BLE001
                log.warning("HA-Abgleich fehlgeschlagen: %s", e)
            self.wake.wait(60)


# ---------------------------------------------------------------- Vorlagen

NOT_REPLACEABLE = r"iphone|ipad|macbook|laptop|tablet|phone|watch|pixel|galaxy|sonos|smart_series|oral_b|zahnb"


def load_templates():
    with open(TEMPLATES) as f:
        return json.load(f)


def room_kind(name, rooms):
    """Raumart aus dem HA-Bereichsnamen („Gäste-WC“ → bathroom)."""
    n = f"_{slugify(name)}_"
    for kind, words in rooms.items():
        if any(f"_{slugify(w)}_" in n for w in words):
            return kind
    return None


def template_item(name, icon, category, minutes, interval_days, category_icon="", **extra):
    item = {"name": name, "icon": icon, "category": category, "category_icon": category_icon, "minutes": minutes,
            "interval_days": interval_days, "sensor": None, "action": None, "area_id": None, "reason": ""}
    item.update(extra)
    return item


def detect_devices(states, ctx):
    """Vorschläge mit Sensor-Regel und Aktion für Geräte, die in HA erkennbar sind."""
    ents = ctx["entities"]
    by_device = {}
    for e, info in ents.items():
        if info["device"]:
            by_device.setdefault(info["device"], []).append(e)
    attr = lambda e, k: (states.get(e) or {}).get("attributes", {}).get(k)  # noqa: E731
    fname = lambda e: attr(e, "friendly_name") or e  # noqa: E731
    dev_name = lambda e: (ents.get(e) or {}).get("device_name") or fname(e)  # noqa: E731
    area = lambda e: (ents.get(e) or {}).get("area")  # noqa: E731
    remaining = r"verbleib|remaining|_left|lebensdauer|_life"
    items = []

    vacuum_devices = set()
    for e in sorted(x for x in states if x.startswith("vacuum.")):
        dev = (ents.get(e) or {}).get("device")
        vacuum_devices.add(dev)
        name = dev_name(e)
        group = by_device.get(dev, [])
        cons = sorted(x for x in group if x.startswith("sensor.") and re.search(remaining, x)
                      and attr(x, "unit_of_measurement") in ("h", "min", "%"))
        resets = sorted(x for x in group if x.startswith("button.") and re.search(r"reset|zuruck", x))
        first = slugify(name).split("_")[0]
        if cons:
            hours = any(attr(x, "unit_of_measurement") != "%" for x in cons)
            items.append(template_item(
                f"{name} warten", "🔧", "Putzen", 15, None, "🧽",
                sensor={"mode": "below", "entities": cons, "threshold": 0 if hours else 5},
                action={"service": "button.press", "entities": resets} if resets else None,
                match_words=[first, "wart"], area_id=area(e),
                reason=f"Fällig, sobald ein Verschleißteil abgelaufen ist ({len(cons)} Zähler)"
                       + (", Abhaken setzt die Zähler zurück" if resets
                          else ". Tipp: Reset-Knöpfe der Integration aktivieren und als Aktion ergänzen")))
        bins = sorted(x for x in group if x.startswith("binary_sensor.")
                      and re.search(r"bin|behalter|behaelter|dust|staub|_full|_voll", x))
        items.append(template_item(f"{name} leeren", "🤖", "Putzen", 3, 7, "🧽", match_words=[first, "leeren"], area_id=area(e),
                                   sensor={"mode": "above", "entities": bins[:1], "threshold": 1} if bins else None,
                                   reason=f"Wöchentlich oder sobald „{fname(bins[0])}“ meldet" if bins
                                          else "Behälter des Saugroboters, wöchentlich"))

    flags = [e for e in states if e.split(".")[0] in ("input_boolean", "binary_sensor")
             and re.search(r"wartet|fertig|finished|done|complete|ausraum|leeren", e)]
    for pattern, exclude, name, icon, category, minutes in (
            (r"geschirr|spul|dishwash", None, "Geschirrspüler ausräumen", "🫧", "Küche", 12),
            (r"wasche|waesche|wasch|laundry|washer|washing", r"geschirr|spul|dish", "Wäsche aufhängen", "👕", "Wäsche", 15),
            (r"trockner|dryer", None, "Trockner ausräumen", "🌬️", "Wäsche", 10)):
        found = [e for e in flags if re.search(pattern, e) and not (exclude and re.search(exclude, e))]
        if found:
            items.append(template_item(name, icon, category, minutes, None,
                                       sensor={"mode": "above", "entities": found[:1], "threshold": 1},
                                       reason=f"Fällig, sobald „{fname(found[0])}“ an ist"))

    for e in sorted(states):
        dev = (ents.get(e) or {}).get("device")
        if e.startswith("sensor.") and "filter" in e and re.search(remaining, e) and dev not in vacuum_devices:
            unit = attr(e, "unit_of_measurement")
            resets = sorted(x for x in by_device.get(dev, []) if x.startswith("button.") and "filter" in x
                            and re.search(r"reset|zuruck", x))
            items.append(template_item(
                f"{dev_name(e)}: Filter wechseln", "🌬️", "Haus & Verwaltung", 10, None, "🧰",
                sensor={"mode": "below", "entities": [e], "threshold": 5 if unit == "%" else 0},
                action={"service": "button.press", "entities": resets} if resets else None, area_id=area(e),
                reason="Fällig, sobald die Filter-Restlaufzeit abgelaufen ist"))

    batteries, seen = [], set()
    for e in sorted(states, key=lambda x: (HomeAssistant.value(states[x]) is None, x)):
        if not e.startswith("sensor.") or attr(e, "device_class") != "battery":
            continue
        dev = (ents.get(e) or {}).get("device") or e
        group = " ".join(by_device.get(dev, [e]))
        if dev in seen or dev in vacuum_devices or re.search(NOT_REPLACEABLE, slugify(dev_name(e)) + " " + e) \
                or re.search(r"charging|battery_state|ladestatus", group):
            continue
        seen.add(dev)
        batteries.append(e)
    if batteries:
        names = sorted({dev_name(e) for e in batteries})
        items.append(template_item(
            "Batterien wechseln", "🔋", "Haus & Verwaltung", 10, None, "🧰",
            sensor={"mode": "below", "entities": batteries, "threshold": 15}, match_words=["batterie"],
            reason=f"Fällig, sobald eins von {len(batteries)} Geräten unter 15 % fällt: " + ", ".join(names[:6])
                   + (" …" if len(names) > 6 else "")))

    moisture = sorted(e for e in states if e.startswith("sensor.") and attr(e, "device_class") == "moisture")
    if moisture:
        items.append(template_item("Pflanzen gießen", "🪴", "Pflanzen & Ordnung", 10, None, "🪴",
                                   sensor={"mode": "below", "entities": moisture, "threshold": 20},
                                   reason=f"Fällig, sobald die Erde an einem von {len(moisture)} Sensoren unter 20 % ist"))
    if any(attr(e, "device_class") == "smoke" for e in states):
        items.append(template_item("Rauchmelder testen", "🚨", "Haus & Verwaltung", 10, 365, "🧰",
                                   reason="Rauchmelder in Home Assistant gefunden"))

    seen_names = set()
    metered = lambda e: any(attr(x, "unit_of_measurement") in ("W", "kWh")  # noqa: E731 – nur echte Steckdosen
                            for x in by_device.get((ents.get(e) or {}).get("device"), []))
    for e in sorted(x for x in states if x.startswith("switch.") and metered(x)):
        name = dev_name(e)  # ein Vorschlag pro Steckdose, auch wenn sie mehrere Schalter hat (z. B. Kindersicherung)
        key = slugify(name)
        for pattern, icon, interval, minutes in ((r"kaffee|espresso|siebtr|moccamaster|jura|delonghi|coffee", "☕", 60, 20),
                                                 (r"wasserkocher|kettle", "🫖", 30, 10),
                                                 (r"boiler|warmwasser", "🚿", 365, 30)):
            if re.search(pattern, key) and key not in seen_names:
                seen_names.add(key)
                items.append(template_item(f"{name} entkalken", icon, "Küche", minutes, interval, "🍳",
                                           match_words=[key.split("_")[0], "entkalk"], area_id=area(e),
                                           reason=f"Steckdose „{name}“ gefunden"))
    return items


def match_existing(item, tasks):
    active = [t for t in tasks if t["active"]]
    found = next((t for t in active if t["name"].lower() == item["name"].lower()), None)
    ents = set((item.get("sensor") or {}).get("entities", [])) | set((item.get("action") or {}).get("entities", []))
    if not found and ents:
        found = next((t for t in active if ents & (set((t["sensor"] or {}).get("entities", []))
                                                    | set((t["action"] or {}).get("entities", [])))), None)
    if not found and item.get("match_words"):
        found = next((t for t in active if all(w in slugify(t["name"]) for w in item["match_words"])), None)
    return found


def mark(item, tasks, fields):
    """Status: new (anlegen), update (vorhandene Aufgabe ergänzen) oder exists."""
    t = match_existing(item, tasks)
    item["existing_id"], item["existing_name"] = (t["id"], t["name"]) if t else (None, None)
    if not t:
        item["status"] = "new"
        return item
    item["update"] = {k: item[k] for k in fields if item.get(k) and not t.get(k)}
    item["status"] = "update" if item["update"] else "exists"
    return item


def build_templates(tasks, states, ctx):
    data = load_templates()
    catalog = [{"name": c["name"], "icon": c["icon"], "items": [
        mark(template_item(n, i, c["name"], m, d, c["icon"], kinds=k), tasks, ()) for n, i, m, d, k in c["tasks"]]}
        for c in data["categories"]]
    rooms = []
    for area_id, area_name in sorted(ctx["areas"].items(), key=lambda a: a[1]):
        kind = room_kind(area_name, data["rooms"])
        items = [mark(template_item(n, i, c["name"], m, d, c["icon"], area_id=area_id, area_name=area_name),
                      tasks, ("area_id",))
                 for c in data["categories"] for n, i, m, d, k in c["tasks"] if kind in k] if kind else []
        if items:
            rooms.append({"area_id": area_id, "name": area_name, "items": items})
    devices = [mark(dict(i, area_name=ctx["areas"].get(i["area_id"])), tasks, ("sensor", "action", "area_id"))
               for i in detect_devices(states, ctx)]
    return {"devices": devices, "rooms": rooms, "catalog": catalog}


def apply_templates(store, items, actor):
    created = updated = 0
    for item in items:
        if item.get("existing_id"):
            fields = {k: v for k, v in (item.get("update") or {}).items() if k in ("sensor", "action", "area_id")}
            if fields:
                store.save_task(int(item["existing_id"]), fields, actor)
                updated += 1
            continue
        body = {k: item.get(k) for k in ("name", "icon", "minutes", "interval_days", "sensor", "action", "area_id")}
        body["category_id"] = store.category_for(item["category"], item.get("category_icon") or "", actor) \
            if item.get("category") else None
        store.save_task(None, body, actor)
        created += 1
    return {"created": created, "updated": updated}


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "Haushalt"
    store: Store = None
    ha: HomeAssistant = None

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    # ---- Hilfen

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except ValueError:
            raise ApiError(400, "Ungültiges JSON")

    def user(self):
        """HA-Benutzer aus den Ingress-Headern (im Dev-Modus aus HAUSHALT_DEV_USER)."""
        uid = self.headers.get("X-Remote-User-Id")
        name = self.headers.get("X-Remote-User-Display-Name") or self.headers.get("X-Remote-User-Name") or ""
        if not uid and DEV:
            uid = os.environ.get("HAUSHALT_DEV_USER", "dev-user")
            name = os.environ.get("HAUSHALT_DEV_NAME", "Dev")
        if not uid:
            return None, None
        return uid, self.store.ensure_person(uid, name or "Unbekannt")

    def allowed(self):
        ip = self.client_address[0]
        return DEV or ip == INGRESS_IP or ip == CORE_IP

    def static(self, path):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        full = os.path.realpath(os.path.join(STATIC, rel))
        if not full.startswith(os.path.realpath(STATIC)) or not os.path.isfile(full):
            self.send_error(404)
            return
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", (mimetypes.guess_type(full)[0] or "application/octet-stream")
                         + ("; charset=utf-8" if full.endswith((".html", ".js", ".css")) else ""))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---- Routing

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PATCH(self):
        self.dispatch("PATCH")

    def do_DELETE(self):
        self.dispatch("DELETE")

    def dispatch(self, method):
        if not self.allowed():
            self.send_error(403)
            return
        url = urlparse(self.path)
        path = re.sub(r"/+", "/", url.path)
        try:
            if not path.startswith("/api/"):
                if method != "GET":
                    raise ApiError(405, "Methode nicht erlaubt")
                return self.static(path)
            result = self.api(method, path[5:].strip("/").split("/"), parse_qs(url.query))
            self.send_json(result)
        except ApiError as e:
            self.send_json({"error": e.message}, e.status)
        except (ValueError, KeyError, TypeError) as e:
            self.send_json({"error": f"Ungültige Eingabe: {e}"}, 400)
        except Exception as e:  # noqa: BLE001
            log.exception("Fehler bei %s %s", method, path)
            self.send_json({"error": str(e)}, 500)

    def api(self, method, parts, query):
        s = self.store
        uid, me = self.user()
        actor = me["name"] if me else "Home Assistant"
        head, rest = parts[0], parts[1:]
        rid = int(rest[0]) if rest and rest[0].isdigit() else None

        if head == "state" and method == "GET":
            return {"me": me, "persons": s.persons(), "categories": s.categories(),
                    "tasks": self.ha.annotate(s.tasks()), "reaction_set": list(REACTIONS), "settings": s.settings(),
                    "ha": self.ha.enabled or bool(os.environ.get("HAUSHALT_DEV_STATES")), "areas": self.ha.areas()}

        if head == "templates":
            if method == "GET" and not rest:
                return build_templates(s.tasks(), self.ha.states(), self.ha.context())
            if method == "POST" and rest == ["apply"]:
                result = apply_templates(s, self.body().get("items") or [], actor)
                self.after_change()
                return result

        if head == "settings" and method == "PATCH":
            s.save_settings(self.body(), actor)
            return s.settings()

        if head == "ha" and rest == ["catalog"] and method == "GET":
            return self.ha.catalog()

        if head == "completions":
            if method == "GET":
                one = lambda k: query.get(k, [None])[0]  # noqa: E731
                return s.completions(since=one("since"), until=one("until"), include_deleted=one("deleted") == "1",
                                     task_id=one("task"), limit=one("limit"))
            if method == "POST" and rid is None:
                data = self.body()
                data = self.resolve_names(data)
                if not data.get("person_ids"):
                    if not me:
                        raise ApiError(400, "person_ids oder person angeben")
                    data["person_ids"] = [me["id"]]
                task = next((t for t in s.tasks() if t["id"] == int(data.get("task_id") or 0)), None)
                data["sensor_base"] = self.ha.sensor_base(task)
                result = s.create_completion(data, actor)
                self.after_change(result)
                return result
            if method == "POST" and rid and rest[1:] == ["reactions"]:
                if not me:
                    raise ApiError(400, "Reaktionen nur für angemeldete Personen")
                emoji = self.body().get("emoji")
                added, result = s.toggle_reaction(rid, me, emoji)
                if added:
                    self.after_reaction(me, emoji, result)
                return result
            if method == "POST" and rid and rest[1:] == ["restore"]:
                result = s.restore_completion(rid, actor)
            elif method == "PATCH" and rid:
                result = s.update_completion(rid, self.body(), actor)
            elif method == "DELETE" and rid:
                result = s.delete_completion(rid, actor)
            else:
                raise ApiError(404, "Unbekannter Pfad")
            self.after_change()
            return result

        if head == "tasks":
            if method in ("POST", "PATCH"):
                body = self.body()
                sensor = body.get("sensor")
                if sensor and sensor.get("mode") == "increase":  # Startwert, falls noch nie abgehakt wurde
                    sensor["base"] = self.ha.sensor_base({"sensor": sensor}, keep=sensor.get("base")) or {}
            if method == "POST" and rid is None:
                result = {"id": s.save_task(None, body, actor)}
            elif method == "PATCH" and rid:
                result = {"id": s.save_task(rid, body, actor)}
            elif method == "DELETE" and rid:
                result = {"result": s.delete_task(rid, actor)}
            else:
                raise ApiError(404, "Unbekannter Pfad")
            self.after_change()
            return result

        if head == "categories":
            if method == "POST" and rid is None:
                return {"id": s.save_category(None, self.body(), actor)}
            if method == "PATCH" and rid:
                return {"id": s.save_category(rid, self.body(), actor)}
            if method == "DELETE" and rid:
                s.delete_category(rid, actor)
                return {"result": "deleted"}

        if head == "persons":
            if method == "POST" and rid is None:
                result = {"id": s.create_person(self.body(), actor)}
            elif method == "PATCH" and rid:
                s.update_person(rid, self.body(), actor)
                result = {"id": rid}
            elif method == "POST" and rid and rest[1:] == ["test"]:
                p = next((p for p in s.persons() if p["id"] == rid), None)
                if not p or not p["notify_service"]:
                    raise ApiError(400, "Kein Benachrichtigungsdienst eingetragen")
                if not self.ha.push(p, "🏠 Haushalt", f"Test für {p['name']}: Benachrichtigungen kommen an."):
                    raise ApiError(502, "Home Assistant hat die Nachricht nicht angenommen")
                return {"result": "sent"}
            else:
                raise ApiError(404, "Unbekannter Pfad")
            self.after_change()
            return result

        if head == "export" and method == "GET":
            return s.export()

        if head == "import" and method == "POST":
            result = s.import_(self.body(), actor)
            self.after_change()
            return result

        raise ApiError(404, "Unbekannter Pfad")

    def resolve_names(self, data):
        """Erlaubt Aufrufe aus HA (rest_command) mit Namen statt IDs: {"task": "Bad putzen", "person": "Sarah"}."""
        if data.get("task") and not data.get("task_id"):
            match = [t for t in self.store.tasks() if t["name"].lower() == str(data["task"]).lower()]
            if not match:
                raise ApiError(400, f"Aufgabe {data['task']!r} nicht gefunden")
            data["task_id"] = match[0]["id"]
        if data.get("person") and not data.get("person_ids"):
            names = data["person"] if isinstance(data["person"], list) else [data["person"]]
            persons = {p["name"].lower(): p["id"] for p in self.store.persons()}
            try:
                data["person_ids"] = [persons[str(n).lower()] for n in names]
            except KeyError as e:
                raise ApiError(400, f"Person {e.args[0]!r} nicht gefunden")
        return data

    def after_reaction(self, me, emoji, completion):
        task = next((t for t in self.store.tasks() if t["id"] == completion["task_id"]), {})
        names = {p["id"]: p["name"] for p in self.store.persons()}
        threading.Thread(target=self.ha.fire, daemon=True, args=("haushalt_reaktion", {
            "von": me["name"], "emoji": emoji, "aufgabe": task.get("name"),
            "fuer": [names.get(i) for i in completion["person_ids"]],
            "ha_user_ids": [p["ha_user_id"] for p in self.store.persons()
                            if p["id"] in completion["person_ids"] and p["ha_user_id"]],
            "zeitpunkt": completion["done_at"],
        })).start()

    def after_change(self, completion=None):
        self.ha.after_change(completion)


def main():
    logging.basicConfig(level=getattr(logging, str(OPTIONS.get("log_level", "info")).upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(message)s")
    store = Store(os.path.join(DATA_DIR, "haushalt.db"))
    ha = HomeAssistant(store)
    threading.Thread(target=ha.run, daemon=True).start()
    Handler.store, Handler.ha = store, ha
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log.info("Haushalt läuft auf Port %s%s", PORT, " (Dev-Modus)" if DEV else "")
    server.serve_forever()


if __name__ == "__main__":
    main()
