"""Объяснения решений (А6). Запуск: python -m unittest"""

import dataclasses
import json
import random
import time
import unittest

from naryad.core.model import Day
from naryad.explain import (day_summary, explain_option, interval, why_driver, why_unfilled,
                            why_vehicle)
from naryad.ops.replan import Breakdown, OpsState, options_for
from naryad.solve.compare import with_shortage
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
        shift_id = next(k for k, v in self.plan.unfilled.items() if v == "no_driver")
        item = why_unfilled(self.day, self.plan, shift_id)
        self.check_shape(item)
        self.assertEqual(item["numbers"]["drivers"], len(self.day.drivers))

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
        history = History()
        history.get(driver_id).last_end = day_base(day) + day.shifts[shift_id].start - 6 * 60
        history.get(driver_id).last_length = 8 * 60
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

    def test_interval(self):
        day = Day.load(SAMPLES / "park7_weekday.json")
        plan = solve_vehicles(day)
        route = next(iter(day.routes.values()))
        iv = interval(day, plan, route.id, 12 * 60)
        self.assertEqual(iv["planned_min"], iv["actual_min"])
        self.assertEqual(iv["planned_min"], round(route.turnaround_min / iv["planned_buses"]))

    def test_summaries(self):
        day = Day.load(SAMPLES / "park7_weekday.json")
        full = day_summary(day, solve_drivers(day, solve_vehicles(day)))
        self.assertEqual(full["reasons"], ["Все наряды и смены закрыты"])
        item = day_summary(self.day, self.plan)
        self.check_shape(item)
        self.assertEqual(item["numbers"]["line_total"], 326)


if __name__ == "__main__":
    unittest.main()
