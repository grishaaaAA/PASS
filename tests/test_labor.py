"""Нормы труда (А2): длина смены за день и отдых на серии дней. Запуск: python -m unittest"""

import copy
import dataclasses
import json
import os
import unittest
from unittest import mock
from datetime import date, timedelta

from naryad.core.invariants import (LABOR_ENV, LABOR_FILE, PRESETS_FILE, check_plan, check_rest,
                                    labor_presets, load_labor, use_labor_preset)
from naryad.core.model import Day, Plan
from naryad.data.generate import generate, generate_series

from tests.test_core import SAMPLES, SMALL, WEEKDAY, codes, greedy_plan

BASE = Day.from_dict(generate("case", WEEKDAY, seed=1, **SMALL))
SHIFT = next(iter(BASE.shifts))
DRIVER = next(iter(BASE.drivers))


def series(shifts, first="2026-10-05"):
    """Серия дней, где один водитель работает в заданное время.

    shifts - список (номер дня, начало, конец) в часах от начала того дня.
    Дни без смены - выходные водителя.
    """
    out, start = [], date.fromisoformat(first)
    by_day = {d: (s, e) for d, s, e in shifts}
    for index in range(max(by_day) + 1):
        day = copy.copy(BASE)
        day.meta = dict(BASE.meta, date=(start + timedelta(days=index)).isoformat())
        plan = Plan()
        if index in by_day:
            s, e = by_day[index]
            day.shifts = dict(BASE.shifts)
            day.shifts[SHIFT] = dataclasses.replace(BASE.shifts[SHIFT],
                                                    start=int(s * 60), end=int(e * 60))
            plan.drivers[SHIFT] = DRIVER
        out.append((day, plan))
    return out


def series_at(starts, length=4):
    """Серия, где один водитель работает сменами length ч, начиная в starts (часы от первого дня)."""
    out, first = [], date.fromisoformat("2026-10-05")
    by_day = {}
    for t in starts:
        by_day.setdefault(int(t // 24), []).append(t % 24)
    ids = list(BASE.shifts)
    for index in range(max(by_day) + 1):
        day = copy.copy(BASE)
        day.meta = dict(BASE.meta, date=(first + timedelta(days=index)).isoformat())
        day.shifts, plan = dict(BASE.shifts), Plan()
        for k, start in enumerate(by_day.get(index, [])):
            day.shifts[ids[k]] = dataclasses.replace(BASE.shifts[ids[k]], start=round(start * 60),
                                                     end=round((start + length) * 60))
            plan.drivers[ids[k]] = DRIVER
        out.append((day, plan))
    return out


class TestLaborFile(unittest.TestCase):

    def test_every_norm_has_source_and_status(self):
        raw = json.loads(LABOR_FILE.read_text(encoding="utf-8"))
        for name, item in raw.items():
            if name.startswith("_"):
                continue
            with self.subTest(name):
                self.assertIn("value", item)
                self.assertTrue(item["rule"] and item["status"] and item["checked"])
                self.assertIn("p424", item)  # отменённый № 424 - для истории
                if name != "max_shifts_per_driver_day":  # наше допущение, в приказе его нет
                    self.assertTrue(item["p160"])  # действующий Приказ Минтранса № 160
        self.assertIn("№ 160", raw["_about"])


class TestShiftLength(unittest.TestCase):
    """Смена 11 ч: нарушение, пока неизвестно, установил ли перевозчик смены до 12 ч."""

    def setUp(self):
        self.day = copy.copy(BASE)
        self.day.shifts = dict(BASE.shifts)
        self.plan = greedy_plan(self.day)
        shift_id = next(iter(self.plan.drivers))
        shift = self.day.shifts[shift_id]
        self.day.shifts[shift_id] = dataclasses.replace(shift, end=shift.start + 11 * 60)

    def test_eleven_hours_without_agreement(self):
        found = check_plan(self.day, self.plan)
        self.assertIn("driver_overtime", codes(found))
        self.assertTrue(any("п. 4 Приказа Минтранса № 160" in v.text for v in found))

    def test_eleven_hours_with_agreement(self):
        labor = dict(load_labor(), city_12h_allowed=True)
        self.assertNotIn("driver_overtime", codes(check_plan(self.day, self.plan, labor)))


class TestRest(unittest.TestCase):

    def test_five_two_is_fine(self):
        week = [(d, 6, 14) for d in range(5)] + [(d, 6, 14) for d in range(7, 12)]
        self.assertEqual(check_rest(series(week)), [])

    def test_seven_days_in_row(self):
        found = check_rest(series([(d, 6, 14) for d in range(7)]))
        self.assertEqual(codes(found), {"weekly_rest"})

    def test_long_shift_needs_double_rest(self):
        # 11 ч работы, через 13 ч снова на смену: нужно 22 ч минус 30 мин питания
        self.assertIn("rest_ratio", codes(check_rest(series([(0, 5, 16), (1, 5, 16)]))))

    def test_long_shifts_every_other_day_are_fine(self):
        self.assertEqual(check_rest(series([(0, 5, 16), (2, 5, 16), (4, 5, 16)])), [])

    def test_rest_shorter_than_nine_hours(self):
        # смена до 23:00, следующая с 06:00 - 7 ч отдыха
        found = check_rest(series([(0, 19, 23), (1, 6, 10)]))
        self.assertIn("daily_rest", codes(found))

    def test_reduced_rest_only_three_times(self):
        # смены по 4 ч, между ними 10 ч: три сокращения законны, четвёртое - нарушение
        self.assertEqual(check_rest(series_at([0, 14, 28, 42])), [])
        self.assertEqual(codes(check_rest(series_at([0, 14, 28, 42, 56]))), {"daily_rest"})

    def test_reduced_count_resets_only_after_weekly_rest(self):
        # отдыхи 10, 10, 12, 10, 10 ч: обычный 12 ч счётчик не сбрасывает - четвёртое сокращение
        self.assertEqual(codes(check_rest(series_at([0, 14, 28, 44, 58, 72]))), {"daily_rest"})
        # отдыхи 10, 10, 45, 10, 10 ч: после еженедельного отдыха снова можно три раза
        self.assertEqual(check_rest(series_at([0, 14, 28, 77, 91, 105])), [])

    def test_boundaries(self):
        self.assertEqual(check_rest(series_at([0, 13])), [])                  # отдых ровно 9 ч
        self.assertEqual(check_rest(series([(d, 6, 14) for d in range(6)])), [])  # ровно 6 смен подряд



class TestPrepTime(unittest.TestCase):
    """Подготовка и медосмотры (п. 13 Приказа № 160) - рабочее время."""

    PREP = dict(load_labor(), prep_before_min=15, prep_after_min=10)

    def test_shift_with_prep_over_limit(self):
        day = copy.copy(BASE)
        day.shifts = dict(BASE.shifts)
        plan = greedy_plan(day)
        shift_id = next(iter(plan.drivers))
        shift = day.shifts[shift_id]
        day.shifts[shift_id] = dataclasses.replace(shift, end=shift.start + 9 * 60 + 40)
        self.assertNotIn("driver_overtime", codes(check_plan(day, plan)))
        self.assertIn("driver_overtime", codes(check_plan(day, plan, self.PREP)))  # 9:40 + 0:25 > 10 ч

    def test_rest_without_prep_time(self):
        week = series([(0, 18, 22), (1, 7 + 1 / 6, 11)])  # между сменами 9 ч 10 мин
        self.assertEqual(check_rest(week), [])             # сокращённый отдых, законно
        self.assertEqual(codes(check_rest(week, self.PREP)), {"daily_rest"})  # отдых 8 ч 45 мин

    def test_solver_respects_prep_time(self):
        from naryad.solve.series import solve_series
        days = [Day.from_dict(d) for d in generate_series("park7", "2026-10-05", 5, 1, "morning")]
        plans = solve_series(days, self.PREP)
        self.assertEqual(check_rest(plans, self.PREP), [])
        self.assertFalse([v for day, plan in plans for v in check_plan(day, plan, self.PREP)])


class TestLaborPresets(unittest.TestCase):
    """Наборы норм на время, пока перевозчик не ответил: current, likely, strict."""

    def tearDown(self):
        use_labor_preset(None)

    def test_presets_file(self):
        raw = json.loads(PRESETS_FILE.read_text(encoding="utf-8"))
        known = set(load_labor())
        self.assertEqual(set(labor_presets()), {"current", "likely", "strict"})
        for name, item in raw.items():
            if name.startswith("_"):
                continue
            with self.subTest(name):
                self.assertTrue(item["_why"])
                self.assertLessEqual(set(labor_presets()[name]), known)  # только нормы из labor.json
        self.assertEqual(labor_presets()["current"], {})

    def test_preset_argument(self):
        self.assertEqual(load_labor()["prep_before_min"], 0)
        likely = load_labor(preset="likely")
        self.assertEqual((likely["prep_before_min"], likely["prep_after_min"]), (20, 10))
        self.assertTrue(likely["city_12h_allowed"])
        self.assertEqual(load_labor(preset="current"), load_labor())

    def test_unknown_preset(self):
        with self.assertRaises(ValueError):
            load_labor(preset="нет такого")
        with self.assertRaises(ValueError):
            use_labor_preset("нет такого")

    def test_preset_for_process(self):
        use_labor_preset("strict")
        self.assertEqual(load_labor()["prep_after_min"], 15)
        self.assertFalse(load_labor()["city_12h_allowed"])
        use_labor_preset(None)
        self.assertEqual(load_labor()["prep_after_min"], 0)

    def test_preset_from_environment(self):
        with mock.patch.dict(os.environ, {LABOR_ENV: "likely"}):
            self.assertEqual(load_labor()["prep_before_min"], 20)
            use_labor_preset("strict")  # явный выбор сильнее переменной окружения
            self.assertEqual(load_labor()["prep_before_min"], 30)

    def test_solver_is_legal_under_every_preset(self):
        from naryad.solve.series import solve_series
        days = [Day.from_dict(d) for d in generate_series("park7", "2026-10-05", 3, 1, "morning")]
        for name in labor_presets():
            with self.subTest(name):
                labor = load_labor(preset=name)
                plans = solve_series(days, labor)
                self.assertEqual(check_rest(plans, labor), [])
                self.assertFalse([v for day, plan in plans for v in check_plan(day, plan, labor)])

    def test_manual_model_is_legal_under_every_preset(self):
        # ручной способ тоже не ставит водителя на смену, которая с подготовкой длиннее нормы
        from naryad.solve.baseline import baseline_drivers
        from naryad.solve.vehicles import solve_vehicles
        day = Day.load(SAMPLES / "park7_weekday.json")
        vehicles = solve_vehicles(day)
        for name in labor_presets():
            with self.subTest(name):
                labor = load_labor(preset=name)
                plan = baseline_drivers(day, vehicles, {}, labor)
                self.assertEqual(check_plan(day, plan, labor), [])
        strict = baseline_drivers(day, vehicles, {}, load_labor(preset="strict"))
        self.assertGreater(len(strict.unfilled), len(baseline_drivers(day, vehicles, {}).unfilled))

    def test_strict_prep_makes_nine_and_a_half_hour_shift_overtime(self):
        # смена 9 ч 30 мин: с подготовкой 30 + 15 мин это 10 ч 15 мин, больше нормы 10 ч
        day = copy.copy(BASE)
        day.shifts = dict(BASE.shifts)
        plan = greedy_plan(day)
        shift_id = next(iter(plan.drivers))
        shift = day.shifts[shift_id]
        day.shifts[shift_id] = dataclasses.replace(shift, end=shift.start + 9 * 60 + 30)
        self.assertNotIn("driver_overtime", codes(check_plan(day, plan, load_labor(preset="likely"))))
        self.assertIn("driver_overtime", codes(check_plan(day, plan, load_labor(preset="strict"))))


if __name__ == "__main__":
    unittest.main()
