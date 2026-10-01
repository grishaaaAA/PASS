"""Проверки генератора, проверки данных и CSV. Запуск: python -m unittest"""

import copy
import tempfile
import unittest
from pathlib import Path

from naryad.data.check import check
from naryad.data.csvio import read_csv, write_csv
from naryad.data.generate import generate, parse_hhmm

WEEKDAY = "2026-10-05"  # понедельник
WEEKEND = "2026-10-04"  # воскресенье


def count(items, **match):
    return sum(all(item[k] == v for k, v in match.items()) for item in items)


class TestPark7(unittest.TestCase):
    """Цифры генератора совпадают со справкой парка №7."""

    @classmethod
    def setUpClass(cls):
        cls.weekday = generate("park7", WEEKDAY, seed=1)
        cls.weekend = generate("park7", WEEKEND, seed=1)

    def test_fleet(self):
        vehicles = self.weekday["vehicles"]
        self.assertEqual(len(vehicles), 420)
        self.assertEqual(count(vehicles, **{"class": "big"}), 258)
        self.assertEqual(count(vehicles, **{"class": "extra_big"}), 162)
        self.assertEqual(count(vehicles, fuel="gas"), 144)
        self.assertEqual(count(vehicles, fuel="diesel"), 276)
        self.assertEqual(count(vehicles, condition="ok"), 375)  # 420 x 0,893

    def test_release_weekday(self):
        duties = self.weekday["duties"]
        self.assertEqual(count(duties, type="line"), 326)
        self.assertEqual(count(duties, type="reserve"), 24)
        self.assertEqual([count(duties, shift_count=k) for k in (1, 2, 3)], [107, 119, 124])

    def test_release_weekend(self):
        duties = self.weekend["duties"]
        self.assertEqual(count(duties, type="line"), 247)
        self.assertEqual(count(duties, type="reserve"), 19)
        self.assertEqual([count(duties, shift_count=k) for k in (1, 2, 3)], [90, 88, 88])

    def test_clean(self):
        for data in (self.weekday, self.weekend):
            report = check(data)
            self.assertEqual(report["errors"], [])
            self.assertEqual(report["warnings"], [])

    def test_enough_healthy_per_class(self):
        for data in (self.weekday, self.weekend):
            for cls in ("big", "extra_big"):
                have = count(data["vehicles"], condition="ok", **{"class": cls})
                need = count(data["duties"], vehicle_class=cls)
                self.assertGreaterEqual(have, need, cls)

    def test_shifts_inside_duty(self):
        duties = {d["id"]: d for d in self.weekday["duties"]}
        for shift in self.weekday["shifts"]:
            duty = duties[shift["duty_id"]]
            self.assertLessEqual(parse_hhmm(duty["start"]), parse_hhmm(shift["start"]))
            self.assertLessEqual(parse_hhmm(shift["end"]), parse_hhmm(duty["end"]))

    def test_moment(self):
        plan = self.weekday["drivers"]
        self.assertTrue(all(d["medical"] == "pending" for d in plan if d["schedule"] == "work"))
        self.assertTrue(all(d["medical"] is None for d in plan if d["schedule"] == "day_off"))
        morning = generate("park7", WEEKDAY, seed=1, moment="morning")["drivers"]
        self.assertGreater(count(morning, medical="passed"), 0)


class TestSeed(unittest.TestCase):
    def test_same_seed_same_data(self):
        self.assertEqual(generate(seed=7), generate(seed=7))

    def test_other_seed_other_data(self):
        self.assertNotEqual(generate(seed=7)["vehicles"], generate(seed=8)["vehicles"])


class TestCheckCatches(unittest.TestCase):
    """Испорченные данные ловятся с понятным текстом."""

    def setUp(self):
        self.data = generate(seed=1)

    def errors(self):
        return " ".join(check(self.data)["errors"])

    def test_list_count_mismatch(self):
        self.data["vehicles"].pop()
        self.assertIn("по документам 420", self.errors())

    def test_duplicate_id(self):
        self.data["drivers"].append(copy.deepcopy(self.data["drivers"][0]))
        self.assertIn("повторяется", self.errors())

    def test_unknown_condition(self):
        self.data["vehicles"][0]["condition"] = "сломан"
        self.assertIn("condition", self.errors())

    def test_reserve_with_route(self):
        reserve = next(d for d in self.data["duties"] if d["type"] == "reserve")
        reserve["route_id"] = self.data["routes"][0]["id"]
        self.assertIn("резервного наряда", self.errors())

    def test_wrong_class_on_route(self):
        duty = next(d for d in self.data["duties"]
                    if d["type"] == "line" and d["vehicle_class"] == "big")
        duty["vehicle_class"] = "extra_big"
        self.assertIn("не допущен на маршрут", self.errors())

    def test_shift_gap(self):
        shift = next(s for s in self.data["shifts"] if s["order"] == 2)
        shift["start"] = "23:59"
        self.assertIn("встык", self.errors())

    def test_driver_without_permit(self):
        articulated = {v["id"] for v in self.data["vehicles"] if v["class"] == "extra_big"}
        driver = next(d for d in self.data["drivers"] if d["home_vehicle_id"] in articulated)
        driver["classes"] = ["medium", "big"]
        self.assertIn("нет допуска", self.errors())

    def test_deficit_is_warning(self):
        for vehicle in self.data["vehicles"]:
            if vehicle["class"] == "extra_big":
                vehicle["condition"] = "repair"
        report = check(self.data)
        self.assertEqual(report["errors"], [])
        self.assertTrue(any("не хватает" in w for w in report["warnings"]))


class TestCsv(unittest.TestCase):
    def test_round_trip(self):
        data = generate(seed=3)
        with tempfile.TemporaryDirectory() as folder:
            write_csv(data, folder)
            back = read_csv(folder)
        self.assertEqual(back, data)
        self.assertEqual(check(back)["errors"], [])

    def test_bad_number(self):
        data = generate(seed=3)
        with tempfile.TemporaryDirectory() as folder:
            write_csv(data, folder)
            path = Path(folder) / "parks.csv"
            text = path.read_text(encoding="utf-8-sig").replace(";420;", ";много;")
            path.write_text(text, encoding="utf-8-sig")
            with self.assertRaisesRegex(ValueError, "не число"):
                read_csv(folder)


if __name__ == "__main__":
    unittest.main()
