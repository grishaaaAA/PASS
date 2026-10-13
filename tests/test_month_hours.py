"""Предел перевозчика за месяц: 180 ч рабочего времени на водителя. Запуск: python -m unittest

Со слов директора парка №6 (13.10.2026): водитель работает до 12 ч в день,
но не больше 180 ч в месяц. Рабочее время - смены вместе с подготовкой и
медосмотрами (п. 13 Приказа № 160), месяц - календарный, по дате дня.
"""

from __future__ import annotations

import unittest

from naryad.core.invariants import check_rest, load_labor, work_minutes
from naryad.core.model import Day
from naryad.data.generate import generate_series
from naryad.explain import why_driver, why_unfilled
from naryad.ops.replan import (Breakdown, NoShow, OpsState, Segment, apply, check_state, free_drivers,
                               options_for, worked_today)
from naryad.solve.drivers import DriverState, History, remember, solve_drivers
from naryad.solve.series import series_report, solve_series
from naryad.solve.vehicles import solve_vehicles
from naryad.web.api import Engine

from tests.test_core import SAMPLES
from tests.test_labor import series
from tests.test_ops import MORNING, important_running, morning_state

SAMPLE = SAMPLES / "park7_weekday.json"   # 05.10.2026
LIMIT = load_labor()["max_month_work_min"]


def worked(ids, minutes: int, month: str = "2026-10") -> History:
    """Память, где у водителей ids в месяце month уже отработано minutes."""
    history = History()
    for driver_id in ids:
        history.get(driver_id).month, history.get(driver_id).month_minutes = month, minutes
    return history


class TestNorm(unittest.TestCase):

    def test_limit_and_twelve_hours(self):
        labor = load_labor()
        self.assertEqual(LIMIT, 180 * 60)
        self.assertTrue(labor["city_12h_allowed"])

    def test_memory_counts_prep_time(self):
        labor = dict(load_labor(), prep_before_min=20, prep_after_min=10)
        state = DriverState()
        remember(state, 0, 8 * 60, "2026-10", labor)
        self.assertEqual(state.month_minutes, 8 * 60 + 30)


class TestSeries(unittest.TestCase):

    @staticmethod
    def month_hours(days, first):
        return [v for v in check_rest(series(days, first)) if v.code == "month_hours"]

    def test_over_the_limit_is_caught_once(self):
        days = [(d, 6, 16) for d in range(31) if d % 6 < 4]   # 4 через 2 по 10 ч: 21 смена, 210 ч
        found = self.month_hours(days, "2026-10-01")
        self.assertEqual(len(found), 1)
        self.assertIn("предел перевозчика 180 ч", found[0].text)
        self.assertIn("190 ч 00 мин", found[0].text)   # первая смена сверх предела - девятнадцатая

    def test_exactly_the_limit_is_fine(self):
        days = [(d, 6, 16) for d in range(27) if d % 6 < 4][:18]   # 18 смен по 10 ч = 180 ч
        self.assertEqual(self.month_hours(days, "2026-10-01"), [])

    def test_month_starts_from_zero(self):
        days = [(d, 6, 16) for d in range(31) if d % 6 < 4]   # те же 210 ч, но с 20.10 по 19.11
        self.assertEqual(self.month_hours(days, "2026-10-20"), [])


class TestSolver(unittest.TestCase):

    def setUp(self):
        self.day = Day.load(SAMPLE)
        plan = solve_drivers(self.day, solve_vehicles(self.day))
        self.ids = set(sorted(set(plan.drivers.values()))[:30])

    def test_no_shift_beyond_the_limit(self):
        again = solve_drivers(self.day, solve_vehicles(self.day), worked(self.ids, LIMIT - 60))
        self.assertEqual(set(again.drivers.values()) & self.ids, set())

    def test_last_month_does_not_count(self):
        again = solve_drivers(self.day, solve_vehicles(self.day), worked(self.ids, LIMIT - 60, "2026-09"))
        self.assertTrue(set(again.drivers.values()) & self.ids)

    def test_explained(self):
        everyone = worked(self.day.drivers, LIMIT - 60)
        plan = solve_drivers(self.day, solve_vehicles(self.day), worked(self.day.drivers, LIMIT - 60))
        self.assertFalse(plan.drivers)
        shift_id = next(s for s in sorted(plan.unfilled) if s in self.day.shifts)
        text = " ".join(why_unfilled(self.day, plan, shift_id, everyone)["reasons"])
        self.assertIn("выработали месячный предел 180 ч", text)

    def test_hours_so_far_are_shown_with_the_limit(self):
        plan = solve_drivers(self.day, solve_vehicles(self.day), worked(self.day.drivers, 100 * 60))
        shift_id = sorted(plan.drivers)[0]
        reasons = why_driver(self.day, plan, shift_id, worked(self.day.drivers, 100 * 60))["reasons"]
        self.assertIn("В этом месяце отработал 100 ч 00 мин из 180 ч", reasons)


class TestWithinDay(unittest.TestCase):

    def test_replacement_and_manual_placement(self):
        state = morning_state()
        duty = important_running(state)
        labor = load_labor()
        found = free_drivers(state, duty, MORNING, MORNING + 4 * 60, labor)
        tired = found[0]
        state.history = worked([tired], LIMIT - 60)
        self.assertNotIn(tired, free_drivers(state, duty, MORNING, MORNING + 4 * 60, labor))
        self.assertIn(tired, free_drivers(state, duty, MORNING, MORNING + 60, labor))   # час ещё есть
        shift_id = next(s for s in sorted(state.drivers) if state.day.shifts[s].duty_id == duty.id)
        shift = state.day.shifts[shift_id]
        state.drivers[shift_id] = [Segment(shift.start, shift.end, tired)]
        said = [v.text for v in check_state(state) if v.code == "month_hours"]
        self.assertEqual(len(said), 1, said)
        self.assertIn("предел перевозчика 180 ч", said[0])


class TestReserveWithinDay(unittest.TestCase):
    """Водитель резерва уже работает сегодня: отрезок на линии удлиняет его день, а с ним и месяц."""

    @classmethod
    def setUpClass(cls):
        cls.day = Day.load(SAMPLE)
        cls.plan = solve_drivers(cls.day, solve_vehicles(cls.day))

    def state(self, done) -> OpsState:
        """Утро дня, где у водителей резерва в месяце уже done(резервная смена) минут."""
        history = History()
        for shift_id, driver_id in self.plan.drivers.items():
            shift = self.day.shifts[shift_id]
            if self.day.duties[shift.duty_id].type == "reserve":
                history.get(driver_id).month, history.get(driver_id).month_minutes = "2026-10", done(shift)
        return OpsState.from_plan(self.day, self.plan, history)

    def events(self, state) -> list:
        """За полтора часа до конца резервной смены: сход и неявка там, где замена уходит дальше её конца."""
        day, plan, out = self.day, self.plan, []
        for reserve_id, _ in sorted(plan.drivers.items()):
            reserve = day.shifts[reserve_id]
            if day.duties[reserve.duty_id].type != "reserve":
                continue
            t = reserve.end - 90
            for duty in sorted(day.duties.values(), key=lambda d: d.id):
                shift = state.shift_at(duty.id, t) if duty.type == "line" and duty.start <= t < duty.end - 120 else None
                if shift is not None and shift.end > reserve.end + 30 and state.vehicle_at(duty.id, t):
                    out.append(Breakdown(state.vehicle_at(duty.id, t), t))
                    break
            for shift_id, driver_id in sorted(plan.drivers.items()):
                shift = day.shifts[shift_id]
                if (day.duties[shift.duty_id].type == "line" and t - 30 <= shift.start <= t + 30
                        and shift.end > reserve.end + 30):
                    out.append(NoShow(driver_id, shift.start - 20))
                    break
        return out[:12]

    def test_reserve_driver_is_not_sent_past_the_month(self):
        labor = load_labor()
        free = self.state(lambda shift: 0)
        tight = self.state(lambda shift: LIMIT - work_minutes(shift.length, labor))
        self.assertEqual([v.text for v in check_state(tight) if v.code == "month_hours"], [])   # утром законно
        events = self.events(tight)
        self.assertGreater(len(events), 5)
        would_break = 0
        for event in events:
            for option in options_for(free, event):   # без памяти водитель резерва поехал бы на линию
                would_break += any(v.code == "month_hours" for v in check_state(apply(tight, event, option)))
            for option in options_for(tight, event):
                with self.subTest(event=event, option=option.title):
                    after = check_state(apply(tight, event, option))
                    self.assertEqual([v.text for v in after if v.code == "month_hours"], [])
        self.assertGreater(would_break, 0, "сценарий должен задевать водителей резерва")


class TestSeriesByFact(unittest.TestCase):
    """Отдых и месяц по серии дней - по факту прошлых дней, как память водителей."""

    @classmethod
    def setUpClass(cls):
        engine = Engine()
        raw1, raw2 = generate_series("park7", "2026-10-05", 2, 1, "morning")
        id1 = engine.handle("POST", "/api/days", None, raw1)[1]["day_id"]
        id2 = engine.handle("POST", "/api/days", None, raw2)[1]["day_id"]
        engine.handle("POST", f"/api/days/{id1}/plan", None, {})
        first = engine.days[id1]
        cls.victims = sorted((s, d) for s, d in first.plan.drivers.items()   # длинные смены с 06:00 и позже
                             if first.day.shifts[s].start >= 360 and first.day.shifts[s].length >= 8 * 60)[:40]
        for _, driver_id in cls.victims:
            event = {"type": "no_show", "driver_id": driver_id, "at": "05:00"}
            options = engine.handle("POST", f"/api/days/{id1}/events/options", None, event)[1]["options"]
            none = next(i for i, o in enumerate(options) if o["kind"] == "none")
            status, answer = engine.handle("POST", f"/api/days/{id1}/events/apply", None,
                                           {"event": event, "option": none})
            assert status == 200, answer
        status, cls.answer = engine.handle("POST", f"/api/days/{id2}/plan", None, {"history_from": id1})
        assert status == 200, cls.answer
        cls.first, cls.second = first, engine.days[id2]
        cls.missing = {d for _, d in cls.victims}

    def test_no_show_yesterday_is_not_a_worked_shift(self):
        self.assertEqual(self.answer["rest_violations"], [])
        # по утреннему плану вчерашнего дня проверка ругалась бы на не вышедших
        morning = check_rest([(self.first.day, self.first.plan), (self.second.day, self.second.plan)])
        self.assertTrue(morning)
        self.assertLessEqual({v.ids[0] for v in morning}, self.missing)

    def test_month_counts_only_what_was_worked(self):
        low = dict(load_labor(), max_month_work_min=16 * 60)   # чтобы двух дней хватило
        fact = check_rest([(self.first.day, worked_today(self.first.state)), (self.second.day, self.second.plan)], low)
        morning = check_rest([(self.first.day, self.first.plan), (self.second.day, self.second.plan)], low)
        self.assertEqual({v.ids[0] for v in fact if v.code == "month_hours"} & self.missing, set())
        self.assertTrue({v.ids[0] for v in morning if v.code == "month_hours"} & self.missing)

    def test_worked_today_matches_memory(self):
        worked = worked_today(self.first.state)
        self.assertEqual(set(worked) & self.missing, set())
        for driver_id, [(start, end, _)] in worked.items():
            self.assertEqual(self.first.history_after.peek(driver_id).last_length, end - start, driver_id)


class TestSeriesReport(unittest.TestCase):

    def test_month_limit_is_its_own_line(self):
        days = [Day.from_dict(d) for d in generate_series("park7", "2026-10-05", 2, 1, "morning")]
        report = series_report(solve_series(days))
        self.assertEqual(report["водителей сверх 180 ч за месяц"], 0)
        self.assertEqual(report["нарушений норм отдыха"], 0)


if __name__ == "__main__":
    unittest.main()
