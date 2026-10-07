"""Справочник маршрутов на карте: проверка данных, запись, правки из двух процессов."""

import json
import tempfile
import unittest
from pathlib import Path

from naryad.geo.store import DB_FILE, GeoError, RouteStore

ROUTE = {"park_id": "P07", "number": "999т", "name": "тест",
         "directions": [{"line": [[59.83, 30.30], [59.84, 30.31]],
                         "stops": [{"name": "Тестовая", "lat": 59.83, "lon": 30.30}]}]}


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "routes.json"
        self.store = RouteStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_create_update_delete(self):
        r = self.store.create(ROUTE)
        self.assertEqual(r["id"], "P07-M01")
        self.assertEqual(r["directions"][0]["id"], "A")
        self.store.update(r["id"], {**ROUTE, "name": "новое"})
        self.assertEqual(RouteStore(self.path).all()["routes"][0]["name"], "новое")
        self.assertTrue(self.path.with_suffix(".json.bak").exists())
        self.store.delete(r["id"])
        self.assertEqual(RouteStore(self.path).all()["routes"], [])
        actions = [json.loads(x)["action"] for x in (self.path.parent / "journal.jsonl").read_text().splitlines()]
        self.assertEqual(actions, ["create", "update", "delete"])

    def test_rejects_bad_data(self):
        with self.assertRaisesRegex(GeoError, "номер"):
            self.store.create({**ROUTE, "number": " "})
        with self.assertRaisesRegex(GeoError, "перепутаны"):
            self.store.create({**ROUTE, "directions": [{"line": [[30.3, 59.83]]}]})
        with self.assertRaisesRegex(GeoError, "название"):
            self.store.create({**ROUTE, "directions": [{"stops": [{"name": "", "lat": 59.8, "lon": 30.3}]}]})
        with self.assertRaisesRegex(GeoError, "цвет"):
            self.store.create({**ROUTE, "color": "red"})
        self.store.create(ROUTE)
        with self.assertRaisesRegex(GeoError, "цвета не повторяются"):
            self.store.create({**ROUTE, "number": "998т", "color": "#227B81"})
        with self.assertRaisesRegex(GeoError, "уже есть"):
            self.store.create({**ROUTE, "number": "999Т"})
        self.assertEqual(len(self.store.all()["routes"]), 1)  # отклонённое не записано

    def test_missing_route(self):
        with self.assertRaises(KeyError):
            self.store.update("P07-M99", ROUTE)
        with self.assertRaises(KeyError):
            self.store.delete("P07-M99")

    def test_sees_changes_of_other_process(self):
        other = RouteStore(self.path)
        self.store.create(ROUTE)
        self.assertEqual(len(other.all()["routes"]), 1)
        other.create({**ROUTE, "number": "998т", "color": "#be0032"})  # не затирает чужую запись
        self.assertEqual(len(self.store.all()["routes"]), 2)


class Park7DataTest(unittest.TestCase):
    def test_park7_routes_match_registry(self):
        from naryad.web.dispatcher import registry
        data = RouteStore(DB_FILE).all()
        numbers = {r["number"].lower() for r in data["routes"] if r["park_id"] == "P07"}
        day = {r["number"].lower() for r in registry("P07", "2026-10-07")["routes"]}
        self.assertEqual(numbers, day)
        for r in data["routes"]:
            RouteStore(Path(tempfile.gettempdir()) / "unused.json").validate(r, r["id"])
        colors = [r["color"].lower() for r in data["routes"] if r["park_id"] == "P07"]
        self.assertEqual(len(colors), len(set(colors)), "цвета маршрутов парка повторяются")


if __name__ == "__main__":
    unittest.main()
