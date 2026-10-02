"""Оперативный пересчёт (А5). Запуск: python -m unittest"""

import copy
import dataclasses
import time
import unittest

from naryad.core.model import Day
from naryad.data.generate import generate
from naryad.ops.replan import (SUPPLY_MIN, TRANSFER_MIN, Accident, Breakdown, Losses, NoShow,
                               OpsState, Segment, _cut, _end, apply, check_state, options_for)
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


class TestBreakdownForAWhile(unittest.TestCase):
    """Сход на время: замена работает только до возвращения своего автобуса."""

    def setUp(self):
        self.state = morning_state()
        self.duty = important_running(self.state)
        self.bus = self.state.vehicle_at(self.duty.id, MORNING)
        self.shift = self.state.shift_at(self.duty.id, MORNING)
        self.driver = self.state.driver_at(self.shift.id, MORNING)
        self.back = MORNING + 90
        self.assertGreater(self.shift.end, self.back)

    def test_every_option_one_bus_and_own_bus_back(self):
        event = Breakdown(self.bus, MORNING, duration=90)
        options = options_for(self.state, event, limit=None)
        self.assertEqual({"none", "reserve", "idle"} - {o.kind for o in options}, set())
        for option in options:
            with self.subTest(option.kind):
                after = apply(self.state, event, option)
                self.assertEqual(check_state(after), [])  # в т.ч. не два автобуса на наряде
                self.assertEqual(after.vehicle_at(self.duty.id, self.back), self.bus)
                self.assertEqual(after.driver_at(self.shift.id, self.back), self.driver)
                if option.kind == "none":
                    continue
                spare = after.vehicle_at(self.duty.id, self.back - 1)
                self.assertNotIn(spare, (None, self.bus))
                self.assertEqual(after.duty_of_vehicle(spare, self.back), None)

    def test_reserve_comes_back_for_next_breakdown(self):
        event = Breakdown(self.bus, MORNING, duration=90)
        reserve = next(o for o in options_for(self.state, event, limit=None) if o.kind == "reserve")
        reserve_duty = reserve.changes[0][1]
        spare = self.state.vehicle_at(reserve_duty, MORNING)
        after = apply(self.state, event, reserve)
        self.assertEqual(after.vehicle_at(reserve_duty, self.back + SUPPLY_MIN), spare)
        later = self.back + SUPPLY_MIN + 10
        other = next(d for d in sorted(after.day.duties.values(), key=lambda d: d.id)
                     if d.route_id == self.duty.route_id and d.id != self.duty.id
                     and d.end > later + 60 and after.vehicle_at(d.id, later))
        second = options_for(after, Breakdown(after.vehicle_at(other.id, later), later), limit=None)
        self.assertIn(f"Резерв: автобус {after.day.vehicles[spare].board_number}",
                      [o.title for o in second])

    def test_repair_longer_than_duty(self):
        duty = min((d for d in self.state.day.duties.values() if d.type == "line"
                    and self.state.vehicle_at(d.id, d.end - 30)), key=lambda d: (d.end - d.start, d.id))
        bus = self.state.vehicle_at(duty.id, duty.end - 30)
        event = Breakdown(bus, duty.end - 30, duration=60)
        after = apply(self.state, event, options_for(self.state, event, limit=None)[-1])
        self.assertEqual(check_state(after), [])
        self.assertIn(bus, after.down_vehicles)  # после ремонта его наряд уже кончился


class TestLossPrice(unittest.TestCase):
    """Одна формула цены простоя для схода, неявки и донора."""

    def setUp(self):
        self.state = morning_state()
        self.duty = important_running(self.state)

    def test_no_show_costs_like_breakdown(self):
        s, d = self.state, self.duty
        shift = s.shift_at(d.id, MORNING)
        broken = Losses(_cut(s, Breakdown(s.vehicle_at(d.id, MORNING), MORNING)))
        absent = Losses(_cut(s, NoShow(s.driver_at(shift.id, MORNING), MORNING)))
        for end in (MORNING + 30, MORNING + 60, shift.end):  # после пересменки наряд с неявкой снова едет
            self.assertAlmostEqual(broken.cost(d.id, MORNING, end), absent.cost(d.id, MORNING, end))

    def test_donor_priced_like_its_own_breakdown(self):
        s = self.state
        donor = next(d for d in sorted(s.day.duties.values(), key=lambda d: d.id)
                     if d.type == "line" and d.route_id != self.duty.route_id
                     and d.end > MORNING + 120 and s.vehicle_at(d.id, MORNING))
        planned = Losses(s).cost(donor.id, MORNING, MORNING + 120, remove=True)
        real = Losses(_cut(s, Breakdown(s.vehicle_at(donor.id, MORNING), MORNING)))
        self.assertAlmostEqual(planned, real.cost(donor.id, MORNING, MORNING + 120))

    def test_each_next_loss_costs_more(self):
        """Чем меньше автобусов осталось на маршруте, тем дороже час; маршрут встал - дороже всего."""
        state = copy.deepcopy(self.state)
        route = [d for d in sorted(state.day.duties.values(), key=lambda d: d.id)
                 if d.route_id == self.duty.route_id and d.end > MORNING + 60
                 and state.vehicle_at(d.id, MORNING)]
        hour = []
        for d in route:
            hour.append(Losses(state).cost(d.id, MORNING, MORNING + 60, remove=True))
            _end(state.vehicles, d.id, MORNING)
        self.assertGreater(len(route), 3)
        self.assertTrue(all(a < b for a, b in zip(hour, hour[1:])), hour)
        self.assertGreater(hour[-1], 2 * hour[-2])  # полная остановка - отдельный штраф

    def test_donor_never_stops_equal_route(self):
        """Ранним утром на маршрутах по 1-2 автобуса: последний автобус равного маршрута не снимаем."""
        state = morning_state()
        day, t = state.day, 4 * 60 + 55
        busy = {s.who for segs in state.vehicles.values() for s in segs}
        for v in list(day.vehicles.values()):  # ни резерва, ни свободных автобусов
            if v.id not in busy:
                day.vehicles[v.id] = dataclasses.replace(v, condition="repair")
        for reserve in [d for d in day.duties.values() if d.type == "reserve"]:
            for segment in state.vehicles.pop(reserve.id, []):
                state.down_vehicles[segment.who] = 0

        def running(route_id):
            return sum(1 for d in day.duties.values() if d.route_id == route_id
                       and any(a <= t < b for a, b in state.running(d.id)))

        lonely = {r for r in day.routes if running(r) == 1}
        checked = 0
        for duty in sorted(day.duties.values(), key=lambda d: d.id):
            if duty.type != "line" or not state.vehicle_at(duty.id, t):
                continue
            priority = day.routes[duty.route_id].priority
            if not any(day.routes[r].priority <= priority and r != duty.route_id for r in lonely):
                continue
            checked += 1
            for option in options_for(state, Breakdown(state.vehicle_at(duty.id, t), t), limit=None):
                if option.kind == "donor":
                    donor_route = day.duties[option.changes[0][1]].route_id
                    self.assertFalse(donor_route in lonely
                                     and day.routes[donor_route].priority <= priority, option.title)
        self.assertGreater(checked, 0)


class TestCheckState(unittest.TestCase):
    """Проверка состояния ловит каждую поломку."""

    def setUp(self):
        self.state = morning_state()
        self.duty = important_running(self.state)

    def broken(self, spoil):
        state = copy.deepcopy(self.state)
        spoil(state)
        return " | ".join(check_state(state))

    def test_each_problem_found(self):
        s, duty, t = self.state, self.duty, MORNING
        day = s.day
        bus = s.vehicle_at(duty.id, t)
        shift = s.shift_at(duty.id, t)
        driver = s.driver_at(shift.id, t)
        used = {x.who for segs in s.vehicles.values() for x in segs}
        spare = next(v.id for v in sorted(day.vehicles.values(), key=lambda v: v.id)
                     if v.condition == "ok" and v.park_id == duty.park_id and v.id not in used)
        working = {x.who for segs in s.drivers.values() for x in segs}
        idle = next(d.id for d in sorted(day.drivers.values(), key=lambda d: d.id)
                    if d.park_id == duty.park_id and d.id not in working)
        other = next(d for d in sorted(day.duties.values(), key=lambda d: d.id)
                     if d.id != duty.id and d.type == "line" and s.vehicle_at(d.id, t))
        late = max((x for x in day.shifts.values() if s.driver_at(x.id, x.start)
                    and x.duty_id != duty.id), key=lambda x: (x.end, x.id))

        def two_buses(st):
            st.vehicles[duty.id].append(Segment(t, t + 60, spare))

        def two_drivers(st):
            st.drivers[shift.id].append(Segment(t, t + 60, idle))

        def bus_twice(st):
            st.vehicles[other.id] = [Segment(t, t + 60, bus)]

        def outside(st):
            st.vehicles[duty.id].append(Segment(duty.end, duty.end + 30, spare))

        def empty(st):
            st.vehicles[other.id].append(Segment(t, t, spare))

        def down(st):
            st.down_vehicles[bus] = t

        def long_day(st):
            st.drivers[late.id] = [Segment(late.start, late.end, driver)]

        def tired(st):
            st.history.get(driver).last_end = day_base(day) + shift.start - 60
            st.history.get(driver).last_length = 8 * 60

        for spoil, text in ((two_buses, "сразу"), (two_drivers, "сразу"), (bus_twice, "одновременно"),
                            (outside, "вне времени"), (empty, "пустой"), (down, "выбыл"),
                            (long_day, "больше нормы"), (tired, "не отдохнул")):
            with self.subTest(spoil.__name__):
                self.assertIn(text, self.broken(spoil))


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
