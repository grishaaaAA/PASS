"""
Первичное заполнение справочника маршрутов из OpenStreetMap (данные ODbL, © участники OSM).

Вход - ответы Overpass API, сохранённые файлами:
  * отношения маршрутов с геометрией (`rel[...]; out geom;`)
  * теги остановок (`node(id:...); out tags;`)
Выход - data/geo/routes.json в формате naryad.geo.store. Справочник дальше правится
в редакторе (/routes на сервере диспетчера); повторный импорт перезапишет правки,
поэтому без --force он отказывается работать поверх существующего файла.

    python -m naryad.geo.osm_import rel1.json [rel2.json ...] --tags tags.json --park P07
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone

from naryad.data.generate import generate
from naryad.geo.store import DB_FILE, RouteStore

# Палитра максимального контраста Келли без светлых цветов + 3 добавки: цвета маршрутов парка не повторяются и не сливаются
PALETTE = ["#be0032", "#0067a5", "#f38400", "#008856", "#875692", "#e25822", "#1f2a7a", "#8db600", "#b3446c",
           "#0f8c99", "#f6a600", "#604e97", "#882d17", "#e68fac", "#2b3d26", "#c51b7d", "#654522", "#f99379", "#5a8f29"]


def _norm(ref: str) -> str:
    return ref.strip().lower().replace("a", "а").replace("x", "х").replace("m", "м").replace("e", "э")


def _dist(a, b) -> float:
    """Метры между точками [lat, lon], для малых расстояний."""
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot((a[0] - b[0]) * 111_320, (a[1] - b[1]) * 111_320 * k)


def _chain(ways: list) -> list:
    """Склеить пути отношения по порядку, разворачивая те, что идут навстречу."""
    line: list = []
    for n, pts in enumerate(ways):
        if not pts:
            continue
        if not line:
            line = list(pts)
            continue
        if n == 1 or len(line) == len(ways[0]):
            # первый путь мог быть записан навстречу: разворачиваем, если так стык ближе
            if min(_dist(line[0], pts[0]), _dist(line[0], pts[-1])) < min(_dist(line[-1], pts[0]), _dist(line[-1], pts[-1])):
                line = line[::-1]
        end = line[-1]
        if _dist(end, pts[-1]) < _dist(end, pts[0]):
            pts = pts[::-1]
        line.extend(pts[1:] if _dist(line[-1], pts[0]) < 1 else pts)
    return line


def _simplify(pts: list, tol: float) -> list:
    """Дуглас-Пекер в метрах."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        a, b = pts[i], pts[j]
        best, idx = 0.0, -1
        for k in range(i + 1, j):
            d = _seg_dist(pts[k], a, b)
            if d > best:
                best, idx = d, k
        if best > tol:
            keep[idx] = True
            stack += [(i, idx), (idx, j)]
    return [p for p, k in zip(pts, keep) if k]


def _seg_dist(p, a, b) -> float:
    k = math.cos(math.radians(p[0]))
    ax, ay, bx, by, px, py = a[1] * k, a[0], b[1] * k, b[0], p[1] * k, p[0]
    dx, dy = bx - ax, by - ay
    t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot((ax + t * dx - px), (ay + t * dy - py)) * 111_320


def direction(rel: dict, tags: dict, tol: float, multi: bool = False) -> dict:
    ways, stops, seen = [], [], set()
    for m in rel["members"]:
        if m["type"] == "way" and m["role"] == "" and m.get("geometry"):
            ways.append([[round(g["lat"], 6), round(g["lon"], 6)] for g in m["geometry"] if g])
    # Остановка - точка на дороге (stop); если её нет - платформа.
    for m in rel["members"]:
        if m["type"] != "node" or not (m["role"].startswith("stop") or m["role"].startswith("platform")):
            continue
        name = (tags.get(m["ref"]) or {}).get("name") or "без названия"
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        stops.append({"id": f"osm-{m['ref']}", "name": name.replace("\xa0", " "),
                      "lat": round(m["lat"], 6), "lon": round(m["lon"], 6)})
    t = rel["tags"]
    name = (t.get("from", "") + " → " + t.get("to", "")).strip(" →") or t.get("name", "")
    if multi:
        name = t.get("ref", "") + ": " + name
    return {"name": name, "line": _simplify(_chain(ways), tol), "stops": stops, "osm_id": rel["id"]}


def build(rel_files: list, tags_file: str, park: str, tol: float) -> dict:
    tags = {e["id"]: e.get("tags", {}) for e in json.load(open(tags_file, encoding="utf-8"))["elements"]}
    rels = [e for f in rel_files for e in json.load(open(f, encoding="utf-8"))["elements"] if e["type"] == "relation"]
    preset = "park7" if park == "P07" else "case"
    day_routes = [r for r in generate(preset)["routes"] if r["park_id"] == park]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    routes, missing = [], []
    for i, r in enumerate(day_routes):
        refs = {_norm(x) for x in r["number"].split(",")}
        mine = [e for e in rels if _norm(e["tags"].get("ref", "")) in refs]
        dirs = [direction(e, tags, tol, len(refs) > 1) for e in sorted(mine, key=lambda e: (e["tags"].get("ref", ""), e["id"]))]
        if not dirs:
            missing.append(r["number"])
        routes.append({
            "id": r["id"], "park_id": park, "number": r["number"],
            "name": dirs[0]["name"].replace(" → ", " - ") if dirs else "",
            "color": PALETTE[i % len(PALETTE)],
            "directions": [{"id": "ABCDEFGH"[k], "name": d["name"], "line": d["line"], "stops": d["stops"]}
                           for k, d in enumerate(dirs)],
            "source": "osm:" + ",".join(str(d["osm_id"]) for d in dirs) if dirs else "вручную",
            "note": "" if dirs else "В OpenStreetMap не найден - нарисовать вручную",
            "updated_at": now,
        })
    print(f"маршрутов {len(routes)}, без трассы: {', '.join(missing) or 'нет'}")
    return {"routes": routes}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument("rels", nargs="+")
    p.add_argument("--tags", required=True)
    p.add_argument("--park", default="P07")
    p.add_argument("--tolerance", type=float, default=6.0, help="упрощение трассы, м")
    p.add_argument("--park-point", help="lat,lon парка (из OSM)")
    p.add_argument("--force", action="store_true")
    a = p.parse_args(argv)
    if DB_FILE.exists() and not a.force:
        print(f"{DB_FILE} уже есть - в нём могут быть ручные правки. Добавьте --force, чтобы перезаписать.")
        return 1
    data = build(a.rels, a.tags, a.park, a.tolerance)
    store = RouteStore(DB_FILE)
    store.data["routes"] = []
    for r in data["routes"]:
        store.data["routes"].append(store.validate(r))
    if a.park_point:
        lat, lon = map(float, a.park_point.split(","))
        store.data.setdefault("parks", {})[a.park] = {"lat": lat, "lon": lon, "source": "osm"}
    store.save()
    print(f"записано: {DB_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
