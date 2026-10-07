"""
Справочник маршрутов на карте - редактируемая база в одном JSON-файле data/geo/routes.json.

Маршрут:
    {"id": "P07-R01", "park_id": "P07", "number": "3", "name": "улица Костюшко - ...",
     "color": "#227b81", "source": "osm:958135,1721705" | "вручную", "note": "", "updated_at": "...",
     "directions": [{"id": "A", "name": "улица Костюшко → ...",
                     "line": [[59.83, 30.30], ...],
                     "stops": [{"id": "osm-123", "name": "...", "lat": 59.83, "lon": 30.30}, ...]}]}

Связь с днём: маршрут дня с тем же park_id и номером (без учёта регистра).
Запись атомарная (через временный файл), прошлая версия остаётся в routes.json.bak,
каждое изменение - строкой в data/geo/journal.jsonl.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from datetime import datetime, timezone
from pathlib import Path

DB_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "geo" / "routes.json"
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
# Ленинградская область с запасом: точка вне - почти всегда перепутаны широта и долгота.
LAT, LON = (58.4, 61.4), (27.5, 35.8)
MAX_POINTS, MAX_STOPS = 20000, 500


class GeoError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _point(p, where: str) -> list:
    if not isinstance(p, (list, tuple)) or len(p) != 2:
        raise GeoError(f"{where}: точка - пара [широта, долгота]")
    try:
        lat, lon = float(p[0]), float(p[1])
    except (TypeError, ValueError):
        raise GeoError(f"{where}: широта и долгота - числа") from None
    if not (LAT[0] <= lat <= LAT[1] and LON[0] <= lon <= LON[1]):
        raise GeoError(f"{where}: точка {lat}, {lon} вне Петербурга и области - не перепутаны ли широта и долгота?")
    return [round(lat, 6), round(lon, 6)]


class RouteStore:
    def __init__(self, path: Path = DB_FILE):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.data = {"routes": [], "parks": {}}
        self._mtime = None
        self._refresh()

    def _refresh(self):
        """Перечитать файл, если его изменил другой процесс (два сервера на одном справочнике)."""
        if not self.path.exists():
            return
        mtime = self.path.stat().st_mtime_ns
        if mtime != self._mtime:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            self.data.setdefault("routes", [])
            self.data.setdefault("parks", {})
            self._mtime = mtime

    # --- проверка ---------------------------------------------------------------
    def validate(self, r: dict, route_id: str | None = None) -> dict:
        if not isinstance(r, dict):
            raise GeoError("маршрут - объект JSON")
        number = str(r.get("number") or "").strip()
        if not number:
            raise GeoError("нужен номер маршрута")
        park = str(r.get("park_id") or "").strip()
        if not re.fullmatch(r"P\d{2}", park):
            raise GeoError("park_id - номер парка вида P07")
        color = str(r.get("color") or "#227b81")
        if not COLOR.match(color):
            raise GeoError("цвет - в виде #RRGGBB")
        dirs = r.get("directions") or []
        if not isinstance(dirs, list) or len(dirs) > 8:
            raise GeoError("направлений - от 0 до 8")
        out_dirs, points = [], 0
        for i, d in enumerate(dirs):
            if not isinstance(d, dict):
                raise GeoError(f"направление {i + 1}: объект")
            line = [_point(p, f"направление {i + 1}, точка трассы {k + 1}") for k, p in enumerate(d.get("line") or [])]
            stops = []
            for k, s in enumerate(d.get("stops") or []):
                if not isinstance(s, dict):
                    raise GeoError(f"направление {i + 1}, остановка {k + 1}: объект")
                name = str(s.get("name") or "").strip()
                if not name:
                    raise GeoError(f"направление {i + 1}, остановка {k + 1}: нужно название")
                lat, lon = _point([s.get("lat"), s.get("lon")], f"направление {i + 1}, остановка «{name}»")
                stops.append({"id": str(s.get("id") or f"m-{i}-{k}-{lat}-{lon}"), "name": name[:120], "lat": lat, "lon": lon})
            if len(stops) > MAX_STOPS:
                raise GeoError(f"направление {i + 1}: больше {MAX_STOPS} остановок")
            points += len(line)
            out_dirs.append({"id": str(d.get("id") or "ABCDEFGH"[i])[:8], "name": str(d.get("name") or "").strip()[:200],
                             "line": line, "stops": stops})
        if points > MAX_POINTS:
            raise GeoError(f"в трассе больше {MAX_POINTS} точек - упростите")
        rid = route_id or str(r.get("id") or "").strip()
        if not rid:
            rid = self._new_id(park)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", rid):
            raise GeoError("id маршрута: латиница, цифры, - и _")
        return {"id": rid, "park_id": park, "number": number[:20], "name": str(r.get("name") or "").strip()[:200],
                "color": color, "directions": out_dirs, "source": str(r.get("source") or "вручную")[:200],
                "note": str(r.get("note") or "").strip()[:1000], "updated_at": _now()}

    def _new_id(self, park: str) -> str:
        used = {r["id"] for r in self.data["routes"]}
        n = 1
        while f"{park}-M{n:02d}" in used:
            n += 1
        return f"{park}-M{n:02d}"

    # --- чтение и запись --------------------------------------------------------
    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            shutil.copyfile(self.path, self.path.with_suffix(".json.bak"))
        tmp = self.path.with_suffix(".json.tmp")
        body = {"_about": "Справочник маршрутов на карте: трассы и остановки. Правится в редакторе /routes "
                          "сервера диспетчера (python -m naryad.web.dispatcher). Первичные данные - "
                          "OpenStreetMap (ODbL, © участники OpenStreetMap).",
                "parks": self.data.get("parks", {}), "routes": self.data["routes"]}
        tmp.write_text(json.dumps(body, ensure_ascii=False, separators=(",", ":")).replace('{"id"', '\n{"id"'),
                       encoding="utf-8")
        os.replace(tmp, self.path)
        self._mtime = self.path.stat().st_mtime_ns

    def _journal(self, action: str, route: dict):
        line = {"at": _now(), "action": action, "id": route["id"], "number": route["number"]}
        with open(self.path.parent / "journal.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    def all(self) -> dict:
        with self.lock:
            self._refresh()
            return {"parks": self.data.get("parks", {}), "routes": self.data["routes"]}

    def create(self, body: dict) -> dict:
        with self.lock:
            self._refresh()
            r = self.validate({**body, "id": None})
            self._unique(r)
            self.data["routes"].append(r)
            self.save()
            self._journal("create", r)
            return r

    def update(self, rid: str, body: dict) -> dict:
        with self.lock:
            self._refresh()
            i = self._index(rid)
            r = self.validate(body, rid)
            self._unique(r, skip=rid)
            self.data["routes"][i] = r
            self.save()
            self._journal("update", r)
            return r

    def delete(self, rid: str) -> dict:
        with self.lock:
            self._refresh()
            i = self._index(rid)
            r = self.data["routes"].pop(i)
            self.save()
            self._journal("delete", r)
            return {"deleted": rid}

    def _index(self, rid: str) -> int:
        for i, r in enumerate(self.data["routes"]):
            if r["id"] == rid:
                return i
        raise KeyError(rid)

    def _unique(self, r: dict, skip: str | None = None):
        for x in self.data["routes"]:
            if x["id"] != skip and x["park_id"] == r["park_id"] and x["number"].lower() == r["number"].lower():
                raise GeoError(f"в парке {r['park_id']} уже есть маршрут {r['number']}")
            if x["id"] != skip and x["park_id"] == r["park_id"] and x["color"].lower() == r["color"].lower():
                raise GeoError(f"цвет {r['color']} уже у маршрута {x['number']} - у маршрутов парка цвета не повторяются")
