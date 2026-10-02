"""Модель дня и проверка плана (А1). Запуск: python -m unittest"""

import copy
import dataclasses
import time
import unittest
from pathlib import Path

from naryad.core.invariants import check_plan, load_labor, metrics
from naryad.core.model import Day, Plan
from naryad.data.generate import generate

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "samples"
WEEKDAY = "2026-10-05"
SMALL = dict(park_count=2, release_per_park=40, routes_total=6,
             class_mix={"medium": 30, "big": 30, "extra_big": 20})


def greedy_plan(day: Day) -> Plan:
    """Простейший честный план: первый подходящий автобус и водитель.

    Это не движок (движок - задача А3), а способ получить заведомо
    допустимый план, чтобы на нём проверять саму проверку.
    """
    plan = Plan(meta={"made_by": "test-greedy"})
    used_vehicles, used_drivers, released = set(), set(), {}
    duties = sorted((d for d in day.duties.values() if d.day_type == day.day_type),
                    key=lambda d: (d.type != "line",
                                   day.routes[d.route_id].priority if d.route_id else 9, d.id))
    for duty in duties:
        park = day.parks[duty.park_id]
        if park.state == "down":
            plan.unfilled[duty.id] = "park_down"
            continue
        if released.get(park.id, 0) >= park.release(day.day_type):
            plan.unfilled[duty.id] = "release_limit"
            continue
        allowed = (day.routes[duty.route_id].allowed_classes if duty.type == "line"
                   else (duty.vehicle_class,))
        vehicle = next((v for v in day.vehicles.values()
                        if v.id not in used_vehicles and v.park_id == duty.park_id
                        and v.condition == "ok" and v.cls in allowed), None)
        if vehicle is None:
            plan.unfilled[duty.id] = "no_vehicle"
            continue
        used_vehicles.add(vehicle.id)
        released[park.id] = released.get(park.id, 0) + 1
        plan.vehicles[duty.id] = vehicle.id
        for shift in day.shifts_by_duty.get(duty.id, []):
            driver = next((d for d in day.drivers.values()
                           if d.id not in used_drivers and d.park_id == duty.park_id
                           and d.schedule == "work" and d.medical != "failed"
                           and vehicle.cls in d.classes), None)
            if driver is None:
                plan.unfilled[shift.id] = "no_driver"
                continue
            used_drivers.add(driver.id)
            plan.drivers[shift.id] = driver.id
    return plan


def codes(violations) -> set:
    return {v.code for v in violations}


class TestDay(unittest.TestCase):

    def test_reads_anton_samples(self):
        day = Day.load(SAMPLES / "park7_weekday.json")
        self.assertEqual(len(day.duties), 350)
        self.assertEqual(len(day.vehicles), 420)
        self.assertEqual(sum(len(s) for s in day.shifts_by_duty.values()), len(day.shifts))

    def test_rejects_broken_data(self):
        data = generate("case", WEEKDAY, seed=1, **SMALL)
        data["vehicles"].append(copy.deepcopy(data["vehicles"][0]))  # повтор автобуса
        with self.assertRaises(ValueError):
            Day.from_dict(data)

    def test_plan_round_trip(self):
        day = Day.from_dict(generate("case", WEEKDAY, seed=1, **SMALL))
        plan = greedy_plan(day)
        again = Plan.from_dict(plan.to_dict())
        self.assertEqual((again.vehicles, again.drivers, again.unfilled),
                         (plan.vehicles, plan.drivers, plan.unfilled))

    def test_plan_rejects_duplicates(self):
        with self.assertRaises(ValueError):
            Plan.from_dict({"vehicle_assignments": [{"duty_id": "A", "vehicle_id": "1"},
                                                    {"duty_id": "A", "vehicle_id": "2"}]})


class TestValidPlans(unittest.TestCase):
    """Честный план проходит проверку на любых данных генератора."""

    def assert_valid(self, day):
        plan = greedy_plan(day)
        violations = check_plan(day, plan)
        self.assertEqual(violations, [], "\n".join(map(str, violations[:10])))
        return plan

    def test_small(self):
        self.assert_valid(Day.from_dict(generate("case", WEEKDAY, seed=1, **SMALL)))

    def test_park7_both_days(self):
        for name in ("park7_weekday.json", "park7_weekend.json"):
            with self.subTest(name):
                self.assert_valid(Day.load(SAMPLES / name))

    def test_whole_city_fast(self):
        day = Day.from_dict(generate("case", WEEKDAY, seed=1))
        plan = greedy_plan(day)
        start = time.perf_counter()
        self.assertEqual(check_plan(day, plan), [])
        self.assertLess(time.perf_counter() - start, 2.0)

    def test_metrics(self):
        day = Day.load(SAMPLES / "park7_weekday.json")
        stats = metrics(day, greedy_plan(day))
        self.assertEqual(stats["line_duties"], 326)
        plan = greedy_plan(day)
        self.assertEqual(stats["line_filled"], sum(day.duties[d].type == "line" for d in plan.vehicles))
        self.assertEqual(sum(p["total"] for p in stats["by_priority"].values()), 326)


class TestBrokenPlans(unittest.TestCase):
    """Каждая поломка плана ловится и называется своим кодом."""

    def setUp(self):
        self.day = Day.from_dict(generate("case", WEEKDAY, seed=1, **SMALL))
        self.plan = greedy_plan(self.day)
        self.assertEqual(check_plan(self.day, self.plan), [])
        self.duty_ids = list(self.plan.vehicles)
        self.shift_ids = list(self.plan.drivers)

    def broken(self, expected: str, labor=None):
        found = codes(check_plan(self.day, self.plan, labor))
        self.assertIn(expected, found)

    def free_vehicle(self, **match):
        used = set(self.plan.vehicles.values())
        return next(v for v in self.day.vehicles.values() if v.id not in used
                    and all(getattr(v, k) == val for k, val in match.items()))

    def test_vehicle_twice(self):
        first, second = self.duty_ids[:2]
        self.plan.vehicles[second] = self.plan.vehicles[first]
        self.broken("vehicle_twice")

    def test_vehicle_class(self):
        duty = self.day.duties[self.duty_ids[0]]
        allowed = self.day.routes[duty.route_id].allowed_classes
        wrong = next(v for v in self.day.vehicles.values()
                     if v.cls not in allowed and v.park_id == duty.park_id)
        self.plan.vehicles[duty.id] = wrong.id
        self.broken("vehicle_class")

    def test_vehicle_broken(self):
        duty_id = self.duty_ids[0]
        vehicle_id = self.plan.vehicles[duty_id]
        self.day.vehicles[vehicle_id] = dataclasses.replace(
            self.day.vehicles[vehicle_id], condition="repair")
        self.broken("vehicle_broken")

    def test_vehicle_park(self):
        duty = self.day.duties[self.duty_ids[0]]
        vehicle_id = self.plan.vehicles[duty.id]
        other = next(p for p in self.day.parks if p != duty.park_id)
        self.day.vehicles[vehicle_id] = dataclasses.replace(
            self.day.vehicles[vehicle_id], park_id=other)
        self.broken("vehicle_park")

    def test_release_limit(self):
        park_id = self.day.duties[self.duty_ids[0]].park_id
        self.day.parks[park_id] = dataclasses.replace(
            self.day.parks[park_id], release_weekday=1, release_weekend=1)
        self.broken("release_limit")

    def test_park_down(self):
        park_id = self.day.duties[self.duty_ids[0]].park_id
        self.day.parks[park_id] = dataclasses.replace(self.day.parks[park_id], state="down")
        self.broken("park_down")

    def test_wrong_day_type(self):
        duty_id = self.duty_ids[0]
        other = "weekend" if self.day.day_type == "weekday" else "weekday"
        self.day.duties[duty_id] = dataclasses.replace(self.day.duties[duty_id], day_type=other)
        self.broken("wrong_day_type")

    def test_driver_without_vehicle(self):
        shift = self.day.shifts[self.shift_ids[0]]
        del self.plan.vehicles[shift.duty_id]
        self.plan.unfilled[shift.duty_id] = "no_vehicle"
        self.broken("driver_without_vehicle")

    def test_driver_permit(self):
        shift_id = self.shift_ids[0]
        driver_id = self.plan.drivers[shift_id]
        self.day.drivers[driver_id] = dataclasses.replace(self.day.drivers[driver_id], classes=())
        self.broken("driver_permit")

    def test_driver_park(self):
        shift_id = self.shift_ids[0]
        driver_id = self.plan.drivers[shift_id]
        duty = self.day.duties[self.day.shifts[shift_id].duty_id]
        other = next(p for p in self.day.parks if p != duty.park_id)
        self.day.drivers[driver_id] = dataclasses.replace(self.day.drivers[driver_id], park_id=other)
        self.broken("driver_park")

    def test_driver_not_working(self):
        driver_id = self.plan.drivers[self.shift_ids[0]]
        self.day.drivers[driver_id] = dataclasses.replace(self.day.drivers[driver_id], schedule="sick")
        self.broken("driver_not_working")

    def test_driver_medical(self):
        driver_id = self.plan.drivers[self.shift_ids[0]]
        self.day.drivers[driver_id] = dataclasses.replace(self.day.drivers[driver_id], medical="failed")
        self.broken("driver_medical")

    def test_driver_two_shifts_same_duty(self):
        duty_id = next(d for d in self.duty_ids if len(self.day.shifts_by_duty[d]) >= 2)
        first, second = self.day.shifts_by_duty[duty_id][:2]
        self.plan.drivers[second.id] = self.plan.drivers[first.id]
        self.broken("driver_too_many_shifts")

    def test_driver_overlap(self):
        first, second = (self.day.shifts_by_duty[d][0] for d in self.duty_ids[:2])
        self.plan.drivers[second.id] = self.plan.drivers[first.id]
        self.broken("driver_overlap")

    def test_driver_overtime(self):
        labor = dict(load_labor(), max_shift_min=60)
        self.broken("driver_overtime", labor)

    def test_unexplained_duty(self):
        del self.plan.vehicles[self.duty_ids[-1]]
        for shift in self.day.shifts_by_duty[self.duty_ids[-1]]:
            self.plan.drivers.pop(shift.id, None)
        self.broken("unexplained")

    def test_unexplained_shift(self):
        del self.plan.drivers[self.shift_ids[0]]
        self.broken("unexplained")

    def test_bad_reason(self):
        del self.plan.drivers[self.shift_ids[0]]
        self.plan.unfilled[self.shift_ids[0]] = "просто так"
        self.broken("bad_reason")

    def test_unfilled_but_filled(self):
        self.plan.unfilled[self.duty_ids[0]] = "no_vehicle"
        self.broken("unfilled_but_filled")

    def test_unknown_id(self):
        self.plan.vehicles["НЕТ-ТАКОГО"] = self.free_vehicle(condition="ok").id
        self.broken("unknown_id")

    def test_reserve_class(self):
        duty_id = next(d for d in self.duty_ids if self.day.duties[d].type == "reserve")
        duty = self.day.duties[duty_id]
        wrong = next(v for v in self.day.vehicles.values()
                     if v.cls != duty.vehicle_class and v.park_id == duty.park_id)
        self.plan.vehicles[duty_id] = wrong.id
        self.broken("vehicle_class")

    def test_transfer_to_other_park(self):
        duty = self.day.duties[self.duty_ids[0]]
        vehicle_id = self.plan.vehicles[duty.id]
        parks = sorted(self.day.parks)
        other = next(p for p in parks if p != duty.park_id)
        self.day.vehicles[vehicle_id] = dataclasses.replace(self.day.vehicles[vehicle_id], park_id=other)
        self.plan.transfers[vehicle_id] = other  # переброшен, но не в парк этого наряда
        self.broken("vehicle_park")
        self.plan.transfers[vehicle_id] = duty.park_id
        self.assertNotIn("vehicle_park", codes(check_plan(self.day, self.plan)))

    def test_unknown_driver_vehicle_shift(self):
        for spoil in (lambda: self.plan.drivers.__setitem__(self.shift_ids[0], "НЕТ-ВОДИТЕЛЯ"),
                      lambda: self.plan.vehicles.__setitem__(self.duty_ids[0], "НЕТ-АВТОБУСА"),
                      lambda: self.plan.drivers.__setitem__("НЕТ-СМЕНЫ", self.plan.drivers[self.shift_ids[1]])):
            self.setUp()
            spoil()
            self.broken("unknown_id")

    def test_shift_exactly_at_limit(self):
        shift = self.day.shifts[self.shift_ids[0]]
        self.day.shifts[shift.id] = dataclasses.replace(shift, end=shift.start + 600)
        self.assertNotIn("driver_overtime", codes(check_plan(self.day, self.plan)))
        self.day.shifts[shift.id] = dataclasses.replace(shift, end=shift.start + 601)
        self.broken("driver_overtime")


if __name__ == "__main__":
    unittest.main()
