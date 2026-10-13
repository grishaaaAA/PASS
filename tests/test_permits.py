"""Допуск водителя по марке автобуса и по маршрутам. Запуск: python -m unittest

У перевозчика водитель «обкатан» на марках (МАЗ, НЕФАЗ, ЛиАЗ, на всех
модификациях) и допущен на свой список маршрутов. Оба списка
необязательны: нет списка - нет ограничения, как раньше.
"""

from __future__ import annotations

import copy
import csv
import dataclasses
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from naryad.core.invariants import can_drive, check_plan, permit_problem
from naryad.core.model import Day, Plan, make_of, normal_make
from naryad.data.check import check
from naryad.data.csvio import read_csv, write_csv
from naryad.data.registry import load
from naryad.explain import why_unfilled
from naryad.ops.replan import Breakdown, Late, NoShow, OpsState, apply, check_state, options_for
from naryad.solve.baseline import baseline_drivers
from naryad.solve.drivers import solve_drivers
from naryad.solve.vehicles import solve_vehicles
from naryad.web.api import Engine

from tests.test_core import SAMPLES
from tests.test_registry import META, RUSSIAN

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


def tiny(duties: tuple, boards: tuple, drivers: list) -> dict:
    """Маленький день из парка №7: эти наряды со своими сменами, эти автобусы, эти водители."""
    raw = raw_day()
    raw["duties"] = [d for d in raw["duties"] if d["id"] in duties]
    raw["shifts"] = [s for s in raw["shifts"] if s["duty_id"] in duties]
    raw["vehicles"] = [v for v in raw["vehicles"] if v["board_number"] in boards]
    raw["parks"][0]["list_count"] = len(raw["vehicles"])
    raw["drivers"] = drivers
    return raw


def person(n: int, **fields) -> dict:
    """Водитель, который сегодня работает и прошёл медосмотр, с допуском ко всем классам."""
    return {"id": f"T{n}", "tab_number": f"99{n:04d}", "full_name": "Тестов Т. Т.", "park_id": "P07",
            "classes": ["medium", "big", "extra_big"], "home_vehicle_id": None, "schedule": "work",
            "medical": "passed", **fields}


# Наряд маршрута 3 с одной сменой, 06:10-14:55. Исправные большие автобусы без своего
# маршрута: 7003 - VOLGABUS, 7079 и 7083 - ЛиАЗ.
DUTY, SHIFT = "P07-R01-WD02", "P07-R01-WD02-S1"


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

    def test_model_after_the_make_is_cut(self):
        """Допуск на марку на всех модификациях: модель через дефис или пробел - не другая марка."""
        for text, make in (("ЛиАЗ-5292.65", "LIAZ"), ("НефАЗ-5299-30-51", "NEFAZ"), ("МАЗ-203", "MAZ"),
                           ("МАЗ 203", "MAZ"), ("Volgabus-5270.G2", "VOLGABUS"), ("ЛиАЗ5292", "LIAZ"),
                           ("Mercedes-Benz Citaro", "MERCEDES"), ("«ЛиАЗ» 529267", "LIAZ")):
            self.assertEqual(normal_make(text), make, text)
        self.assertEqual(make_of({"model": "ЛиАЗ-5292.65"}), "ЛиАЗ")
        self.assertEqual(make_of({"model": "VOLGABUS 5270G", "make": "МАЗ-203"}), "МАЗ")
        raw = raw_day()
        bus = next(v for v in raw["vehicles"] if v["model"].startswith("ЛиАЗ") and v["condition"] == "ok")
        bus["model"] = "ЛиАЗ-5292.65"
        driver = next(d for d in raw["drivers"] if not d["home_vehicle_id"] and "big" in d["classes"])
        driver["makes"] = ["ЛиАЗ"]
        self.assertEqual(check(raw)["errors"], [])
        day = Day.from_dict(raw)
        self.assertIsNone(permit_problem(day.drivers[driver["id"]], day.vehicles[bus["id"]], None))

    def test_foreign_makes_in_two_alphabets(self):
        for russian, latin in (("Скания", "Scania OmniLink"), ("Хайгер", "Higer KLQ6129"), ("Ивеко", "Iveco"),
                               ("Мерседес", "Mercedes-Benz Citaro"), ("Хёндэ", "Hyundai"),
                               ("Волга Бас", "VOLGABUS 5270G")):
            self.assertEqual(normal_make(russian), normal_make(latin), russian)

    def test_blank_make_field_takes_the_model(self):
        self.assertEqual(make_of({"model": "ЛиАЗ 529267", "make": " "}), "ЛиАЗ")
        self.assertEqual(make_of({"model": "", "make": None}), "")

    def test_no_lists_means_no_restriction(self):
        self.assertIsNone(permit_problem(self.driver, self.bus, "P07-R01"))

    def test_make_and_route(self):
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


class TestBusChoice(unittest.TestCase):
    """Автобус на наряд выбирается без оглядки на водителей; с допуском по марке это теряло смены."""

    def plans(self, makes: list):
        day = Day.from_dict(tiny((DUTY,), ("7003", "7079"), [person(1, makes=makes)]))
        vehicles = solve_vehicles(day)
        self.assertEqual(vehicles.vehicles, {DUTY: "P07-V0003"}, "по номеру первым идёт VOLGABUS 7003")
        return day, (solve_drivers(day, vehicles), baseline_drivers(day, vehicles, {}))

    def test_bus_of_another_make_closes_the_shift(self):
        """Водитель обкатан только на ЛиАЗ: на наряд встаёт простаивающий ЛиАЗ, и у нас, и вручную."""
        day, plans = self.plans(["ЛиАЗ"])
        for plan in plans:
            self.assertEqual(plan.vehicles, {DUTY: "P07-V0079"}, plan.meta["made_by"])
            self.assertEqual(plan.drivers, {SHIFT: "T1"})
            self.assertNotIn(SHIFT, plan.unfilled)
            self.assertEqual(check_plan(day, plan), [])

    def test_bus_stays_when_the_driver_fits(self):
        _, plans = self.plans(["VOLGABUS"])
        for plan in plans:
            self.assertEqual((plan.vehicles, plan.drivers), ({DUTY: "P07-V0003"}, {SHIFT: "T1"}))

    def test_swap_closes_more_shifts_on_a_whole_park(self):
        """Парк №7, у каждого водителя список марок: обмен закрывает больше смен, план законен."""
        raw = raw_day()
        vehicles = {v["id"]: v for v in raw["vehicles"]}
        for n, driver in enumerate(raw["drivers"]):
            home = vehicles.get(driver["home_vehicle_id"])
            driver["makes"] = [make_of(home)] if home else (["ЛиАЗ"] if n % 4 == 0 else ["VOLGABUS"])
        day = Day.from_dict(raw)
        vehicles = solve_vehicles(day)
        plan = solve_drivers(day, vehicles)
        with mock.patch("naryad.solve.drivers._swap_buses", return_value=False):
            without = solve_drivers(day, vehicles)
        self.assertGreater(len(plan.drivers), len(without.drivers))
        self.assertEqual(check_plan(day, plan), [])
        shifts = [s.id for s in day.shifts.values() if s.duty_id in plan.vehicles]
        self.assertEqual(sorted(shifts), sorted(list(plan.drivers) + [i for i in plan.unfilled if i in day.shifts]),
                         "каждая смена выпущенного наряда либо с водителем, либо в unfilled")
        manual = baseline_drivers(day, vehicles, {})
        self.assertEqual(check_plan(day, manual), [])


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

    def test_check_state_names_the_route(self):
        day = Day.from_dict(tiny((DUTY,), ("7079",), [person(1, routes=["P07-R02"])]))
        state = OpsState.from_plan(day, Plan(vehicles={DUTY: "P07-V0079"}, drivers={SHIFT: "T1"}))
        texts = [v.text for v in check_state(state) if v.code == "driver_permit"]
        self.assertEqual(len(texts), 1, texts)
        self.assertIn("нет допуска к маршруту P07-R01", texts[0])

    def test_idle_bus_of_another_make(self):
        """Первый простаивающий автобус - чужой марки: «из парка» берёт следующий, на который водитель есть."""
        day = Day.from_dict(tiny((DUTY,), ("7003", "7079", "7083"),
                                 [person(1, makes=["ЛиАЗ"]), person(2, makes=["ЛиАЗ"])]))
        state = OpsState.from_plan(day, Plan(vehicles={DUTY: "P07-V0079"}, drivers={SHIFT: "T1"}))
        event = Breakdown("P07-V0079", 8 * 60)
        idle = [o for o in options_for(state, event, limit=None) if o.kind == "idle"]
        self.assertEqual([o.title for o in idle], ["Автобус из парка 7083 + водитель 990002"])
        self.assertEqual([v for v in check_state(apply(state, event, idle[0])) if v.code == "driver_permit"], [])

    def test_donor_driver_needs_the_route(self):
        """Автобус снимают с другого маршрута вместе с водителем: водителю нужен допуск к маршруту наряда."""
        day, plan = plan_of(raw_day())
        state, t = OpsState.from_plan(day, plan), 8 * 60 + 40
        for duty in sorted((d for d in day.duties.values() if d.type == "line" and state.vehicle_at(d.id, t)),
                           key=lambda d: d.id):
            event = Breakdown(state.vehicle_at(duty.id, t), t)
            donor = next((o for o in options_for(state, event, limit=None) if o.kind == "donor"), None)
            if donor is not None:
                break
        shift = state.shift_at(duty.id, t)
        moved = next(seg.who for kind, key, seg in donor.changes if kind == "driver" and key == shift.id)
        home = next(key for kind, key, _ in donor.changes if kind == "vehicle_end")
        # водитель донора допущен только на свой маршрут
        day.drivers[moved] = dataclasses.replace(day.drivers[moved], routes=(day.duties[home].route_id,))
        state = OpsState.from_plan(day, plan)
        for option in options_for(state, event, limit=None):
            on_shift = [seg.who for kind, key, seg in option.changes if kind == "driver" and key == shift.id]
            self.assertNotIn(moved, on_shift, option.title)
            self.assertEqual([v for v in check_state(apply(state, event, option)) if v.code == "driver_permit"],
                             [], option.title)

    def test_edit_suggestions_respect_permits(self):
        """Всё, что подсказано, ставится без force: водители на смены и автобусы на наряды."""
        engine = Engine()
        day_id = engine.handle("POST", "/api/days", None, restricted(raw_day()))[1]["day_id"]
        engine.handle("POST", f"/api/days/{day_id}/plan", None, {})
        record = engine.days[day_id]
        day, state = record.day, record.state
        lines = [s for s in sorted(day.shifts.values(), key=lambda s: s.id)
                 if day.duties[s.duty_id].type == "line" and state.drivers.get(s.id)]
        # смены на ЛиАЗ (у половины водителей допуск только на VOLGABUS) и наряды, где водитель со списком марок
        shifts = [s.id for s in lines if normal_make(day.vehicles[state.vehicle_at(s.duty_id, s.start)].make) == "LIAZ"]
        duties = sorted({s.duty_id for s in lines if day.drivers[state.driver_at(s.id, s.start)].makes})
        targets = ([("set_driver", "shift_id", s, "drivers", "driver_id") for s in shifts[:4]]
                   + [("set_vehicle", "duty_id", d, "vehicles", "vehicle_id") for d in duties[:4]])
        tried = 0
        for kind, field, key, listed, who in targets:
            status, answer = engine.handle("GET", f"/api/days/{day_id}/edits/options", {field: key}, None)
            self.assertEqual(status, 200, answer)
            for item in answer[listed][:8]:
                kept = record.state, record.log, record.history_after
                status, after = engine.handle("POST", f"/api/days/{day_id}/edits", None,
                                              {"type": kind, field: key, who: item[who]})
                record.state, record.log, record.history_after = kept   # каждая подсказка - на одном и том же дне
                self.assertEqual(status, 200, (key, item, after.get("violations")))
                tried += 1
        self.assertGreater(tried, 40)


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
        first, second = raw["drivers"][0]["id"], raw["drivers"][1]["id"]
        raw["drivers"][0]["routes"] = ["P99-R01"]
        raw["drivers"][1]["routes"] = ["P99-R01", "P07-R01"]
        result = check(raw)
        self.assertEqual(result["errors"], [])
        unknown = [w for w in result["warnings"] if "P99-R01" in w]
        self.assertEqual(len(unknown), 1, "одна строка на маршрут, а не на водителя")
        self.assertIn(first, unknown[0])
        self.assertIn(second, unknown[0])
        off_line = [w for w in result["warnings"] if "на линию не попадут" in w]
        self.assertEqual(len(off_line), 1, result["warnings"])
        self.assertIn(first, off_line[0])
        self.assertNotIn(second, off_line[0], "у второго один маршрут из допуска в дне есть")

    def test_make_without_letters_is_an_error(self):
        """«-», «?» или номер модели вместо марки молча запрещали водителю все автобусы."""
        for entity, field, value in (("drivers", "makes", ["-"]), ("drivers", "makes", ["ЛиАЗ", "?"]),
                                     ("drivers", "makes", ["5292"]), ("vehicles", "make", " "),
                                     ("vehicles", "make", "\u2014")):
            raw = raw_day()
            raw[entity][0][field] = value
            self.assertTrue(any("нет марки" in e for e in check(raw)["errors"]), value)

    def test_make_absent_from_the_day_is_a_warning(self):
        raw = raw_day()
        first, second = raw["drivers"][0]["id"], raw["drivers"][1]["id"]
        raw["drivers"][0]["makes"] = ["МАЗ"]
        raw["drivers"][1]["makes"] = ["Скания", "лиаз"]
        result = check(raw)
        self.assertEqual(result["errors"], [])
        text = " | ".join(result["warnings"])
        self.assertIn("«МАЗ»", text)
        self.assertIn("«Скания»", text)
        nowhere = [w for w in result["warnings"] if "ни на один автобус" in w]
        self.assertEqual(len(nowhere), 1, result["warnings"])
        self.assertIn(first, nowhere[0])
        self.assertNotIn(second, nowhere[0], "на ЛиАЗ второй водитель обкатан")

    def test_two_makes_with_a_space_are_reported(self):
        raw = raw_day()
        raw["drivers"][0]["makes"] = ["ЛиАЗ VOLGABUS"]
        self.assertTrue(any("несколько марок через пробел" in w for w in check(raw)["warnings"]))
        raw["drivers"][0]["makes"] = ["ЛиАЗ 5292"]   # модель после марки - не вторая марка
        self.assertFalse(any("несколько марок" in w for w in check(raw)["warnings"]))

    def test_buses_without_make_are_reported_when_makes_matter(self):
        raw = raw_day()
        raw["vehicles"][0]["model"] = ""
        self.assertFalse(any("не указана марка" in w for w in check(raw)["warnings"]), "без допусков по марке неважно")
        raw["drivers"][0]["makes"] = ["ЛиАЗ"]
        self.assertTrue(any("не указана марка" in w and raw["vehicles"][0]["id"] in w
                            for w in check(raw)["warnings"]))

    def test_home_bus_route_outside_driver_routes_is_a_warning(self):
        """Водитель закреплён за автобусом, а к маршруту этого автобуса не допущен: вместе они не выйдут."""
        raw = raw_day()
        vehicles = {v["id"]: v for v in raw["vehicles"]}
        driver = next(d for d in raw["drivers"]
                      if d["home_vehicle_id"] and vehicles[d["home_vehicle_id"]]["home_route_id"])
        home_route = vehicles[driver["home_vehicle_id"]]["home_route_id"]
        driver["routes"] = [next(r["id"] for r in raw["routes"] if r["id"] != home_route)]
        result = check(raw)
        self.assertEqual(result["errors"], [])
        # водитель назван табельным номером, как его знает диспетчер, а не кодом записи
        self.assertTrue(any(w.startswith(f"водитель {driver['tab_number']}:") and "не допущен" in w
                            for w in result["warnings"]), result["warnings"])
        driver["routes"].append(home_route)
        self.assertFalse(any(w.startswith(f"водитель {driver['tab_number']}:") for w in check(raw)["warnings"]))

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
        files = dict(RUSSIAN)
        files["Автобусы"] = ("Гаражный номер;Марка;Класс;Топливо;Парк;Тех. состояние\n"
                             "7001;ЛиАЗ;большой;газ;П7;исправен\n7002;МАЗ;большой;газ;П7;в ремонте\n")
        files["Водители"] = ("Табельный номер;ФИО;Парк;Допуск;Обкатан на;Маршруты;График;Медосмотр\n"
                             "070001;Макаров Д. В.;П7;большой;МАЗ, LIAZ;3;работает;прошел\n"
                             "070002;Иванов И. И.;П7;большой|средний;;;выходной;\n")
        data, report = load(files=files, meta=META)
        self.assertEqual(report["errors"], [])
        self.assertEqual(make_of(data["vehicles"][0]), "ЛиАЗ")
        self.assertEqual(data["drivers"][0]["makes"], ["МАЗ", "LIAZ"])
        self.assertEqual(data["drivers"][0]["routes"], ["М3"], "номер маршрута переведён в код")
        self.assertNotIn("makes", data["drivers"][1], "пустая ячейка - нет ограничения")
        day = Day.from_dict(data)
        self.assertTrue(can_drive(day.drivers["070001"], day.vehicles["7001"], "М3"))


class TestRegistry(unittest.TestCase):
    """Реестр перевозчика: «Марка», «Обкатан на», «Маршруты»."""

    def load(self, **sheets):
        files = dict(RUSSIAN)
        files.update(sheets)
        return load(files=files, meta=META)

    def test_brand_column_alone_reads_as_before(self):
        """Одна «Марка» без «Модели» - это марка с моделью, как читалась до допуска по марке."""
        data, report = self.load(Автобусы="Гаражный номер;Марка;Класс;Топливо;Парк;Тех. состояние\n"
                                          "7001;ЛиАЗ-529267;большой;газ;П7;исправен\n"
                                          "7002;МАЗ 203;большой;газ;П7;в ремонте\n")
        self.assertEqual(report["errors"], [])
        self.assertEqual([v["model"] for v in data["vehicles"]], ["ЛиАЗ-529267", "МАЗ 203"])
        self.assertTrue(all("make" not in v for v in data["vehicles"]))
        self.assertEqual(report["entities"][4]["matched"]["model"], "Марка")
        day = Day.from_dict(data)
        self.assertEqual([day.vehicles[v].make for v in ("7001", "7002")], ["ЛиАЗ", "МАЗ"])

    def test_brand_and_model_columns(self):
        data, report = self.load(Автобусы="Гаражный номер;Марка;Модель;Класс;Топливо;Парк;Тех. состояние\n"
                                          "7001;НефАЗ;5299-30-51;большой;газ;П7;исправен\n"
                                          "7002;МАЗ;203;большой;газ;П7;в ремонте\n")
        self.assertEqual(report["errors"], [])
        self.assertEqual([(v["make"], v["model"]) for v in data["vehicles"]],
                         [("НефАЗ", "5299-30-51"), ("МАЗ", "203")])

    def test_dash_or_no_in_a_permit_is_not_guessed(self):
        """Прочерк и «нет» читаются и как «без ограничения», и как «не допущен никуда»."""
        for column, cell in (("Обкатан на", "-"), ("Обкатан на", "нет"), ("Обкатан на", "?"),
                             ("Маршруты", "\u2014"), ("Маршруты", "нет")):
            data, report = self.load(Водители=f"Табельный номер;ФИО;Парк;Допуск;{column};График;Медосмотр\n"
                                              f"070001;Макаров Д. В.;П7;большой;{cell};работает;прошел\n")
            self.assertTrue(any(f"столбец «{column}», строка 2: значение «{cell}» не понято" in e
                                for e in report["errors"]), (cell, report["errors"]))
            self.assertIn("пустая ячейка", report["errors"][-1])

    def test_line_break_inside_a_cell_separates_makes(self):
        data, report = self.load(Водители="Табельный номер;ФИО;Парк;Допуск;Обкатан на;График;Медосмотр\n"
                                          '070001;Макаров Д. В.;П7;большой;"ЛиАЗ\nМАЗ";работает;прошел\n')
        self.assertEqual(report["errors"], [])
        self.assertEqual(data["drivers"][0]["makes"], ["ЛиАЗ", "МАЗ"])

    def test_route_numbers_in_other_spellings(self):
        """«13a» латиницей, «39-Э», «2М» из составного номера - те же маршруты."""
        data, report = self.load(
            Маршруты="Код;Парк;Номер маршрута;Допустимые классы;Важность;Время оборота\n"
                     "М13а;П7;13а;большой;высокая;160\nМ39Э;П7;39Э;большой;высокая;160\n"
                     "М2М;П7;2М,2МА,2МБ;большой;низкая;160\n",
            Наряды="Наряд;Парк;Тип;Маршрут;Класс автобуса;Тип дня;Выход;Заезд;Смен\n"
                   "Н1;П7;линейный;М13а;большой;будни;4:50;13:20;1\nН2;П7;линейный;М39Э;большой;будни;6:00;14:00;1\n",
            Водители="Табельный номер;ФИО;Парк;Допуск;Маршруты;График;Медосмотр\n"
                     "070001;Макаров Д. В.;П7;большой;13a|39-E;работает;прошел\n"
                     "070002;Иванов И. И.;П7;большой;13 А, 2М;работает;прошел\n"
                     "070003;Петров П. П.;П7;большой;99;работает;прошел\n")
        self.assertEqual(report["errors"], [])
        self.assertEqual([d["routes"] for d in data["drivers"]], [["М13а", "М39Э"], ["М13а", "М2М"], ["99"]])
        off_line = [w for w in report["warnings"] if "на линию не попадут" in w]
        self.assertEqual(len(off_line), 1, report["warnings"])
        self.assertIn("070003", off_line[0])

    def test_code_that_is_also_another_number_is_an_error(self):
        """«3» - код маршрута №13 и номер маршрута с кодом 1: не угадываем."""
        data, report = self.load(
            Маршруты="Код;Парк;Номер маршрута;Допустимые классы;Важность;Время оборота\n"
                     "1;П7;3;большой;высокая;160\n3;П7;13;большой;высокая;160\n",
            Наряды="Наряд;Парк;Тип;Маршрут;Класс автобуса;Тип дня;Выход;Заезд;Смен\n"
                   "Н1;П7;линейный;1;большой;будни;4:50;13:20;1\nН2;П7;линейный;3;большой;будни;6:00;14:00;1\n",
            Водители="Табельный номер;ФИО;Парк;Допуск;Маршруты;График;Медосмотр\n"
                     "070001;Макаров Д. В.;П7;большой;3;работает;прошел\n"
                     "070002;Иванов И. И.;П7;большой;13;работает;прошел\n")
        self.assertEqual(len(report["errors"]), 1, report["errors"])
        self.assertIn("строка 2, столбец «Маршруты»: маршрут «3» - это и код маршрута №13, "
                      "и номер маршрута с кодом 1", report["errors"][0])

    def test_number_of_two_routes_is_an_error(self):
        """«39E» латиницей подходит и к «39Е», и к «39Э»: не угадываем, а точное написание понятно."""
        data, report = self.load(
            Маршруты="Код;Парк;Номер маршрута;Допустимые классы;Важность;Время оборота\n"
                     "М39Е;П7;39Е;большой;высокая;160\nМ39Э;П7;39Э;большой;высокая;160\n",
            Наряды="Наряд;Парк;Тип;Маршрут;Класс автобуса;Тип дня;Выход;Заезд;Смен\n"
                   "Н1;П7;линейный;М39Е;большой;будни;4:50;13:20;1\nН2;П7;линейный;М39Э;большой;будни;6:00;14:00;1\n",
            Водители="Табельный номер;ФИО;Парк;Допуск;Маршруты;График;Медосмотр\n"
                     "070001;Макаров Д. В.;П7;большой;39E;работает;прошел\n"
                     "070002;Иванов И. И.;П7;большой;39Э;работает;прошел\n")
        self.assertEqual(len(report["errors"]), 1, report["errors"])
        self.assertIn("строка 2, столбец «Маршруты»: маршрут «39E» - такой номер у нескольких маршрутов "
                      "парка, коды М39Е, М39Э", report["errors"][0])
        self.assertEqual(data["drivers"][1]["routes"], ["М39Э"])


class TestExplain(unittest.TestCase):
    """Почему смена без водителя, когда мешает допуск, и сходятся ли числа причин."""

    def why(self, drivers: list, duty: str = DUTY, model: str | None = None) -> dict:
        raw = tiny((duty,), ("7079",), drivers)
        if model is not None:
            raw["vehicles"][0]["model"] = model
        day, plan = plan_of(raw)
        shift = day.shifts_by_duty[duty][0].id
        self.assertIn(shift, plan.unfilled)
        item = why_unfilled(day, plan, shift)
        numbers = item["numbers"]
        self.assertEqual(sum(v for k, v in numbers.items() if k != "drivers"), numbers["drivers"],
                         "каждый водитель парка посчитан ровно один раз")
        return item

    def test_only_permits_block(self):
        item = self.why([person(1, makes=["VOLGABUS"]), person(2, routes=["P07-R02"]),
                         person(3, classes=["medium"])])
        self.assertIn("не допущен к автобусу 7079 (ЛиАЗ)", item["answer"])
        self.assertNotIn("по графику и нормам можно", item["answer"])
        self.assertEqual(item["numbers"]["не обкатаны на марке ЛиАЗ"], 1)
        self.assertEqual(item["numbers"]["нет допуска к маршруту 3"], 1)
        self.assertEqual(item["numbers"]["нет допуска к классу «большой»"], 1)

    def test_other_reasons_keep_the_answer_about_norms(self):
        item = self.why([person(1, makes=["VOLGABUS"]), person(2, schedule="day_off", medical=None)])
        self.assertIn("по графику и нормам", item["answer"])

    def test_bus_without_make(self):
        item = self.why([person(1, makes=["ЛиАЗ"])], model="")
        self.assertIn("(без марки)", item["answer"])
        self.assertEqual(item["numbers"]["не обкатаны на марке без марки"], 1)

    def test_reserve_has_no_route_reason(self):
        """У резервного наряда маршрута нет: допуск по маршрутам ему не мешает."""
        item = self.why([person(1, makes=["VOLGABUS"], routes=["P07-R02"])], duty="P07-RES-WD01")
        self.assertEqual(item["numbers"].get("не обкатаны на марке ЛиАЗ"), 1)
        self.assertFalse(any("маршрут" in k for k in item["numbers"]), item["numbers"])


if __name__ == "__main__":
    unittest.main()
