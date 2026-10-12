"""Объяснения решений (А6). Запуск: python -m unittest"""

import dataclasses
import json
import random
import time
import unittest

from naryad.core.model import Day, Plan
from naryad.explain import (CLASS_NAMES, day_summary, explain_option, interval, intervals,
                            route_interval, why_driver, why_unfilled, why_vehicle)
from naryad.ops.replan import Breakdown, Late, OpsState, options_for
from naryad.solve.compare import with_shortage, worst_growth
from naryad.solve.drivers import History, day_base, solve_drivers
from naryad.solve.vehicles import solve_vehicles

from tests.test_core import SAMPLES


def stressed():
    """День 7-го парка: 15% автобусов не вышли, 330 водителей на больничном."""
    day = with_shortage(Day.load(SAMPLES / "park7_weekday.json"), 0.15, 1)
    rng = random.Random(2)
    for driver_id in rng.sample(sorted(day.drivers), 330):
        day.drivers[driver_id] = dataclasses.replace(day.drivers[driver_id], schedule="sick")
    return day, solve_drivers(day, solve_vehicles(day))


class TestExplain(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.day, cls.plan = stressed()

    def check_shape(self, item):
        self.assertTrue(item["question"].endswith("?") or item["question"].startswith("Сводка"))
        self.assertTrue(item["answer"])
        self.assertTrue(item["reasons"])
        json.dumps(item, ensure_ascii=False)  # интерфейс получает это как есть

    def test_every_unfilled_explained_fast(self):
        start = time.perf_counter()
        items = [why_unfilled(self.day, self.plan, i) for i in self.plan.unfilled]
        self.assertLess(time.perf_counter() - start, 5.0)
        self.assertEqual(len(items), len(self.plan.unfilled))
        for item in items:
            self.check_shape(item)

    def test_no_vehicle_numbers_add_up(self):
        duty_id = next(k for k, v in self.plan.unfilled.items()
                       if v == "no_vehicle" and self.day.duties[k].type == "line")
        item = why_unfilled(self.day, self.plan, duty_id)
        self.assertEqual(item["numbers"]["vehicles_ok"], item["numbers"]["vehicles_busy"])
        self.assertIn("большой", item["answer"])
        self.assertIn("Интервал", " ".join(item["reasons"]))

    def test_no_driver_names_the_class(self):
        def without(shift_id):
            cls = self.day.vehicles[self.plan.vehicles[self.day.shifts[shift_id].duty_id]].cls
            return cls, sum(cls not in d.classes for d in self.day.drivers.values())
        shift_id = next(k for k, v in sorted(self.plan.unfilled.items())
                        if v == "no_driver" and without(k)[1] > 0)
        item = why_unfilled(self.day, self.plan, shift_id)
        self.check_shape(item)
        self.assertEqual(item["numbers"]["drivers"], len(self.day.drivers))
        cls, count = without(shift_id)
        self.assertIn(f"нет допуска к классу «{CLASS_NAMES[cls]}»: {count}", " ".join(item["reasons"]))

    def test_interval_grows_when_buses_missing(self):
        duty_id = next(k for k, v in self.plan.unfilled.items()
                       if v == "no_vehicle" and self.day.duties[k].type == "line")
        duty = self.day.duties[duty_id]
        t = (duty.start + duty.end) // 2
        iv = interval(self.day, self.plan, duty.route_id, t)
        self.assertLess(iv["running_buses"], iv["planned_buses"])
        self.assertGreater(iv["actual_min"], iv["planned_min"])

    def test_summary_counts_reserves(self):
        summary = day_summary(self.day, self.plan)
        reserves = sum(1 for d in self.plan.unfilled if d in self.day.duties
                       and self.day.duties[d].type == "reserve")
        self.assertEqual(summary["numbers"]["reserve_unfilled"], reserves)
        self.assertGreater(reserves, 0)

    def test_own_and_other_vehicle(self):
        own = next(d for d, v in self.plan.vehicles.items() if self.day.duties[d].type == "line"
                   and self.day.vehicles[v].home_route_id == self.day.duties[d].route_id)
        other = next(d for d, v in self.plan.vehicles.items() if self.day.duties[d].type == "line"
                     and self.day.vehicles[v].home_route_id != self.day.duties[d].route_id)
        self.assertTrue(why_vehicle(self.day, self.plan, own)["numbers"]["own_route"])
        item = why_vehicle(self.day, self.plan, other)
        self.assertFalse(item["numbers"]["own_route"])
        self.check_shape(item)

    def test_tired_home_driver_reason(self):
        day = Day.load(SAMPLES / "park7_weekday.json")
        plan = solve_drivers(day, solve_vehicles(day))
        shift_id, driver_id = next((s, d) for s, d in plan.drivers.items()
                                   if day.drivers[d].home_vehicle_id
                                   == plan.vehicles[day.shifts[s].duty_id])
        vehicle_id = plan.vehicles[day.shifts[shift_id].duty_id]
        for other in [d for d in day.drivers.values()
                      if d.home_vehicle_id == vehicle_id and d.id != driver_id]:
            day.drivers[other.id] = dataclasses.replace(other, schedule="day_off")
        history = History()  # вчера закончил за час до последней смены своего автобуса: не годится ни на одну
        latest = max(s.start for d, v in plan.vehicles.items() if v == vehicle_id
                     for s in day.shifts_by_duty[d])
        history.get(driver_id).last_end = day_base(day) + latest - 60
        history.get(driver_id).last_length = 10 * 60
        again = solve_drivers(day, solve_vehicles(day), history=History(
            drivers={driver_id: dataclasses.replace(history.get(driver_id))}))
        self.assertNotEqual(again.drivers.get(shift_id), driver_id)
        text = " ".join(why_driver(day, again, shift_id, history)["reasons"])
        self.assertIn("не отдохнул", text)
        self.assertIn("выходной по графику", text)

    def test_rest_explained_like_solver_decides(self):
        """Законный сокращённый отдых - не нарушение; после 45 ч отдыха счётчик смен не мешает."""
        day = Day.load(SAMPLES / "park7_weekday.json")
        plan = solve_drivers(day, solve_vehicles(day))
        shift_id, driver_id = sorted(plan.drivers.items())[0]
        start = day_base(day) + day.shifts[shift_id].start
        history = History()
        state = history.get(driver_id)
        cases = [(10 * 60, 4 * 60, 0, "сокращённый отдых"),         # 9-11 ч после короткой смены
                 (50 * 60, 8 * 60, 6, "еженедельный отдых"),        # 6 смен, но потом 50 ч отдыха
                 (20 * 60, 8 * 60, 6, "Нарушение: уже 6 смен подряд"),
                 (8 * 60, 4 * 60, 0, "Нарушение: не отдохнул")]
        for rest, length, in_row, expected in cases:
            with self.subTest(expected):
                state.last_end, state.last_length, state.in_row = start - rest, length, in_row
                text = " ".join(why_driver(day, plan, shift_id, history)["reasons"])
                self.assertIn(expected, text)
                self.assertIn("Приказа № 160", text)
                if not expected.startswith("Нарушение"):
                    self.assertNotIn("Нарушение", text)

    def test_reserve_vehicle_not_own_route(self):
        duty_id = next(d for d in sorted(self.plan.vehicles) if self.day.duties[d].type == "reserve")
        plan = Plan(vehicles=dict(self.plan.vehicles), unfilled=dict(self.plan.unfilled))
        cls = self.day.duties[duty_id].vehicle_class
        line_id, home_bus = next((d, v) for d, v in sorted(plan.vehicles.items())
                                 if self.day.vehicles[v].home_route_id and self.day.vehicles[v].cls == cls)
        plan.vehicles[duty_id], plan.vehicles[line_id] = home_bus, plan.vehicles[duty_id]
        self.assertFalse(why_vehicle(self.day, plan, duty_id)["numbers"]["own_route"])  # у резерва нет маршрута

    def test_unfilled_numbers_and_shortest(self):
        duty_id = next(k for k, v in sorted(self.plan.unfilled.items())
                       if v == "no_vehicle" and self.day.duties[k].type == "line")
        duty = self.day.duties[duty_id]
        classes = self.day.routes[duty.route_id].allowed_classes
        busy = sum(1 for v in self.plan.vehicles.values() if self.day.vehicles[v].park_id == duty.park_id
                   and self.day.vehicles[v].cls in classes)
        item = why_unfilled(self.day, self.plan, duty_id)
        self.assertEqual(item["numbers"]["vehicles_busy"], busy)
        group = [d for d in self.day.duties.values() if d.route_id == duty.route_id
                 and d.day_type == self.day.day_type]
        shortest = min(group, key=lambda d: (d.end - d.start, d.id))
        self.assertIn(shortest.id, self.plan.unfilled)  # на маршруте с потерями первым выпадает самый короткий
        self.assertIn("самый короткий наряд", " ".join(why_unfilled(self.day, self.plan, shortest.id)["reasons"]))

    def test_option_explained(self):
        day = Day.load(SAMPLES / "park7_weekday.json")
        state = OpsState.from_plan(day, solve_drivers(day, solve_vehicles(day)))
        at = 8 * 60 + 40
        duty_id = next(d for d in sorted(state.vehicles) if day.duties[d].type == "line"
                       and day.duties[d].start < at < day.duties[d].end)
        options = options_for(state, Breakdown(state.vehicle_at(duty_id, at), at))
        item = explain_option(state, None, options, 0)
        self.check_shape(item)
        self.assertIn(f"остальные {len(state.vehicles) - item['numbers']['touched_duties']}",
                      " ".join(item["reasons"]))
        self.assertIn("хуже", item["reasons"][-1])
        expected = {"none": 0, "idle": 1, "reserve": 2, "donor": 2}
        for i, option in enumerate(options_for(state, Breakdown(state.vehicle_at(duty_id, at), at), limit=None)):
            touched = explain_option(state, None, options_for(
                state, Breakdown(state.vehicle_at(duty_id, at), at), limit=None), i)["numbers"]["touched_duties"]
            self.assertEqual(touched, expected[option.kind], option.kind)

    def test_option_counts_duties_where_only_the_driver_changes(self):
        """Обмен сменами меняет водителей на двух нарядах, а не «0 нарядов»."""
        day = Day.load(SAMPLES / "park7_weekday.json")
        state = OpsState.from_plan(day, solve_drivers(day, solve_vehicles(day)))
        shift = min((s for s in day.shifts.values() if day.duties[s.duty_id].type == "line"
                     and s.start >= 7 * 60 + 20 and state.driver_at(s.id, s.start)),
                    key=lambda s: (s.start, s.id))
        options = options_for(state, Late(state.driver_at(shift.id, shift.start), shift.start - 10, 30),
                              limit=None)
        expected = {"swap": 2, "free_driver": 1, "reserve_driver": 2, "none": 1}
        for i, option in enumerate(options):
            item = explain_option(state, None, options, i)
            self.assertEqual(item["numbers"]["touched_duties"], expected[option.kind], option.kind)
            self.assertIn(f"Меняется нарядов: {expected[option.kind]}", " ".join(item["reasons"]))

    def test_interval(self):
        """Фактический интервал считает только наряды, где есть и автобус, и водитель."""
        day = Day.load(SAMPLES / "park7_weekday.json")
        plan = solve_drivers(day, solve_vehicles(day))
        route = next(iter(day.routes.values()))
        at = 12 * 60
        iv = interval(day, plan, route.id, at)
        self.assertEqual(iv["planned_min"], iv["actual_min"])
        self.assertEqual(iv["planned_min"], round(route.turnaround_min / iv["planned_buses"]))
        self.assertEqual(iv["planned_buses"], iv["running_buses"])

        # снимем водителей с одного наряда: автобус стоит в парке, интервал растёт
        duty = next(d for d in day.duties.values() if d.route_id == route.id
                    and d.day_type == day.day_type and d.start <= at < d.end
                    and d.id in plan.vehicles)
        thin = Plan(vehicles=dict(plan.vehicles), drivers=dict(plan.drivers))
        for shift in day.shifts_by_duty.get(duty.id, []):
            thin.drivers.pop(shift.id, None)
        after = interval(day, thin, route.id, at)
        self.assertEqual(after["planned_buses"], iv["planned_buses"])
        self.assertEqual(after["running_buses"], iv["running_buses"] - 1)
        self.assertEqual(after["actual_min"], round(route.turnaround_min / after["running_buses"]))
        self.assertGreaterEqual(after["actual_min"], after["planned_min"])

    def test_summaries(self):
        day = Day.load(SAMPLES / "park7_weekday.json")
        full = day_summary(day, solve_drivers(day, solve_vehicles(day)))
        self.assertEqual(full["reasons"], ["Все наряды и смены закрыты"])
        item = day_summary(self.day, self.plan)
        self.check_shape(item)
        self.assertEqual(item["numbers"]["line_total"], 326)

    def test_summary_counts_match_independent_count(self):
        """Цифры сводки сверяются с независимым подсчётом по плану.

        Без этого порча счётчика «водители на X из Y смен» проходит молча:
        именно эту цифру диспетчер и читает первой.
        """
        for name, day, plan in (("полный день", Day.load(SAMPLES / "park7_weekday.json"), None),
                                ("нехватка", self.day, self.plan)):
            with self.subTest(name):
                if plan is None:
                    plan = solve_drivers(day, solve_vehicles(day))
                numbers = day_summary(day, plan)["numbers"]
                lines = [d for d in day.duties.values()
                         if d.day_type == day.day_type and d.type == "line"]
                # смены считаются по всем выпущенным нарядам, включая резервные:
                # резервному наряду водитель тоже нужен
                shifts = [s for duty_id in plan.vehicles
                          for s in day.shifts_by_duty.get(duty_id, [])]
                self.assertEqual(numbers["line_total"], len(lines))
                self.assertEqual(numbers["line_filled"],
                                 sum(d.id in plan.vehicles for d in lines))
                self.assertEqual(numbers["shifts_total"], len(shifts))
                self.assertEqual(numbers["shifts_filled"],
                                 sum(s.id in plan.drivers for s in shifts))
                self.assertIn(f"{numbers['line_filled']} из {numbers['line_total']}",
                              day_summary(day, plan)["answer"])
                self.assertIn(f"{numbers['shifts_filled']} из {numbers['shifts_total']}",
                              day_summary(day, plan)["answer"])

    def test_broken_summary_counter_is_caught(self):
        """Сторож самого сторожа: порченый счётчик смен тест обязан ловить."""
        day = Day.load(SAMPLES / "park7_weekday.json")
        plan = solve_drivers(day, solve_vehicles(day))
        lost = next(iter(plan.drivers))
        thin = Plan(vehicles=dict(plan.vehicles), drivers=dict(plan.drivers))
        thin.drivers.pop(lost)
        numbers = day_summary(day, thin)["numbers"]
        self.assertEqual(numbers["shifts_filled"],
                         day_summary(day, plan)["numbers"]["shifts_filled"] - 1)


if __name__ == "__main__":
    unittest.main()


class TestIntervals(unittest.TestCase):
    """Интервалы по всем маршрутам сразу: показатель «ровные интервалы» на экране."""

    @classmethod
    def setUpClass(cls):
        cls.day = Day.load(SAMPLES / "park7_weekday.json")
        cls.plan = solve_drivers(cls.day, solve_vehicles(cls.day))
        cls.state = OpsState.from_plan(cls.day, cls.plan)

    def test_clean_day_has_no_growth_and_no_worst_route(self):
        answer = intervals(self.day, self.state)
        self.assertEqual(answer["answer"], "Интервалы как по плану")
        self.assertEqual(answer["numbers"]["worst_growth"], 1.0)
        self.assertIsNone(answer["numbers"]["worst_route"], "роста нет, винить некого")
        self.assertIsNone(answer["numbers"]["worst_at"])
        self.assertEqual(answer["numbers"]["over_25_percent"], 0)
        self.assertEqual(answer["numbers"]["stopped_routes"], 0)

    def test_routes_without_duties_today_are_left_out(self):
        answer = intervals(self.day, self.state)
        with_duties = {d.route_id for d in self.day.duties.values()
                       if d.type == "line" and d.day_type == self.day.day_type}
        self.assertEqual({r["route_id"] for r in answer["routes"]}, with_duties)
        self.assertEqual(answer["numbers"]["routes_total"], len(with_duties))
        self.assertLess(len(with_duties), len(self.day.routes), "в образце есть маршруты без нарядов")

    def test_every_route_reports_its_normal_interval(self):
        for row in intervals(self.day, self.state)["routes"]:
            self.assertEqual(row["growth"], 1.0, row)
            self.assertEqual(row["planned_min"], row["actual_min"], row)
            self.assertGreater(row["planned_min"], 0, row)
            self.assertEqual(row["planned_buses"], row["running_buses"], row)

    def test_growth_matches_the_number_we_give_the_customer(self):
        """Цифра на экране диспетчера обязана совпасть с цифрой в письме заказчику."""
        day = with_shortage(self.day, 0.2, 7, by_class=False)
        plan = solve_drivers(day, solve_vehicles(day))
        state = OpsState.from_plan(day, plan)
        by_route = {}
        for duty in day.duties.values():
            if duty.type == "line" and duty.day_type == day.day_type:
                by_route.setdefault(duty.route_id, []).append(duty)
        self.assertTrue(by_route)
        checked = 0
        for route_id, duties in by_route.items():
            ours = route_interval(day, state, route_id, duties)
            theirs = worst_growth(plan, duties)
            if theirs == float("inf"):
                self.assertGreater(ours["stopped_min"], 0, route_id)
            else:
                self.assertEqual(ours["growth"], round(theirs, 2), route_id)
                checked += 1
        self.assertGreater(checked, 10, "сверить надо не пару маршрутов")

    def test_breakdown_without_replacement_is_worse_than_with_one(self):
        duty = next(d for d in sorted(self.day.duties.values(), key=lambda d: d.id)
                    if d.type == "line" and d.start < 8 * 60 + 40 < d.end)
        event = Breakdown(self.plan.vehicles[duty.id], 8 * 60 + 40)
        options = options_for(self.state, event)
        from naryad.ops.replan import apply
        nothing = next(o for o in options if o.kind == "none")
        best = options[0]
        self.assertNotEqual(best.kind, "none", "на этом наряде должна быть замена")
        worse = intervals(self.day, apply(self.state, event, nothing))["numbers"]["worst_growth"]
        better = intervals(self.day, apply(self.state, event, best))["numbers"]["worst_growth"]
        self.assertGreater(worse, better, "замена обязана улучшить интервал")
        self.assertEqual(intervals(self.day, self.state)["numbers"]["worst_growth"], 1.0)

    def test_moment_matches_the_single_route_answer(self):
        route = sorted(r for r in self.day.routes)[0]
        whole = intervals(self.day, self.state, 8 * 60 + 30)
        row = next(r for r in whole["routes"] if r["route_id"] == route)
        single = interval(self.day, self.plan, route, 8 * 60 + 30)
        self.assertEqual((row["planned_min"], row["actual_min"]),
                         (single["planned_min"], single["actual_min"]))
        self.assertEqual(row["at"], "08:30")

    def test_time_does_not_wrap_past_midnight(self):
        """Интерфейс сверяет этот момент с отрезками состояния, где часы идут дальше 24."""
        late = max(d.end for d in self.day.duties.values()) - 10
        self.assertGreater(late, 24 * 60)
        answer = intervals(self.day, self.state, late)
        self.assertEqual(answer["routes"][0]["at"], f"{late // 60:02d}:{late % 60:02d}")
        self.assertIn(f"{late // 60:02d}:", answer["question"])

    def test_moment_outside_route_hours_is_not_a_stop(self):
        """В 03:00 нарядов нет ни у кого: это не остановка маршрутов, а ночь."""
        for t in (3 * 60, 4 * 60 + 50, 26 * 60):
            answer = intervals(self.day, self.state, t)
            self.assertEqual(answer["numbers"]["stopped_routes"], 0, t)
            self.assertEqual(answer["numbers"]["worst_growth"], 1.0, t)
            self.assertEqual(answer["reasons"],
                             [f"на всех {answer['numbers']['routes_total']} маршрутах интервал в пределах плана"], t)
            self.assertEqual(answer["answer"], "Интервалы как по плану", t)

    def test_route_that_stops_completely_is_counted_apart(self):
        day = Day.load(SAMPLES / "park7_weekday.json")
        plan = solve_drivers(day, solve_vehicles(day))
        state = OpsState.from_plan(day, plan)
        route = next(r for r, duties in
                     sorted({d.route_id: [x for x in day.duties.values() if x.route_id == d.route_id
                                          and x.type == "line" and x.day_type == day.day_type]
                             for d in day.duties.values() if d.type == "line"}.items())
                     if len(duties) <= 3)
        for duty in [d for d in day.duties.values() if d.route_id == route]:
            state.vehicles[duty.id] = []
        answer = intervals(day, state)
        row = next(r for r in answer["routes"] if r["route_id"] == route)
        self.assertGreater(row["stopped_min"], 0)
        self.assertIsNotNone(row["stopped_at"])
        self.assertGreaterEqual(answer["numbers"]["stopped_routes"], 1)
        self.assertIn("не осталось ни одного автобуса", " ".join(answer["reasons"]))
        # строка маршрута говорит про останов, а не «работают все»
        self.assertEqual(row["running_buses"], 0)
        self.assertIsNone(row["actual_min"])
        self.assertIsNone(row["growth"])
        self.assertEqual(row["at"], row["stopped_at"])
        self.assertGreater(row["planned_buses"], 0)
        # и заголовок не противоречит причинам
        self.assertNotEqual(answer["answer"], "Интервалы как по плану")
        self.assertIn(day.routes[route].number, answer["answer"])
        moment = intervals(day, state, (day.duties[next(iter(
            d.id for d in day.duties.values() if d.route_id == route))].start + 30))
        self.assertNotEqual(moment["answer"], "Интервалы как по плану")

    def test_text_says_how_many_routes_stopped_beyond_the_first_five(self):
        """Все маршруты стоят: в тексте видно, что их больше пяти, а не «остановились 5»."""
        day = Day.load(SAMPLES / "park7_weekday.json")
        state = OpsState.from_plan(day, solve_drivers(day, solve_vehicles(day)))
        for duty_id in state.vehicles:
            state.vehicles[duty_id] = []
        answer = intervals(day, state)
        total = answer["numbers"]["stopped_routes"]
        self.assertGreater(total, 5)
        self.assertEqual(total, answer["numbers"]["routes_total"])
        self.assertIn(f"и ещё {total - 5}", answer["answer"])
        self.assertIn(f"и ещё {total - 5}", " ".join(answer["reasons"]))
