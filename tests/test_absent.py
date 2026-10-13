"""Отметка диспетчера «Не выходит» (schedule = absent). Запуск: python -m unittest

Диспетчер отмечает только «не выходит», причину не указывает. Для расчёта это
то же, что любой график, кроме work, а объяснения и нарушения говорят ровно то,
что он отметил: без медосмотра и без выходного по графику.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import unittest

from naryad.core.model import Day
from naryad.data.check import check
from naryad.explain import why_driver, why_unfilled
from naryad.solve.drivers import solve_drivers
from naryad.solve.vehicles import solve_vehicles
from naryad.web.api import Engine
from naryad.web.store import history_to_dict

from tests.test_core import SAMPLES

SAMPLE = SAMPLES / "park7_weekday.json"
MARK = "не выходит по отметке диспетчера"


def raw_day() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def mark(raw: dict, ids, schedule: str, medical=None) -> dict:
    raw = copy.deepcopy(raw)
    for driver in raw["drivers"]:
        if driver["id"] in ids:
            driver.update(schedule=schedule, medical=medical)
    return raw


def planned(raw: dict):
    """Сервер с одним спланированным днём: (сервер, номер дня, запись дня)."""
    engine = Engine()
    status, answer = engine.handle("POST", "/api/days", None, raw)
    assert status == 200, answer
    day_id = answer["day_id"]
    status, answer = engine.handle("POST", f"/api/days/{day_id}/plan", None, {})
    assert status == 200, answer
    return engine, day_id, engine.days[day_id]


class TestAbsent(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.raw = raw_day()
        _, _, record = planned(cls.raw)
        # 20 водителей, которых утренний план поставил на смены: снимать их ощутимо
        on_shift = sorted({seg.who for segments in record.state.drivers.values() for seg in segments})
        cls.ids = set(on_shift[::len(on_shift) // 20][:20])

    def test_data_check_accepts_the_mark(self):
        self.assertEqual(check(mark(self.raw, self.ids, "absent"))["errors"], [])

    def test_mark_with_medical_is_an_error_in_words(self):
        errors = check(mark(self.raw, self.ids, "absent", "passed"))["errors"]
        self.assertTrue(errors)
        self.assertTrue(all(MARK in e and "absent" not in e for e in errors), errors)

    def test_same_plan_and_memory_as_other_ways_to_stay_home(self):
        """Расстановка и память водителей те же, что при выходном и непройденном медосмотре."""
        results = {}
        for name, raw in (("absent", mark(self.raw, self.ids, "absent")),
                          ("day_off", mark(self.raw, self.ids, "day_off")),
                          ("failed", mark(self.raw, self.ids, "work", "failed"))):
            _, _, record = planned(raw)
            results[name] = (record.plan.vehicles, record.plan.drivers, record.plan.unfilled,
                             history_to_dict(record.history_after))
        self.assertEqual(results["absent"], results["day_off"])
        self.assertEqual(results["absent"], results["failed"])
        remembered = {d for d, row in results["absent"][3].items() if row[0] is not None}
        self.assertTrue(remembered)
        self.assertEqual(remembered & self.ids, set(), "кто не выходил, тот день не отработал")

    def test_explanations_name_the_mark_not_medical(self):
        day = Day.load(SAMPLE)
        plan = solve_drivers(day, solve_vehicles(day))
        found = 0
        for shift_id, driver_id in sorted(plan.drivers.items()):
            home = day.drivers[driver_id]
            if home.home_vehicle_id != plan.vehicles[day.shifts[shift_id].duty_id]:
                continue
            marked = copy.deepcopy(day)
            marked.drivers[driver_id] = dataclasses.replace(home, schedule="absent", medical=None)
            again = solve_drivers(marked, solve_vehicles(marked))
            now = again.drivers.get(shift_id)
            if now is None or marked.drivers[now].home_vehicle_id == home.home_vehicle_id:
                continue   # объяснение «чужой водитель» перечисляет закреплённых, только если встал не второй из них
            reasons = why_driver(marked, again, shift_id)["reasons"]
            self.assertIn(f"Закреплённый водитель {home.tab_number}: {MARK}", reasons)
            found += 1
            if found == 3:
                break
        self.assertEqual(found, 3)

    def test_unfilled_counts_the_mark(self):
        day = Day.load(SAMPLE)
        for driver in list(day.drivers.values()):
            if driver.schedule == "work":
                day.drivers[driver.id] = dataclasses.replace(driver, schedule="absent", medical=None)
        plan = solve_drivers(day, solve_vehicles(day))
        shift_id = next(s for s in sorted(plan.unfilled) if s in day.shifts)
        text = " ".join(why_unfilled(day, plan, shift_id)["reasons"])
        self.assertIn(f"{MARK}: ", text)
        self.assertNotIn("медосмотр", text)

    def test_manual_edit_is_refused_with_the_mark(self):
        driver_id = sorted(self.ids)[0]
        engine, day_id, record = planned(mark(self.raw, {driver_id}, "absent"))
        shift_id = next(s for s in sorted(record.day.shifts)
                        if record.day.duties[record.day.shifts[s].duty_id].type == "line"
                        and record.state.drivers.get(s))
        status, answer = engine.handle("GET", f"/api/days/{day_id}/edits/options", {"shift_id": shift_id}, None)
        self.assertEqual(status, 200, answer)
        self.assertNotIn(driver_id, [d["driver_id"] for d in answer["drivers"]])
        edit = {"type": "set_driver", "shift_id": shift_id, "driver_id": driver_id}
        status, answer = engine.handle("POST", f"/api/days/{day_id}/edits", None, edit)
        self.assertEqual(status, 409, answer)
        said = " ".join(v["text"] for v in answer["violations"])
        self.assertIn(MARK, said)
        self.assertNotIn("медосмотр", said)
        self.assertNotIn("по графику", said)
        status, answer = engine.handle("POST", f"/api/days/{day_id}/edits", None, {**edit, "force": True})
        self.assertEqual(status, 200, answer)


if __name__ == "__main__":
    unittest.main()
