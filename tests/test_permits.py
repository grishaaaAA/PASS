"""Допуск водителя по марке автобуса и по маршрутам. Запуск: python -m unittest

У перевозчика водитель «обкатан» на марках (МАЗ, НЕФАЗ, ЛиАЗ, на всех
модификациях) и допущен на свой список маршрутов. Оба списка
необязательны: нет списка - нет ограничения, как раньше.
"""

import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path

from naryad.core.invariants import can_drive, check_plan, permit_problem
from naryad.core.model import Day, make_of, normal_make
from naryad.data.check import check
from naryad.data.csvio import read_csv, write_csv
from naryad.data.registry import load
from naryad.ops.replan import Breakdown, Late, NoShow, OpsState, apply, check_state, options_for
from naryad.solve.drivers import solve_drivers
from naryad.solve.vehicles import solve_vehicles
from naryad.web.api import Engine

from tests.test_core import SAMPLES

PARK7 = SAMPLES / "park7_weekday.json"


def raw_day() -> dict:
    return json.loads(PARK7.read_text(encoding="utf-8"))


def restricted(raw: dict) -> dict:
    """Половина водителей обкатана только на VOLGABUS, каждый третий - только на двух маршрутах.

    Закреплённые за автобусом водители остаются на своей марке, иначе данные
    противоречат сами себе.
    """
    raw = copy.deepcopy(raw)
    vehicles = {v["id"]: v for v in raw["vehicles"]}
    routes = sorted(r["id"] for r in raw["routes"])
    for n, driver in enumerate(raw["drivers"]):
        home = vehicles.get(driver["home_vehicle_id"])
        if n % 2 == 0:
            driver["makes"] = [make_of(home)] if home else ["VOLGABUS"]
        if n % 3 == 0 and not home:
            driver["routes"] = routes[n % len(routes):][:2] or routes[:2]
    return raw


def plan_of(raw: dict):
    day = Day.from_dict(raw)
    return day, solve_drivers(day, solve_vehicles(day))


class TestRule(unittest.TestCase):

    def setUp(self):
        self.day = Day.from_dict(raw_day())
        self.bus = next(v for v in self.day.vehicles.values() if v.make == "ЛиАЗ")
        self.driver = next(d for d in self.day.drivers.values() if self.bus.cls in d.classes)

    def test_spellings_of_one_make_match(self):
        self.assertEqual({normal_make(x) for x in ("ЛиАЗ", "LIAZ", "лиаз", "Li-AZ")}, {"LIAZ"})
        self.assertEqual(normal_make("ВолгаБас"), normal_make("VOLGABUS"))
        self.assertEqual(make_of({"model": "VOLGABUS 5270G"}), "VOLGABUS")
        self.assertEqual(make_of({"model": "VOLGABUS 5270G", "make": "Волжанин"}), "Волжанин")

    def test_no_lists_means_no_restriction(self):
        self.assertIsNone(permit_problem(self.driver, self.bus, "P07-R01"))

    def test_make_and_route(self):
        import dataclasses
        other_make = dataclasses.replace(self.driver, makes=(normal_make("VOLGABUS"),))
        self.assertIn("не обкатан на марке ЛиАЗ", permit_problem(other_make, self.bus, "P07-R01"))
        own_make = dataclasses.replace(self.driver, makes=(normal_make("лиаз"),))
        self.assertTrue(can_drive(own_make, self.bus, "P07-R01"))
        routes = dataclasses.replace(self.driver, routes=("P07-R02",))
        self.assertIn("нет допуска к маршруту P07-R01", permit_problem(routes, self.bus, "P07-R01"))
        self.assertTrue(can_drive(routes, self.bus, None), "у резерва маршрута нет")
        no_class = dataclasses.replace(self.driver, classes=())
        self.assertIn("нет допуска к классу", permit_problem(no_class, self.bus, None))


class TestMorningPlan(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.day, cls.plan = plan_of(restricted(raw_day()))

    def test_every_assignment_respects_make_and_route(self):
        day, plan = self.day, self.plan
        for shift_id, driver_id in plan.drivers.items():
            duty = day.duties[day.shifts[shift_id].duty_id]
            vehicle = day.vehicles[plan.vehicles[duty.id]]
            self.assertTrue(can_drive(day.drivers[driver_id], vehicle, duty.route_id), shift_id)
        self.assertEqual(check_plan(day, plan), [])

    def test_restrictions_really_bite(self):
        """Ограничения настоящие: без учёта марки многие назначения были бы незаконны."""
        broken = sum(1 for s, d in self.plan_without().drivers.items()
                     if not can_drive(self.day.drivers[d],
                                      self.day.vehicles[self.plan_without().vehicles[self.day.shifts[s].duty_id]],
                                      self.day.duties[self.day.shifts[s].duty_id].route_id))
        self.assertGreater(broken, 50)

    def plan_without(self):
        if not hasattr(self, "_without"):
            plain = Day.from_dict(raw_day())
            self._without = solve_drivers(plain, solve_vehicles(plain))
        return self._without

    def test_lists_that_allow_everything_change_nothing(self):
        raw = raw_day()
        makes = sorted({make_of(v) for v in raw["vehicles"]})
        for driver in raw["drivers"]:
            driver["makes"] = makes
            driver["routes"] = [r["id"] for r in raw["routes"]]
        _, wide = plan_of(raw)
        _, plain = plan_of(raw_day())
        self.assertEqual((wide.vehicles, wide.drivers, wide.unfilled), (plain.vehicles, plain.drivers, plain.unfilled))


class TestDuringTheDay(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.day, cls.plan = plan_of(restricted(raw_day()))
        cls.state = OpsState.from_plan(cls.day, cls.plan)

    def legal(self, event):
        for option in options_for(self.state, event, limit=None):
            problems = [v for v in check_state(apply(self.state, event, option)) if v.code == "driver_permit"]
            self.assertEqual(problems, [], f"{event.title}: {option.title}")

    def test_every_option_respects_permits(self):
        day, state = self.day, self.state
        line = sorted((d for d in day.duties.values() if d.type == "line" and d.start < 8 * 60 + 40 < d.end
                       and state.vehicle_at(d.id, 8 * 60 + 40)), key=lambda d: d.id)
        for duty in line[:8]:
            self.legal(Breakdown(state.vehicle_at(duty.id, 8 * 60 + 40), 8 * 60 + 40))
        shifts = sorted((s for s in day.shifts.values() if day.duties[s.duty_id].type == "line"
                         and s.start >= 7 * 60 and state.driver_at(s.id, s.start)), key=lambda s: s.start)
        for shift in shifts[:8]:
            driver = state.driver_at(shift.id, shift.start)
            self.legal(NoShow(driver, shift.start - 20))
            self.legal(Late(driver, shift.start - 5, 30))

    def test_check_state_names_the_make(self):
        shift_id, driver_id = next((s, d) for s, d in self.plan.drivers.items() if self.day.drivers[d].makes)
        driver = self.day.drivers[driver_id]
        duty_id = self.day.shifts[shift_id].duty_id
        foreign = next(v for v in self.day.vehicles.values()
                       if v.cls in driver.classes and normal_make(v.make) not in driver.makes)
        state = copy.deepcopy(self.state)
        for segment in state.vehicles[duty_id]:
            segment.who = foreign.id
        texts = [v.text for v in check_state(state) if v.code == "driver_permit"]
        self.assertTrue(any("не обкатан на марке" in t for t in texts), texts)

    def test_edit_suggestions_respect_permits(self):
        engine = Engine()
        day_id = engine.handle("POST", "/api/days", None, restricted(raw_day()))[1]["day_id"]
        engine.handle("POST", f"/api/days/{day_id}/plan", None, {})
        record = engine.days[day_id]
        shift = next(s for s in record.day.shifts.values()
                     if record.day.duties[s.duty_id].type == "line" and record.state.drivers.get(s.id))
        status, answer = engine.handle("GET", f"/api/days/{day_id}/edits/options", {"shift_id": shift.id}, None)
        self.assertEqual(status, 200)
        for item in answer["drivers"]:
            status, after = engine.handle("POST", f"/api/days/{day_id}/edits", None,
                                          {"type": "set_driver", "shift_id": shift.id, "driver_id": item["driver_id"]})
            self.assertEqual(status, 200, after)
            break


class TestData(unittest.TestCase):

    def test_restricted_day_passes_the_check(self):
        self.assertEqual(check(restricted(raw_day()))["errors"], [])

    def test_home_bus_of_another_make_is_an_error(self):
        raw = raw_day()
        vehicles = {v["id"]: v for v in raw["vehicles"]}
        driver = next(d for d in raw["drivers"] if d["home_vehicle_id"])
        driver["makes"] = ["MAZ"] if make_of(vehicles[driver["home_vehicle_id"]]) != "MAZ" else ["LIAZ"]
        self.assertTrue(any("не обкатан" in e for e in check(raw)["errors"]))

    def test_bad_lists(self):
        for value, text in (([], "пустое"), ("МАЗ", "список"), ([""], "список"), ([5], "список")):
            raw = raw_day()
            raw["drivers"][0]["makes"] = value
            self.assertTrue(any(text in e for e in check(raw)["errors"]), value)

    def test_unknown_route_is_a_warning(self):
        raw = raw_day()
        raw["drivers"][0]["routes"] = ["P99-R01"]
        result = check(raw)
        self.assertEqual(result["errors"], [])
        self.assertTrue(any("P99-R01" in w for w in result["warnings"]))

    def test_csv_round_trip_keeps_permits(self):
        raw = restricted(raw_day())
        with tempfile.TemporaryDirectory() as folder:
            write_csv(raw, folder)
            again = read_csv(folder)
        self.assertEqual(again["drivers"], raw["drivers"])

    def test_csv_without_permit_columns_reads_as_before(self):
        raw = raw_day()
        with tempfile.TemporaryDirectory() as folder:
            write_csv(raw, folder)
            with open(Path(folder) / "drivers.csv", encoding="utf-8-sig") as f:
                header = next(csv.reader(f, delimiter=";"))
            self.assertNotIn("makes", header)
            self.assertEqual(read_csv(folder)["drivers"], raw["drivers"])

    def test_registry_reads_makes_and_route_numbers(self):
        from tests.test_registry import META, RUSSIAN
        files = dict(RUSSIAN)
        files["Автобусы"] = ("Гаражный номер;Марка;Класс;Топливо;Парк;Тех. состояние\n"
                             "7001;ЛиАЗ;большой;газ;П7;исправен\n7002;МАЗ;большой;газ;П7;в ремонте\n")
        files["Водители"] = ("Табельный номер;ФИО;Парк;Допуск;Обкатан на;Маршруты;График;Медосмотр\n"
                             "070001;Макаров Д. В.;П7;большой;МАЗ, LIAZ;3;работает;прошел\n"
                             "070002;Иванов И. И.;П7;большой|средний;;;выходной;\n")
        data, report = load(files=files, meta=META)
        self.assertEqual(report["errors"], [])
        self.assertEqual(data["vehicles"][0]["make"], "ЛиАЗ")
        self.assertEqual(data["drivers"][0]["makes"], ["МАЗ", "LIAZ"])
        self.assertEqual(data["drivers"][0]["routes"], ["М3"], "номер маршрута переведён в код")
        self.assertNotIn("makes", data["drivers"][1], "пустая ячейка - нет ограничения")
        day = Day.from_dict(data)
        self.assertTrue(can_drive(day.drivers["070001"], day.vehicles["7001"], "М3"))


if __name__ == "__main__":
    unittest.main()
