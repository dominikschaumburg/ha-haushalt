#!/usr/bin/env python3
"""Haushalt – Home-Assistant-App zum Erfassen und Auswerten von Hausarbeit.

Bewusst nur Standardbibliothek: HTTP-Server, SQLite, urllib für die HA-API.
"""
import json
import logging
import mimetypes
import os
import re
import sqlite3
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
# 172.30.32.2 = Ingress-Proxy des Supervisors, 172.30.32.1 = Home Assistant Core (rest_command)
INGRESS_IP = "172.30.32.2"
CORE_IP = "172.30.32.1"

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
  created_at TEXT NOT NULL
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
        if self.db.execute("PRAGMA user_version").fetchone()[0] == 0:
            self._seed()
            self.db.execute("PRAGMA user_version = 1")

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
        fields = {k: data[k] for k in ("name", "color", "active") if k in data}
        if "name" in fields and not str(fields["name"]).strip():
            raise ApiError(400, "Name fehlt")
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
                "SELECT c.id, c.task_id, c.done_at FROM completions c"
                " JOIN (SELECT task_id, MAX(done_at) m FROM completions WHERE deleted_at IS NULL GROUP BY task_id) l"
                " ON l.task_id = c.task_id AND l.m = c.done_at WHERE c.deleted_at IS NULL"):
            last[row["task_id"]] = row
        persons = self._persons_for([r["id"] for r in last.values()])
        counts = {r["task_id"]: r["n"] for r in self.q(
            "SELECT task_id, COUNT(*) n FROM completions WHERE deleted_at IS NULL GROUP BY task_id")}
        for t in tasks:
            l = last.get(t["id"])
            t["last_done"] = l["done_at"] if l else None
            t["last_persons"] = persons.get(l["id"], []) if l else []
            t["count"] = counts.get(t["id"], 0)
        return tasks

    def save_task(self, tid, data, actor):
        fields = {k: data[k] for k in ("name", "icon", "category_id", "minutes", "interval_days", "active", "sort")
                  if k in data}
        if "name" in fields:
            fields["name"] = str(fields["name"]).strip()
            if not fields["name"]:
                raise ApiError(400, "Name fehlt")
        if "minutes" in fields:
            fields["minutes"] = max(0, int(fields["minutes"] or 0))
        if "interval_days" in fields:
            fields["interval_days"] = int(fields["interval_days"]) if fields["interval_days"] else None
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
        persons = self._persons_for([r["id"] for r in rows])
        for r in rows:
            r["person_ids"] = persons.get(r["id"], [])
        return rows

    def completion(self, cid):
        rows = self.q("SELECT * FROM completions WHERE id = ?", (cid,))
        if not rows:
            raise ApiError(404, "Eintrag nicht gefunden")
        rows[0]["person_ids"] = self._persons_for([cid]).get(cid, [])
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
        minutes = float(data["minutes"]) if data.get("minutes") not in (None, "") else float(task["minutes"])
        with self.lock:
            cur = self.x("INSERT INTO completions (task_id, done_at, minutes, note, created_at, created_by)"
                         " VALUES (?, ?, ?, ?, ?, ?)",
                         (task["id"], done_at, max(0.0, minutes), str(data.get("note") or ""), now_iso(), actor))
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

    def export(self):
        return {
            "exported_at": now_iso(),
            "persons": self.persons(),
            "categories": self.categories(),
            "tasks": self.q("SELECT * FROM tasks ORDER BY id"),
            "completions": self.completions(include_deleted=True),
        }


# ---------------------------------------------------------------- Home Assistant

class HomeAssistant:
    """Gleicht Personen mit HA ab und veröffentlicht Sensoren und Ereignisse."""

    def __init__(self, store):
        self.store = store
        self.wake = threading.Event()
        self.enabled = bool(SUPERVISOR_TOKEN)
        self.sensors = OPTIONS.get("sensors", True)

    def call(self, method, path, body=None):
        req = urllib.request.Request(
            "http://supervisor/core/api" + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {SUPERVISOR_TOKEN}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
        return json.loads(raw) if raw else None

    def sync_persons(self):
        for state in self.call("GET", "/states") or []:
            if state["entity_id"].startswith("person.") and state["attributes"].get("user_id"):
                attrs = state["attributes"]
                self.store.ensure_person(attrs["user_id"], attrs.get("friendly_name") or state["entity_id"][7:])

    def fire(self, event, data):
        if not self.enabled:
            return
        try:
            self.call("POST", f"/events/{event}", data)
        except Exception as e:  # noqa: BLE001 – HA nicht erreichbar darf die App nicht stören
            log.warning("Ereignis %s fehlgeschlagen: %s", event, e)

    def publish(self):
        local_today = datetime.now().astimezone().date()
        due = []
        for t in self.store.tasks():
            if not t["active"] or not t["interval_days"]:
                continue
            if not t["last_done"]:  # Rhythmus beginnt erst mit dem ersten Abhaken
                continue
            last = datetime.fromisoformat(t["last_done"].replace("Z", "+00:00")).astimezone().date()
            next_due = last + timedelta(days=t["interval_days"])
            if next_due <= local_today:
                due.append({"name": t["name"], "since_days": (local_today - next_due).days})
        due.sort(key=lambda d: -d["since_days"])
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
        last_sync = 0
        while True:
            try:
                if time.time() - last_sync > 600:
                    self.sync_persons()
                    last_sync = time.time()
                if self.sensors:
                    self.publish()
            except Exception as e:  # noqa: BLE001
                log.warning("HA-Abgleich fehlgeschlagen: %s", e)
            self.wake.wait(300)
            self.wake.clear()


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
            return {"me": me, "persons": s.persons(), "categories": s.categories(), "tasks": s.tasks()}

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
                result = s.create_completion(data, actor)
                self.after_change(result)
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
            if method == "POST" and rid is None:
                result = {"id": s.save_task(None, self.body(), actor)}
            elif method == "PATCH" and rid:
                result = {"id": s.save_task(rid, self.body(), actor)}
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
            else:
                raise ApiError(404, "Unbekannter Pfad")
            self.after_change()
            return result

        if head == "export" and method == "GET":
            return s.export()

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

    def after_change(self, completion=None):
        self.ha.wake.set()
        if completion:
            task = next((t for t in self.store.tasks() if t["id"] == completion["task_id"]), {})
            names = {p["id"]: p["name"] for p in self.store.persons()}
            threading.Thread(target=self.ha.fire, daemon=True, args=("haushalt_erledigt", {
                "aufgabe": task.get("name"), "personen": [names.get(i) for i in completion["person_ids"]],
                "minuten": completion["minutes"], "zeitpunkt": completion["done_at"],
            })).start()


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
