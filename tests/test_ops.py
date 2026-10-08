"""Оперативный пересчёт (А5). Запуск: python -m unittest"""

import copy
import dataclasses
import time
import unittest

from naryad.core.model import Day
from naryad.data.generate import generate
from naryad.ops.replan import (SUPPLY_MIN, SWAP_WINDOW_MIN, TRANSFER_MIN, Accident, Breakdown, Late,
                               Losses, NoShow, late_shift,
                               OpsState, Segment, _cut, _end, apply, check_state, free_drivers,
                               options_for)
from naryad.core.invariants import load_labor
from naryad.ops.scenarios import build, run
from naryad.solve.drivers import History, day_base, rest_status, solve_drivers
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
        """До трёх лучших вариантов плюс «не заменять» как точка отсчёта (docs/CONTRACT.md)."""
        options = options_for(self.state, self.event)
        self.assertLessEqual(len(options), 4)
        self.assertLessEqual(sum(o.kind != "none" for o in options), 3)
        self.assertEqual([o.kind for o in options].count("none"), 1,
                         "вариант «не заменять» должен быть в ответе всегда")
        real = [o.cost for o in options if o.kind != "none"]
        self.assertEqual(real, sorted(real))
        self.assertNotEqual(options[0].kind, "none")
        self.assertEqual(options[0].lost_minutes, SUPPLY_MIN)
        nothing = next(o for o in options if o.kind == "none")
        self.assertGreaterEqual(nothing.lost_minutes, options[0].lost_minutes)

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
        return " | ".join(v.text for v in check_state(state))

    def test_state_problems_are_violation_objects(self):
        """Дневная проверка отдаёт такие же объекты, как утренняя: код, текст, id."""
        from naryad.core.invariants import Violation
        state = copy.deepcopy(self.state)
        duty = self.duty
        bus = state.vehicles[duty.id][0]
        state.vehicles[duty.id].append(Segment(bus.start, bus.end, bus.who))  # автобус дважды
        found = check_state(state)
        self.assertTrue(found)
        self.assertTrue(all(isinstance(v, Violation) for v in found))
        self.assertIn("vehicle_twice", {v.code for v in found})
        self.assertTrue(all(v.text for v in found))
        self.assertIn(bus.who, {i for v in found for i in v.ids})

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

        def wrong_class(st):
            allowed = day.routes[duty.route_id].allowed_classes
            st.vehicles[duty.id] = [Segment(duty.start, duty.end, next(
                v.id for v in sorted(day.vehicles.values(), key=lambda v: v.id)
                if v.cls not in allowed and v.park_id == duty.park_id and v.id not in used))]

        def foreign_driver(st):
            st.day.drivers[driver] = dataclasses.replace(day.drivers[driver], park_id="P99")

        def sick_driver(st):
            st.day.drivers[driver] = dataclasses.replace(day.drivers[driver], schedule="sick")

        def no_permit(st):
            st.day.drivers[driver] = dataclasses.replace(day.drivers[driver], classes=())

        def tired(st):
            st.history.get(driver).last_end = day_base(day) + shift.start - 60
            st.history.get(driver).last_length = 8 * 60

        def in_repair(st):
            st.repairs[bus] = [(t, t + 60)]

        def moved_away(st):
            st.transfers[bus] = "P99"   # утром отдан другому парку, а стоит дома

        for spoil, text in ((two_buses, "сразу"), (two_drivers, "сразу"), (bus_twice, "одновременно"),
                            (outside, "вне времени"), (empty, "пустой"), (down, "выбыл"),
                            (long_day, "больше нормы"), (tired, "не отдохнул"),
                            (wrong_class, "не подходит"), (foreign_driver, "из парка"),
                            (sick_driver, "не работает сегодня"), (no_permit, "нет допуска"),
                            (in_repair, "в ремонте"), (moved_away, "переброшен в парк P99")):
            with self.subTest(spoil.__name__):
                self.assertIn(text, self.broken(spoil))


class TestFreeDrivers(unittest.TestCase):

    def test_park_and_length_filters(self):
        state = morning_state()
        duty = important_running(state)
        found = free_drivers(state, duty, MORNING, MORNING + 60, load_labor())
        self.assertTrue(found)
        self.assertTrue(all(state.day.drivers[d].park_id == duty.park_id for d in found))
        moved = found[0]
        state.day.drivers[moved] = dataclasses.replace(state.day.drivers[moved], park_id="P99")
        self.assertNotIn(moved, free_drivers(state, duty, MORNING, MORNING + 60, load_labor()))
        self.assertEqual(free_drivers(state, duty, MORNING, MORNING + 11 * 60, load_labor()), [])

    def test_several_classes_need_a_permit_to_each(self):
        state = morning_state()
        duty = important_running(state)
        both = free_drivers(state, duty, MORNING, MORNING + 60, load_labor(), classes=("big", "extra_big"))
        self.assertTrue(both)
        self.assertTrue(all({"big", "extra_big"} <= set(state.day.drivers[d].classes) for d in both))
        only_big = free_drivers(state, duty, MORNING, MORNING + 60, load_labor(), "big")
        self.assertTrue(set(both) < set(only_big), "допуск к двум классам строже, чем к одному")
        self.assertEqual(len(state.history.drivers), 0, "подбор кандидатов не пишет в память водителей")


class TestEveryOption(unittest.TestCase):
    """Любой предложенный вариант, а не только лучший, даёт допустимое состояние дня."""

    def check_all(self, state, event):
        options = options_for(state, event, limit=None)
        self.assertTrue(options)
        for option in options:
            with self.subTest(option.title):
                self.assertEqual(check_state(apply(state, event, option)), [])
                if option.kind == "donor":
                    donor = state.day.duties[option.changes[0][1]]
                    self.assertNotEqual(donor.route_id, state.day.duties[state.duty_of_vehicle(
                        event.vehicle_id, event.at)].route_id)
        return options

    def test_park7_both_classes_and_no_show(self):
        state = morning_state()
        day = state.day
        for cls in ("big", "extra_big"):
            duty = next(d for d in sorted(day.duties.values(), key=lambda d: d.id) if d.type == "line"
                        and d.vehicle_class == cls and d.start < MORNING < d.end - 60
                        and state.vehicle_at(d.id, MORNING))
            self.check_all(state, Breakdown(state.vehicle_at(duty.id, MORNING), MORNING))
        shift = next(s for s in sorted(day.shifts.values(), key=lambda s: (s.start, s.id))
                     if state.driver_at(s.id, s.start) and s.start >= 5 * 60)
        options = self.check_all(state, NoShow(state.driver_at(shift.id, shift.start), shift.start - 20))
        self.assertIn("reserve_driver", [o.kind for o in options])
        # водитель резерва с допуском только к классу своего резервного автобуса
        # не предлагается на наряд другого класса
        reserve_shift = next(o for o in options if o.kind == "reserve_driver").changes[0][1]
        reserve_driver = state.driver_at(reserve_shift, day.shifts[reserve_shift].start)
        own_cls = day.vehicles[state.vehicle_at(day.shifts[reserve_shift].duty_id,
                                                 day.shifts[reserve_shift].start)].cls
        day.drivers[reserve_driver] = dataclasses.replace(day.drivers[reserve_driver], classes=(own_cls,))
        other = next(s for s in sorted(day.shifts.values(), key=lambda s: (s.start, s.id))
                     if s.start >= day.shifts[reserve_shift].start and state.driver_at(s.id, s.start)
                     and day.duties[s.duty_id].type == "line"
                     and day.vehicles[state.vehicle_at(s.duty_id, s.start)].cls != own_cls)
        for option in self.check_all(state, NoShow(state.driver_at(other.id, other.start), other.start - 20)):
            self.assertFalse(option.kind == "reserve_driver" and option.changes[-1][2].who == reserve_driver)

    def test_city_no_spares(self):
        day = Day.from_dict(generate("case", WEEKDAY, seed=1))
        state = morning_state(day)
        busy = {s.who for segs in state.vehicles.values() for s in segs}
        for v in list(day.vehicles.values()):  # без свободных автобусов: остаются резерв и доноры
            if v.id not in busy:
                day.vehicles[v.id] = dataclasses.replace(v, condition="repair")
        duty = important_running(state)
        kinds = [o.kind for o in self.check_all(state, Breakdown(state.vehicle_at(duty.id, MORNING), MORNING))]
        self.assertIn("reserve", kinds)
        for reserve in [d for d in day.duties.values() if d.type == "reserve" and d.park_id == duty.park_id]:
            for segment in state.vehicles.pop(reserve.id, []):  # свой резерв кончился - чужой не берём
                state.down_vehicles[segment.who] = 0
        kinds = [o.kind for o in self.check_all(state, Breakdown(state.vehicle_at(duty.id, MORNING), MORNING))]
        self.assertNotIn("reserve", kinds)


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


class TestLate(unittest.TestCase):
    """Опоздание водителя: ждать, обмен сменами, замена. Так, как описал директор парка."""

    def setUp(self):
        self.state = morning_state()
        day = self.state.day
        self.shift = min((s for s in day.shifts.values()
                          if day.duties[s.duty_id].type == "line" and s.start >= 7 * 60 + 20
                          and self.state.driver_at(s.id, s.start)), key=lambda s: (s.start, s.id))
        self.driver = self.state.driver_at(self.shift.id, self.shift.start)
        self.event = Late(self.driver, self.shift.start - 10, 30)

    def kinds(self, event=None):
        return {o.kind: o for o in options_for(self.state, event or self.event, limit=None)}

    def test_every_option_is_legal(self):
        for option in options_for(self.state, self.event, limit=None):
            self.assertEqual(check_state(apply(self.state, self.event, option)), [], option.title)

    def test_wait_starts_the_shift_when_the_driver_comes(self):
        wait = self.kinds()["none"]
        self.assertEqual(wait.title, "Ждать водителя")
        self.assertEqual(wait.lost_minutes, 30)
        after = apply(self.state, self.event, wait)
        segments = after.drivers[self.shift.id]
        self.assertEqual([(s.start, s.who) for s in segments], [(self.shift.start + 30, self.driver)])
        self.assertNotIn(self.driver, after.down_drivers, "опоздавший из дня не выбывает")

    def test_swap_exchanges_two_shifts_and_nobody_waits(self):
        swap = self.kinds()["swap"]
        self.assertEqual(swap.lost_minutes, 0)
        self.assertIn("если он уже в парке", swap.note)
        after = apply(self.state, self.event, swap)
        (taken,) = after.drivers[self.shift.id]
        self.assertEqual(taken.start, self.shift.start, "смена опоздавшего выходит вовремя")
        other = next(k for k, segs in after.drivers.items()
                     if any(s.who == self.driver for s in segs))
        start = self.state.day.shifts[other].start
        self.assertEqual(self.state.driver_at(other, start), taken.who, "это смена того, кто выехал раньше")
        self.assertGreaterEqual(start, self.shift.start + 30, "опоздавший успевает на новую смену")
        self.assertLessEqual(start, self.shift.start + SWAP_WINDOW_MIN)

    def test_swap_is_cheaper_than_waiting(self):
        options = self.kinds()
        self.assertLess(options["swap"].cost, options["none"].cost)

    def test_no_swap_when_the_driver_comes_too_late(self):
        far = Late(self.driver, self.event.at, SWAP_WINDOW_MIN + 10)
        self.assertNotIn("swap", self.kinds(far))
        self.assertIn("none", self.kinds(far))

    def test_late_driver_is_not_offered_as_his_own_replacement(self):
        for option in options_for(self.state, self.event, limit=None):
            if option.kind == "free_driver":
                self.assertNotIn(self.driver, [v.who for _, _, v in option.changes if hasattr(v, "who")])

    def test_shift_that_already_started_cannot_be_late(self):
        self.assertIsNone(late_shift(self.state, self.driver, self.shift.start + 1)
                          if not any(s.start > self.shift.start for segs in self.state.drivers.values()
                                     for s in segs if s.who == self.driver) else None)
        self.assertEqual(late_shift(self.state, self.driver, self.event.at),
                         (self.shift.id, self.shift.start))

    def test_swap_respects_rest_after_yesterday(self):
        """Водитель, который при раннем выходе не отдохнул бы, в обмен не годится.

        План тот же, меняется только память о вчерашней смене напарника:
        к его собственной смене отдых законный, к смене опоздавшего - нет.
        """
        swap = self.kinds()["swap"]
        partner = next(v.who for kind, key, v in swap.changes if kind == "driver" and key == self.shift.id)
        own_start = min(s.start for segs in self.state.drivers.values() for s in segs if s.who == partner)
        tired = copy.deepcopy(self.state)
        base, labor = day_base(tired.day), load_labor()
        known = tired.history.peek(partner)
        # ищем вчерашний конец смены: к своей смене отдых законный, к смене опоздавшего - нет
        last_end = next(end for end in range(base + self.shift.start - 30 * 60, base + self.shift.start)
                        if rest_status(dataclasses.replace(known, last_end=end, last_length=480),
                                       base + self.shift.start, labor) is None
                        and rest_status(dataclasses.replace(known, last_end=end, last_length=480),
                                        base + own_start, labor) is not None)
        tired.history.drivers[partner] = dataclasses.replace(known, last_end=last_end, last_length=480)
        self.assertEqual(check_state(tired), [], "его собственная смена остаётся законной")
        again = {o.kind: o for o in options_for(tired, self.event, limit=None)}
        if "swap" in again:
            chosen = next(v.who for kind, key, v in again["swap"].changes
                          if kind == "driver" and key == self.shift.id)
            self.assertNotEqual(chosen, partner, "не отдохнувшего водителя нельзя ставить раньше")
            self.assertEqual(check_state(apply(tired, self.event, again["swap"])), [])


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


class TestSegmentDiscipline(unittest.TestCase):
    """Дисциплина отрезков: ресурс не ставится поверх уже занятого.

    Три сценария из проверки агентами: второй сход на том же наряде ставил
    второй автобус поверх уже вернувшегося своего; выбывший автобус
    оставался в плане на другом наряде; резервного водителя отдавали на два
    наряда. Плюс общее свойство: у водителя есть допуск к классу того
    автобуса, который он реально ведёт.
    """

    @classmethod
    def setUpClass(cls):
        cls.base = morning_state()
        cls.duty = important_running(cls.base)

    def assertLegal(self, state, what=""):
        found = check_state(state)
        self.assertEqual(found, [], f"{what}: {[v.text for v in found]}")

    def assertPermits(self, state, what=""):
        """У каждого водителя есть допуск к классу автобуса на его наряде."""
        day = state.day
        for shift_id, segments in state.drivers.items():
            duty_id = day.shifts[shift_id].duty_id
            for seg in segments:
                for bus in state.vehicles.get(duty_id, []):
                    if bus.start < seg.end and seg.start < bus.end:
                        self.assertIn(day.vehicles[bus.who].cls, day.drivers[seg.who].classes,
                                      f"{what}: {seg.who} ведёт {bus.who}")

    def test_second_breakdown_on_the_same_duty(self):
        first = Breakdown(self.base.vehicle_at(self.duty.id, MORNING), MORNING, 60)
        after = apply(self.base, first, options_for(self.base, first)[0])
        self.assertLegal(after, "первый сход")
        own_back = [s for s in after.vehicles[self.duty.id] if s.start > MORNING]
        self.assertTrue(own_back, "свой автобус должен вернуться")
        later = MORNING + 40
        second_vehicle = after.vehicle_at(self.duty.id, later)
        if second_vehicle is None:
            self.skipTest("на этом наряде подмены в это время нет")
        for duration in (None, 20, 90):
            event = Breakdown(second_vehicle, later, duration)
            for option in options_for(after, event, limit=None):
                state = apply(after, event, option)
                self.assertLegal(state, f"второй сход, {option.kind}")
                self.assertPermits(state, f"второй сход, {option.kind}")

    def test_departed_vehicle_leaves_every_duty(self):
        event = Breakdown(self.base.vehicle_at(self.duty.id, MORNING), MORNING, 120)
        reserve = next((o for o in options_for(self.base, event, limit=None) if o.kind == "reserve"), None)
        if reserve is None:
            self.skipTest("вариант с резервом не предложен")
        after = apply(self.base, event, reserve)
        self.assertLegal(after, "замена резервом")
        on_line = after.vehicle_at(self.duty.id, MORNING + 40)
        elsewhere = [k for k, segs in after.vehicles.items()
                     if k != self.duty.id and any(s.who == on_line for s in segs)]
        self.assertTrue(elsewhere, "резервный автобус должен вернуться в свой наряд")
        crash = Accident(on_line, MORNING + 60)
        state = apply(after, crash, options_for(after, crash)[0])
        self.assertLegal(state, "ДТП с резервным автобусом")
        for key, segs in state.vehicles.items():
            for seg in segs:
                self.assertFalse(seg.who == on_line and seg.end > MORNING + 60,
                                 f"выбывший автобус остался на {key}")

    def test_reserve_driver_is_not_given_twice(self):
        shift = self.base.shift_at(self.duty.id, MORNING)
        event = NoShow(self.base.driver_at(shift.id, MORNING), MORNING)
        chosen = next((o for o in options_for(self.base, event, limit=None)
                       if o.kind == "reserve_driver"), None)
        if chosen is None:
            self.skipTest("вариант с водителем резерва не предложен")
        after = apply(self.base, event, chosen)
        self.assertLegal(after, "неявка закрыта водителем резерва")
        later = MORNING + 40
        reserve_driver = next(s.who for segs in after.drivers.values() for s in segs
                              if s.start <= later < s.end
                              and s.who in {c[2].who for c in chosen.changes if c[0] == "driver"})
        other = next(d for d in sorted(self.base.day.duties.values(), key=lambda d: d.id)
                     if d.type == "line" and d.id != self.duty.id
                     and d.start < later < d.end and after.vehicle_at(d.id, later))
        second = Breakdown(after.vehicle_at(other.id, later), later, None)
        for option in options_for(after, second, limit=None):
            state = apply(after, second, option)
            self.assertLegal(state, f"второе событие, {option.kind}")
            self.assertPermits(state, f"второе событие, {option.kind}")
            busy = [(shift_id, s.start, s.end) for shift_id, segs in state.drivers.items()
                    for s in segs if s.who == reserve_driver and s.start <= later < s.end]
            self.assertLessEqual(len(busy), 1,
                                 f"водителя {reserve_driver} отдали дважды: {busy}")

    def test_many_events_keep_the_day_legal(self):
        """Перебор: несколько нарядов, разное время, все виды событий и все варианты."""
        day = self.base.day
        line = sorted((d for d in day.duties.values()
                       if d.type == "line" and d.day_type == day.day_type
                       and d.start + 120 < d.end and self.base.vehicle_at(d.id, d.start + 60)),
                      key=lambda d: d.id)[:6]
        applied = 0
        for duty in line:
            for moment in (duty.start + 60, (duty.start + duty.end) // 2):
                vehicle = self.base.vehicle_at(duty.id, moment)
                shift = self.base.shift_at(duty.id, moment)
                driver = self.base.driver_at(shift.id, moment) if shift else None
                events = [Breakdown(vehicle, moment, None), Breakdown(vehicle, moment, 30),
                          Accident(vehicle, moment)]
                if driver:
                    events.append(NoShow(driver, moment))
                for event in events:
                    for option in options_for(self.base, event, limit=None):
                        state = apply(self.base, event, option)
                        applied += 1
                        self.assertLegal(state, f"{duty.id} {type(event).__name__} {option.kind}")
                        self.assertPermits(state, f"{duty.id} {option.kind}")
        self.assertGreater(applied, 50, "перебор должен был проверить хотя бы 50 применений")


if __name__ == "__main__":
    unittest.main()
