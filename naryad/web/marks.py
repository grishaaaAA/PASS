"""
Отметки диспетчера на день и журнал расчётов.

Отметки: что работает, какие автобусы исправны, какие водители выходят.

Хранятся в SQLite (data/marks.sqlite, модуль sqlite3 из стандартной библиотеки) по парку и дню:
на следующий день отметок нет и всё начинается с нового реестра. Каждое изменение пишется
в журнал - кто, когда, что и с какого значения на какое.

    marks   (park_id, day, kind, item_id) -> value     kind: route | veh | drv; value: 1 работает / исправен / выходит, 0 - нет
    journal (at, park_id, day, kind, item_id, value, prev, who)
    runs    (id, at, day, parks, итоги) - каждый расчёт получает порядковый номер («Расчёт № 12»)
"""

from __future__ import annotations

import re
import sqlite3
import threading
from datetime import date as Date, datetime, timezone
from pathlib import Path

DB_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "marks.sqlite"
KINDS = ("route", "veh", "drv")
ITEM = re.compile(r"^(P\d{2})-[A-Za-z0-9-]{1,40}$")


class MarksError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day(text) -> str:
    try:
        return Date.fromisoformat(str(text)).isoformat()
    except ValueError:
        raise MarksError("день - в виде ГГГГ-ММ-ДД") from None


class MarksStore:
    def __init__(self, path: Path = DB_FILE):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS marks (
                    park_id TEXT NOT NULL, day TEXT NOT NULL, kind TEXT NOT NULL, item_id TEXT NOT NULL,
                    value INTEGER NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY (park_id, day, kind, item_id));
                CREATE TABLE IF NOT EXISTS journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, park_id TEXT NOT NULL, day TEXT NOT NULL,
                    kind TEXT NOT NULL, item_id TEXT NOT NULL, value INTEGER NOT NULL, prev INTEGER, who TEXT);
                CREATE INDEX IF NOT EXISTS journal_day ON journal (park_id, day);
                CREATE TABLE IF NOT EXISTS runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, day TEXT NOT NULL, parks TEXT NOT NULL,
                    line_filled INTEGER, line_total INTEGER, shifts_filled INTEGER, shifts_total INTEGER,
                    problems INTEGER, seconds REAL, who TEXT);
            """)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA journal_mode=WAL")  # чтение не ждёт записи
        return db

    def get(self, parks: list, day) -> dict:
        """Отметки выбранных парков на день: {"route": {id: bool}, "veh": {...}, "drv": {...}}."""
        day = _day(day)
        out = {k: {} for k in KINDS}
        if not parks:
            return out
        q = "SELECT kind, item_id, value FROM marks WHERE day = ? AND park_id IN (%s)" % ",".join("?" * len(parks))
        with self.lock, self._db() as db:
            for kind, item, value in db.execute(q, [day] + list(parks)):
                out[kind][item] = bool(value)
        return out

    def set(self, day, items: list, who: str = "") -> dict:
        """Записать отметки: [{"kind", "id", "value"}]. Сначала проверяется всё, потом пишется одной транзакцией."""
        day = _day(day)
        if not isinstance(items, list) or not items:
            raise MarksError("нет отметок")
        if len(items) > 5000:
            raise MarksError("слишком много отметок за раз")
        rows = []
        for i, it in enumerate(items, 1):
            if not isinstance(it, dict):
                raise MarksError(f"отметка {i}: объект")
            kind, item = it.get("kind"), str(it.get("id") or "")
            if kind not in KINDS:
                raise MarksError(f"отметка {i}: kind - route, veh или drv")
            found = ITEM.match(item)
            if not found:
                raise MarksError(f"отметка {i}: id вида P07-...")
            if not isinstance(it.get("value"), bool):
                raise MarksError(f"отметка {i}: value - true или false")
            rows.append((found.group(1), kind, item, int(it["value"])))
        now = _now()
        with self.lock, self._db() as db:
            for park, kind, item, value in rows:
                prev = db.execute("SELECT value FROM marks WHERE park_id=? AND day=? AND kind=? AND item_id=?",
                                  (park, day, kind, item)).fetchone()
                db.execute("INSERT INTO marks VALUES (?,?,?,?,?,?) ON CONFLICT(park_id, day, kind, item_id) "
                           "DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                           (park, day, kind, item, value, now))
                db.execute("INSERT INTO journal (at, park_id, day, kind, item_id, value, prev, who) VALUES (?,?,?,?,?,?,?,?)",
                           (now, park, day, kind, item, value, prev[0] if prev else None, who[:80]))
        return {"saved": len(rows)}

    def journal(self, parks: list, day, limit: int = 200) -> list:
        day = _day(day)
        if not parks:
            return []
        q = ("SELECT at, park_id, kind, item_id, value, prev, who FROM journal WHERE day = ? AND park_id IN (%s) "
             "ORDER BY id DESC LIMIT ?" % ",".join("?" * len(parks)))
        with self.lock, self._db() as db:
            return [{"at": a, "park_id": p, "kind": k, "id": i, "value": bool(v), "prev": None if pv is None else bool(pv), "who": w}
                    for a, p, k, i, v, pv, w in db.execute(q, [day] + list(parks) + [int(limit)])]

    # --- расчёты ---------------------------------------------------------------------
    def add_run(self, day, parks: list, numbers: dict, problems: int, seconds: float, who: str = "") -> dict:
        """Записать расчёт и выдать его порядковый номер."""
        day, now = _day(day), _now()
        with self.lock, self._db() as db:
            cur = db.execute("INSERT INTO runs (at, day, parks, line_filled, line_total, shifts_filled, shifts_total, problems, seconds, who) "
                             "VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (now, day, ",".join(parks), numbers.get("line_filled"), numbers.get("line_total"), numbers.get("shifts_filled"),
                              numbers.get("shifts_total"), int(problems), float(seconds or 0), who[:80]))
            return {"id": cur.lastrowid, "at": now, "day": day, "parks": list(parks)}

    def runs(self, limit: int = 50) -> list:
        with self.lock, self._db() as db:
            return [{"id": i, "at": a, "day": d, "parks": p.split(","), "line_filled": lf, "line_total": lt, "problems": pr}
                    for i, a, d, p, lf, lt, pr in db.execute(
                        "SELECT id, at, day, parks, line_filled, line_total, problems FROM runs ORDER BY id DESC LIMIT ?", (int(limit),))]
