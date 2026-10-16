"""Находки тестирования рабочего места 16.10.2026 в движке и API. Запуск: python -m unittest

Номера - из отчёта тестирования (B16, B42, B40, B24, B26, B05): каждую
воспроизвели на чистом стенде, здесь - то, что должно остаться исправленным.
"""

from __future__ import annotations

import re
import unittest

from naryad.core.model import Day
from naryad.data.registry import read_csv_text
from naryad.explain import explain_option, intervals, why_vehicle
from naryad.ops.replan import (SUPPLY_MIN, Breakdown, Edit, OpsState, apply, check_state, manual_edit,
                               options_for)
from naryad.solve.drivers import solve_drivers
from naryad.solve.vehicles import solve_vehicles
from naryad.web.api import Engine, parse_time, ApiError

from tests.test_core import SAMPLES

SAMPLE = SAMPLES / "park7_weekday.json"
CODES = re.compile(r"\bP\d{2}\b|\bP\d{2}-|duration_min|\bat:|\bfrom:|edits/options|ресурс")


def morning() -> OpsState:
    day = Day.load(SAMPLE)
    return OpsState.from_plan(day, solve_drivers(day, solve_vehicles(day)))


class TestReplacementWithoutDriver(unittest.TestCase):
    """B42: смена кончается раньше, чем подъедет замена, - водителя замены нет и в названии варианта."""

    def test_bus_only_when_the_shift_ends_before_it_arrives(self):
        state = morning()
        day = state.day
        found = 0
        for shift in sorted(day.shifts.values(), key=lambda s: s.id):
            duty = day.duties[shift.duty_id]
            t = shift.end - SUPPLY_MIN // 2          # автобус подъедет уже после пересменки
            nxt = state.shift_at(duty.id, shift.end)
            vehicle = state.vehicle_at(duty.id, t)
            if duty.type != "line" or not vehicle or nxt is None or nxt.id == shift.id or not shift.start < t:
                continue
            before = {(v.code, v.ids) for v in check_state(state)}
            event = Breakdown(vehicle, t)
            for option in options_for(state, event, limit=None):
                if option.kind not in ("idle", "reserve"):
                    continue
                found += 1
                self.assertNotIn("+ водитель", option.title, option.title)
                self.assertNotRegex(option.note, r"водитель резерва \d|свободный водитель \d", option.note)
                after = apply(state, event, option)
                self.assertTrue(all(seg.end > seg.start for segs in after.drivers.values() for seg in segs))
                self.assertEqual({(v.code, v.ids) for v in check_state(after)} - before, set(), option.title)
            if found >= 6:
                break
        self.assertGreater(found, 2)


class TestReserveBreakdown(unittest.TestCase):
    """B16: сход резервного автобуса - потеря запаса, а не простой линии."""

    def test_no_bus_taken_from_a_route_for_the_reserve(self):
        state = morning()
        day = state.day
        reserve = next(d for d in sorted(day.duties.values(), key=lambda d: d.id)
                       if d.type == "reserve" and state.vehicle_at(d.id, 13 * 60))
        event = Breakdown(state.vehicle_at(reserve.id, 13 * 60), 13 * 60)
        options = options_for(state, event, limit=None)
        kinds = {o.kind for o in options}
        self.assertNotIn("donor", kinds)
        self.assertNotIn("reserve", kinds)
        none = next(o for o in options if o.kind == "none")
        self.assertEqual(none.lost_minutes, 0)
        self.assertIn("в резерве на один автобус меньше", none.note)
        text = " ".join(explain_option(state, event, options, options.index(none))["reasons"])
        self.assertIn("Без работы на линии всего (на всех затронутых маршрутах): 0 ч 00 мин", text)


class TestIntervalSummary(unittest.TestCase):
    """B40: небольшой рост интервала не называется «в пределах плана», дробь - через запятую."""

    def test_small_growth_is_not_called_within_plan(self):
        state = morning()
        day = state.day
        duty = next(d for d in sorted(day.duties.values(), key=lambda d: d.id)
                    if d.type == "line" and state.vehicle_at(d.id, 8 * 60 + 40))
        options = options_for(state, Breakdown(state.vehicle_at(duty.id, 8 * 60 + 40), 8 * 60 + 40), limit=None)
        after = apply(state, Breakdown(state.vehicle_at(duty.id, 8 * 60 + 40), 8 * 60 + 40),
                      next(o for o in options if o.kind == "idle"))
        answer = intervals(day, after)
        growth = answer["numbers"]["worst_growth"]
        self.assertTrue(1 < growth <= 1.25, growth)
        self.assertNotIn("в пределах плана", " ".join(answer["reasons"]))
        self.assertRegex(answer["answer"], r"в \d,\d+ раза")


class TestCsvFiles(unittest.TestCase):
    """B24: CSV с концами строк CR читается, двоичный файл - понятное сообщение по-русски."""

    def test_cr_line_endings(self):
        self.assertEqual(read_csv_text("Парк;Название\rП7;Парк №7\r"), [["Парк", "Название"], ["П7", "Парк №7"]])

    def test_binary_file_named_csv(self):
        for content in ("PK\x03\x04\x14\x00\x06\x00", "abc\x00def"):
            with self.assertRaises(ValueError) as caught:
                read_csv_text(content, "Парки.csv")
            text = str(caught.exception)
            self.assertIn("Парки.csv", text)
            self.assertIn(".xlsx", text)
            latin = set(re.findall(r"[A-Za-z]{3,}", text)) - {"CSV", "csv", "Excel", "xlsx"}
            self.assertEqual(latin, set(), text)   # без английского текста Python


class TestDispatcherTexts(unittest.TestCase):
    """B05, B26: тексты, которые видит диспетчер, без кодов и имён полей; переработка в часах."""

    def test_time_errors_name_the_field_in_words(self):
        for field, word in (("at", "Время события"), ("from", "Начало окна"), ("to", "Конец окна")):
            with self.assertRaises(ApiError) as caught:
                parse_time("8.30", field)
            self.assertIn(word, str(caught.exception))
            self.assertNotIn(f"{field}:", str(caught.exception))

    def test_event_errors_and_explanations_without_codes(self):
        engine = Engine()
        day_id = engine.handle("POST", "/api/days", None,
                               {"preset": "park7", "date": "2026-10-05", "seed": 1, "moment": "morning"})[1]["day_id"]
        engine.handle("POST", f"/api/days/{day_id}/plan", None, {})
        record = engine.days[day_id]
        duty = next(d for d in sorted(record.day.duties.values(), key=lambda d: d.id) if d.type == "line")
        bus = record.plan.vehicles[duty.id]
        said = []
        for body in ({"type": "breakdown", "vehicle_id": bus, "at": "03:00"},
                     {"type": "breakdown", "vehicle_id": bus, "at": "08:40", "duration_min": 0},
                     {"type": "breakdown", "vehicle_id": bus, "at": "8.40"}):
            status, answer = engine.handle("POST", f"/api/days/{day_id}/events/options", None, body)
            self.assertEqual(status, 400, answer)
            said.append(answer["error"])
        said.append(" ".join(why_vehicle(record.day, record.plan, duty.id)["reasons"]))
        edit = Edit("clear_vehicle", duty.id, None, duty.start, duty.end, "снять")
        after = manual_edit(record.state, edit)
        from naryad.explain import why_emptied
        emptied = why_emptied(record.day, record.plan, after, duty.id, "removed_by_dispatcher", [])
        said += [emptied["answer"]] + emptied["reasons"]
        self.assertEqual([s for s in said if CODES.search(s)], [], said)
        self.assertIn(record.day.vehicles[bus].board_number, said[0])

    def test_overtime_in_hours_with_the_norm(self):
        state = morning()
        day = state.day
        shift = next(s for s in sorted(day.shifts.values(), key=lambda s: s.id) if s.id in state.drivers)
        other = next(s for s in sorted(day.shifts.values(), key=lambda s: -s.end)
                     if s.id in state.drivers and s.start > shift.end)
        state.drivers[other.id] = [state.drivers[other.id][0].__class__(other.start, other.end,
                                                                        state.drivers[shift.id][0].who)]
        text = next(v.text for v in check_state(state) if v.code == "driver_overtime")
        self.assertRegex(text, r"рабочий день \d+ ч \d\d мин больше нормы 12 ч 00 мин")

    def test_reserve_duty_named_in_the_journal(self):
        engine = Engine()
        day_id = engine.handle("POST", "/api/days", None,
                               {"preset": "park7", "date": "2026-10-05", "seed": 1, "moment": "morning"})[1]["day_id"]
        engine.handle("POST", f"/api/days/{day_id}/plan", None, {})
        record = engine.days[day_id]
        reserve = next(d for d in sorted(record.day.duties.values(), key=lambda d: d.id)
                       if d.type == "reserve" and d.id in record.plan.vehicles)
        status, answer = engine.handle("POST", f"/api/days/{day_id}/edits", None,
                                       {"type": "clear_vehicle", "duty_id": reserve.id})
        self.assertEqual(status, 200, answer)
        self.assertIn("с резервного наряда", answer["log"][-1]["title"])


if __name__ == "__main__":
    unittest.main()
