"""Проверки генератора, проверки данных и CSV. Запуск: python -m unittest"""

import copy
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from naryad.data.check import check
from naryad.data.city import parse_hhmm
from naryad.data.csvio import read_csv, write_csv
from naryad.data.generate import day_numbers, generate, generate_series

WEEKDAY = "2026-10-05"  # понедельник
WEEKEND = "2026-10-04"  # воскресенье


def count(items, **match):
    return sum(all(item[k] == v for k, v in match.items()) for item in items)


class TestPark7(unittest.TestCase):
    """Цифры первого дня совпадают со справкой парка №7."""

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
        self.assertEqual(count(vehicles, condition="ok"), 375)  # 420 x 0,893

    def test_release(self):
        weekday, weekend = self.weekday["duties"], self.weekend["duties"]
        self.assertEqual((count(weekday, type="line"), count(weekday, type="reserve")), (326, 24))
        self.assertEqual((count(weekend, type="line"), count(weekend, type="reserve")), (247, 19))
        self.assertEqual([count(weekday, shift_count=k) for k in (1, 2, 3)], [107, 119, 124])
        self.assertEqual([count(weekend, shift_count=k) for k in (1, 2, 3)], [90, 88, 88])

    def test_clean(self):
        for data in (self.weekday, self.weekend):
            self.assertEqual(check(data)["errors"], [])


class TestCase(unittest.TestCase):
    """Вводные заказчика: 7 парков по 270, 118 маршрутов, классы 700 / 700 / 500."""

    @classmethod
    def setUpClass(cls):
        cls.data = generate("case", WEEKDAY, seed=1)

    def test_parks(self):
        names = [p["name"] for p in self.data["parks"]]
        self.assertEqual(len(names), 7)
        self.assertIn("Колпинский автобусный парк", names)

    def test_routes_and_release(self):
        self.assertEqual(len(self.data["routes"]), 118)
        for park in self.data["parks"]:
            self.assertEqual(count(self.data["duties"], park_id=park["id"]), 270)

    def test_class_mix(self):
        need = Counter(d["vehicle_class"] for d in self.data["duties"])
        # 1 890 = 7 x 270, доли как 700 / 700 / 500 из 1 900
        self.assertEqual(sum(need.values()), 1890)
        self.assertEqual(need["extra_big"], 498)
        self.assertLessEqual(abs(need["medium"] - need["big"]), 1)

    def test_clean(self):
        report = check(self.data)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["warnings"], [])

    def test_inputs_change_city(self):
        small = generate("case", WEEKDAY, seed=1, park_count=3, release_per_park=200,
                         routes_total=40)
        self.assertEqual(len(small["parks"]), 3)
        self.assertEqual(len(small["routes"]), 40)
        self.assertEqual(len(small["duties"]), 600)
        self.assertEqual(check(small)["errors"], [])

    def test_weekend_factor(self):
        weekend = generate("case", WEEKEND, seed=1, weekend_factor=0.76)
        self.assertLess(len(weekend["duties"]), 1890 * 0.8)
        self.assertEqual(check(weekend)["errors"], [])

    def test_bad_inputs(self):
        with self.assertRaises(ValueError):
            generate("case", WEEKDAY, park_count=9)
        with self.assertRaises(ValueError):
            generate("case", WEEKDAY, routes_total=5)


class TestSeries(unittest.TestCase):
    """Постоянное остаётся, меняющееся меняется, день считается из вчерашнего."""

    @classmethod
    def setUpClass(cls):
        cls.days = list(generate_series("case", WEEKDAY, days=14, seed=2))

    def test_fixed_part_is_fixed(self):
        first, last = self.days[0], self.days[-1]
        self.assertEqual(first["routes"], last["routes"])
        strip = lambda vs: [{k: v for k, v in x.items()
                             if k not in ("condition", "repair_days_left", "schedule", "medical")}
                            for x in vs]
        self.assertEqual(strip(first["vehicles"]), strip(last["vehicles"]))
        self.assertEqual(strip(first["drivers"]), strip(last["drivers"]))

    def test_state_changes(self):
        conditions = [[v["condition"] for v in d["vehicles"]] for d in self.days]
        schedules = [[x["schedule"] for x in d["drivers"]] for d in self.days]
        self.assertNotEqual(conditions[0], conditions[1])
        self.assertNotEqual(schedules[0], schedules[1])

    def test_readiness_stays_near_target(self):
        for data in self.days:
            share = day_numbers(data)["ok"] / day_numbers(data)["vehicles"]
            self.assertAlmostEqual(share, 0.893, delta=0.02)

    def test_repair_counts_down(self):
        for today, tomorrow in zip(self.days, self.days[1:]):
            after = {v["id"]: v for v in tomorrow["vehicles"]}
            for vehicle in today["vehicles"]:
                left = vehicle["repair_days_left"]
                if vehicle["condition"] == "repair" and left and left > 1:
                    nxt = after[vehicle["id"]]
                    self.assertEqual((nxt["condition"], nxt["repair_days_left"]),
                                     ("repair", left - 1))

    def test_rota(self):
        from naryad.data.presets import DYNAMICS
        cycle = DYNAMICS["work_days"] + DYNAMICS["rest_days"]
        first = self.days[0]["drivers"]
        driver = next(i for i, d in enumerate(first) if all(
            day["drivers"][i]["schedule"] in ("work", "day_off") for day in self.days[:cycle]))
        week = [day["drivers"][driver]["schedule"] for day in self.days[:cycle]]
        self.assertEqual(week.count("day_off"), DYNAMICS["rest_days"])

    def test_park7_lengths_from_sheet(self):
        routes = {r["number"]: r for r in generate("park7", WEEKDAY)["routes"]}
        self.assertEqual(routes["26"]["length_km"], 24.38)
        self.assertEqual(routes["263"]["length_km"], 19.55)
        self.assertEqual(routes["400Э"]["length_km"], 13.78)
        self.assertIn("203", routes)
        self.assertNotIn("205", routes)

    def test_no_errors(self):
        for data in self.days:
            self.assertEqual(check(data)["errors"], [], data["meta"]["date"])

    def test_same_seed_same_days(self):
        again = list(generate_series("case", WEEKDAY, days=3, seed=2))
        self.assertEqual(again, self.days[:3])

    def test_day_does_not_depend_on_moment(self):
        plan = generate("case", WEEKDAY, seed=2, moment="plan")
        morning = self.days[0]
        self.assertEqual([v["condition"] for v in plan["vehicles"]],
                         [v["condition"] for v in morning["vehicles"]])
        self.assertTrue(all(d["medical"] == "pending" for d in plan["drivers"]
                            if d["schedule"] == "work"))
        self.assertGreater(count(morning["drivers"], medical="passed"), 0)


class TestCheckCatches(unittest.TestCase):
    """Испорченные данные ловятся с понятным текстом."""

    def setUp(self):
        self.data = generate("park7", WEEKDAY, seed=1)

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

    def test_repair_days_on_healthy(self):
        vehicle = next(v for v in self.data["vehicles"] if v["condition"] == "ok")
        vehicle["repair_days_left"] = 3
        self.assertIn("срок ремонта", self.errors())

    def test_medical_on_vacation(self):
        driver = next(d for d in self.data["drivers"] if d["schedule"] != "work")
        driver["medical"] = "passed"
        self.assertIn("не работает сегодня", self.errors())

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
                vehicle["condition"], vehicle["repair_days_left"] = "repair", 2
        report = check(self.data)
        self.assertEqual(report["errors"], [])
        self.assertTrue(any("не хватает" in w for w in report["warnings"]))

    def test_shifts_inside_duty(self):
        duties = {d["id"]: d for d in self.data["duties"]}
        for shift in self.data["shifts"]:
            duty = duties[shift["duty_id"]]
            self.assertLessEqual(parse_hhmm(duty["start"]), parse_hhmm(shift["start"]))
            self.assertLessEqual(parse_hhmm(shift["end"]), parse_hhmm(duty["end"]))


class TestCsv(unittest.TestCase):
    def test_round_trip(self):
        data = generate("park7", WEEKDAY, seed=3)
        with tempfile.TemporaryDirectory() as folder:
            write_csv(data, folder)
            back = read_csv(folder)
        self.assertEqual(back, data)
        self.assertEqual(check(back)["errors"], [])

    def test_bad_number(self):
        data = generate("park7", WEEKDAY, seed=3)
        with tempfile.TemporaryDirectory() as folder:
            write_csv(data, folder)
            path = Path(folder) / "parks.csv"
            text = path.read_text(encoding="utf-8-sig").replace(";420;", ";много;")
            path.write_text(text, encoding="utf-8-sig")
            with self.assertRaisesRegex(ValueError, "не число"):
                read_csv(folder)


class TestMetaAndSelfContradiction(unittest.TestCase):
    """Данные, на которые движок прямо опирается: meta, важность, парки.

    Эти правила добавлены после проверки агентами: день без meta проходил
    как чистый, а потом движок падал, а одна заглавная буква в day_type
    давала пустой план с надписью «Все наряды и смены закрыты».
    """

    def setUp(self):
        self.good = generate("park7", WEEKDAY, seed=1)
        self.assertEqual(check(self.good)["errors"], [])

    def spoiled(self, change):
        data = copy.deepcopy(self.good)
        change(data)
        return check(data)

    def test_meta_is_required(self):
        cases = (
            ("нет блока", lambda d: d.pop("meta"), "meta"),
            ("пустой блок", lambda d: d.update(meta={}), "date"),
            ("нет даты", lambda d: d["meta"].pop("date"), "date"),
            ("дата не та", lambda d: d["meta"].update(date="05.10.2026"), "ГГГГ-ММ-ДД"),
            ("дата числом", lambda d: d["meta"].update(date=20261005), "date"),
            ("нет типа дня", lambda d: d["meta"].pop("day_type"), "day_type"),
            ("опечатка в типе дня", lambda d: d["meta"].update(day_type="Weekday"), "day_type"),
            ("не тот момент", lambda d: d["meta"].update(moment="вечер"), "moment"),
        )
        for name, change, word in cases:
            with self.subTest(name):
                errors = self.spoiled(change)["errors"]
                self.assertTrue(errors, name)
                self.assertTrue(any(word in e for e in errors), (name, errors))

    def test_route_priority_must_be_known_to_engine(self):
        from naryad.solve.vehicles import PRIORITY_WEIGHT
        for bad in (0, 4, 9, -1):
            with self.subTest(bad):
                errors = self.spoiled(lambda d: d["routes"][0].update(priority=bad))["errors"]
                self.assertTrue(any("priority" in e for e in errors), bad)
        for good in sorted(PRIORITY_WEIGHT):  # движок знает ровно эти веса
            with self.subTest(good):
                self.assertEqual(self.spoiled(lambda d: d["routes"][0].update(priority=good))["errors"], [])

    def test_record_that_is_not_an_object(self):
        errors = self.spoiled(lambda d: d["vehicles"].insert(0, "мусор"))["errors"]
        self.assertTrue(any("не объект" in e for e in errors), errors)

    def test_day_type_without_duties(self):
        def to_weekend(data):
            for duty in data["duties"]:
                duty["day_type"] = "weekend"
        errors = self.spoiled(to_weekend)["errors"]
        self.assertTrue(any("нет ни одного наряда" in e for e in errors), errors)

    def test_park_mismatches(self):
        errors = self.spoiled(lambda d: d["drivers"][0].update(park_id="P99"))["errors"]
        self.assertTrue(errors)
        data = copy.deepcopy(self.good)
        other = {**data["parks"][0], "id": "P08", "name": "Парк №8", "list_count": 0}
        data["parks"].append(other)
        data["routes"][0]["park_id"] = "P08"
        errors = check(data)["errors"]
        self.assertTrue(any("а маршрут" in e for e in errors), errors)

    def test_rules_promised_by_the_contract(self):
        """Правила, которые docs/CONTRACT.md называет прямо, и базовые правила полей."""
        cases = (
            ("повтор номера", lambda d: d["vehicles"].append(dict(d["vehicles"][0])), "повторяется"),
            ("ссылка в никуда", lambda d: d["vehicles"][0].update(park_id="P99"), "такого нет"),
            ("ссылка маршрута в никуда", lambda d: d["duties"][0].update(route_id="нет"), "такого нет"),
            ("нет списка", lambda d: d.pop("shifts"), "нет списка shifts"),
            ("список не список", lambda d: d.update(vehicles={}), "нет списка vehicles"),
            ("нет поля", lambda d: d["vehicles"][0].pop("board_number"), "нет поля board_number"),
            ("поле пустое", lambda d: d["vehicles"][0].update(board_number=None), "пустое"),
            ("не тот тип", lambda d: d["vehicles"][0].update(board_number=7001), "должно быть str"),
            ("число строкой", lambda d: d["parks"][0].update(list_count="420"), "должно быть int"),
            ("правда вместо числа", lambda d: d["parks"][0].update(list_count=True), "должно быть int"),
        )
        for name, change, word in cases:
            with self.subTest(name):
                errors = self.spoiled(change)["errors"]
                self.assertTrue(errors, name)
                self.assertTrue(any(word in e for e in errors), (name, errors[:3]))

    def test_good_day_passes_every_rule(self):
        """Образцы в репозитории обязаны проходить проверку без ошибок и предупреждений."""
        from pathlib import Path as _Path
        samples = _Path(__file__).resolve().parent.parent / "data" / "samples"
        for name in ("park7_weekday.json", "park7_weekend.json", "case_day1.json"):
            with self.subTest(name):
                data = json.loads((samples / name).read_text(encoding="utf-8"))
                result = check(data)
                self.assertEqual(result["errors"], [], name)
                self.assertEqual(result["warnings"], [], name)

    def test_park_not_working_with_duties_is_a_warning(self):
        result = self.spoiled(lambda d: d["parks"][0].update(state="down"))
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("не закроет" in w for w in result["warnings"]), result["warnings"])


if __name__ == "__main__":
    unittest.main()
