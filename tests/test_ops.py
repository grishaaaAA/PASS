"""Оперативный пересчёт (А5). Запуск: python -m unittest"""

import dataclasses
import time
import unittest

from naryad.core.model import Day
from naryad.data.generate import generate
from naryad.ops.replan import (SUPPLY_MIN, Accident, Breakdown, NoShow, OpsState, apply,
                               check_state, options_for)
from naryad.ops.scenarios import build, run
from naryad.solve.drivers import History, day_base, solve_drivers
from naryad.solve.vehicles import solve_vehicles

from tests.test_core import SAMPLES, WEEKDAY

MORNING = 8 * 60 + 40


def morning_state(day=None, history=None):
    day = day or Day.load(SAMPLES / "park7_weekday.json")
    return OpsState.from_plan(day, solve_drivers(day, solve_vehicles(day)), history)


def important_running(state, t=MORNING):
    day = state.day
    return next(d for d in sorted(day.duties.values(), key=lambda d: d.id)
                if d.type == "line" and day.routes[d.route_id].priority == 1
                and d.start < t < d.end - 60 and state.vehicle_at(d.id, t))


class TestBreakdown(unittest.TestCase):

    def setUp(self):
        self.state = morning_state()
        self.duty = important_running(self.state)
        self.event = Breakdown(self.state.vehicle_at(self.duty.id, MORNING), MORNING)

    def test_morning_state_valid(self):
        self.assertEqual(check_state(self.state), [])

    def test_options_sorted_and_useful(self):
        options = options_for(self.state, self.event)
        self.assertLessEqual(len(options), 3)
        self.assertEqual([o.cost for o in options], sorted(o.cost for o in options))
        self.assertNotEqual(options[0].kind, "none")
        self.assertEqual(options[0].lost_minutes, SUPPLY_MIN)

    def test_apply_keeps_day_valid_and_stable(self):
        after = apply(self.state, self.event, options_for(self.state, self.event)[0])
        self.assertEqual(check_state(after), [])
        changed = [k for k in self.state.vehicles if after.vehicles[k] != self.state.vehicles[k]]
        self.assertEqual(changed, [self.duty.id])  # остальные наряды не тронуты
        self.assertIsNotNone(after.vehicle_at(self.duty.id, MORNING + SUPPLY_MIN))

    def test_short_breakdown_wait(self):
        event = Breakdown(self.event.vehicle_id, MORNING, duration=20)
        self.assertEqual(options_for(self.state, event)[0].kind, "none")

    def test_no_spare_buses_takes_from_less_important_route(self):
        day = self.state.day
        busy = {s.who for segs in self.state.vehicles.values() for s in segs}
        for v in list(day.vehicles.values()):  # в парке нет свободных автобусов
            if v.id not in busy:
                day.vehicles[v.id] = dataclasses.replace(v, condition="repair")
        state = self.state
        for reserve in [d for d in day.duties.values() if d.type == "reserve"]:
            for segment in state.vehicles.pop(reserve.id, []):  # резерв ушёл на другие сходы
                state.down_vehicles[segment.who] = 0
        options = options_for(state, self.event, limit=None)
        kinds = [o.kind for o in options]
        self.assertNotIn("reserve", kinds)
        self.assertNotIn("idle", kinds)
        self.assertEqual(options[0].kind, "donor")
        donor_route = next(r for r in day.routes.values()
                           if options[0].title.endswith(f"маршрута {r.number}"))
        self.assertGreater(donor_route.priority, 1)  # важный маршрут не грабим
        after = apply(state, self.event, options[0])
        self.assertEqual(check_state(after), [])


class TestAccidentAndDriver(unittest.TestCase):

    def test_accident_driver_out(self):
        state = morning_state()
        duty = important_running(state, 17 * 60 + 30)
        t = 17 * 60 + 30
        shift = state.shift_at(duty.id, t)
        driver = state.driver_at(shift.id, t)
        event = Accident(state.vehicle_at(duty.id, t), t)
        after = apply(state, event, options_for(state, event)[0])
        self.assertEqual(check_state(after), [])
        self.assertIn(driver, after.down_drivers)
        self.assertTrue(all(s.end <= t for s in after.driver_worked(driver)))

    def test_no_show_before_shift_loses_nothing(self):
        state = morning_state()
        shift = next(s for s in sorted(state.day.shifts.values(), key=lambda s: (s.start, s.id))
                     if state.driver_at(s.id, s.start) and s.start >= 5 * 60)
        event = NoShow(state.driver_at(shift.id, shift.start), shift.start - 20)
        best = options_for(state, event)[0]
        self.assertEqual(best.lost_minutes, 0)
        after = apply(state, event, best)
        self.assertEqual(check_state(after), [])
        self.assertNotEqual(after.driver_at(shift.id, shift.start), event.driver_id)

    def test_tired_driver_not_offered(self):
        day = Day.load(SAMPLES / "park7_weekday.json")
        first = morning_state(day)
        shift = next(s for s in sorted(day.shifts.values(), key=lambda s: (s.start, s.id))
                     if first.driver_at(s.id, s.start) and s.start >= 5 * 60)
        event = NoShow(first.driver_at(shift.id, shift.start), shift.start - 20)
        offered = options_for(first, event)[0].title
        tab = offered.split()[-1]
        tired = next(d for d in day.drivers.values() if d.tab_number == tab)
        history = History()
        history.get(tired.id).last_end = day_base(day) - 60  # закончил в 23:00 накануне
        history.get(tired.id).last_length = 8 * 60
        again = OpsState.from_plan(day, solve_drivers(day, solve_vehicles(day)), history)
        self.assertNotIn(tab, " ".join(o.title for o in options_for(again, event, limit=None)))


class TestScenarios(unittest.TestCase):

    def test_five_scenarios(self):
        day = Day.load(SAMPLES / "park7_weekday.json")
        scenarios = build(day)
        self.assertEqual(len(scenarios), 5)
        for name, events in scenarios.items():
            with self.subTest(name):
                result = run(day, events)  # внутри - check_state после каждого события
                self.assertLess(result["max_seconds"], 1.0)
        frost = run(day, scenarios["Мороз: 15 автобусов не вышли к 06:30"])
        self.assertEqual(len(frost["options"]), 15)
        self.assertGreater(frost["avoided_minutes"], 0)

    def test_whole_city_event_fast(self):
        day = Day.from_dict(generate("case", WEEKDAY, seed=1))
        state = morning_state(day)
        duty = important_running(state)
        start = time.perf_counter()
        options = options_for(state, Breakdown(state.vehicle_at(duty.id, MORNING), MORNING))
        self.assertLess(time.perf_counter() - start, 2.0)
        self.assertTrue(options)


if __name__ == "__main__":
    unittest.main()
