"""API движка (Б5): ручки, ошибки, образцы ответов, живой сервер. Запуск: python -m unittest"""

import contextlib
import io
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from naryad.core.invariants import check_rest
from naryad.core.model import Plan
from naryad.data.generate import generate
from naryad.solve.drivers import DriverState
from naryad.web.api import hm
from naryad.web.api import EXAMPLES_FILE, Engine, Handler, make_examples

PARK7 = {"preset": "park7", "date": "2026-10-05", "seed": 1, "moment": "morning"}
DUTY, SHIFT, ROUTE = "P07-R01-WD01", "P07-R01-WD01-S1", "P07-R01"
# все виды вариантов из docs/CONTRACT.md, раздел «API движка»
KINDS = ("none", "idle", "reserve", "donor", "free_driver", "reserve_driver")
CITY = dict(release_per_park=40, routes_total=6,
            class_mix={"medium": 30, "big": 30, "extra_big": 20})


def city_day(parks: int = 2, shortage: float = 0.2, short_park: str = "P02",
             gas_park: str | None = None) -> dict:
    """Небольшой город, где в одном парке часть автобусов не вышла.

    gas_park - весь парк переводится на газ: так видно, работает ли запрет
    отдавать газовый автобус в парк без газовой инфраструктуры.
    """
    raw = generate("case", "2026-10-05", seed=1, park_count=parks, **CITY)
    if gas_park:
        for vehicle in raw["vehicles"]:
            if vehicle["park_id"] == gas_park:
                vehicle["fuel"] = "gas"
    ok_now = [v for v in raw["vehicles"] if v["park_id"] == short_park and v["condition"] == "ok"]
    for vehicle in ok_now[:round(len(ok_now) * shortage)]:
        vehicle["condition"], vehicle["repair_days_left"] = "repair", 3
    return raw


def ok(result):
    status, payload = result
    assert status == 200, (status, payload)
    return payload


class TestDays(unittest.TestCase):

    def setUp(self):
        self.engine = Engine()

    def test_add_day_from_preset_and_from_data(self):
        first = ok(self.engine.handle("POST", "/api/days", None, PARK7))
        self.assertEqual(first["day_id"], "day-1")
        self.assertEqual(first["counts"]["vehicles"], 420)
        self.assertEqual(first["warnings"], [])
        raw = ok(self.engine.handle("GET", "/api/days/day-1"))
        second = ok(self.engine.handle("POST", "/api/days", None, raw))
        self.assertEqual(second["day_id"], "day-2")
        self.assertEqual(second["counts"], first["counts"])
        listed = ok(self.engine.handle("GET", "/api/days"))["days"]
        self.assertEqual([d["day_id"] for d in listed], ["day-1", "day-2"])
        self.assertFalse(listed[0]["planned"])

    def test_bad_inputs(self):
        for body, word in (({"preset": "x"}, "preset"), ({"date": "вчера"}, "date"),
                           ({"seed": "a"}, "seed"), ({"moment": "x"}, "moment")):
            status, payload = self.engine.handle("POST", "/api/days", None, body)
            self.assertEqual(status, 400, body)
            self.assertIn(word, payload["error"])
        status, payload = self.engine.handle("POST", "/api/days", None, {"meta": {}, "parks": "нет"})
        self.assertEqual(status, 400)
        self.assertTrue(payload["errors"])

    def test_every_route_is_in_the_contract(self):
        """Ручка, которой нет в таблице контракта, для интерфейса не существует."""
        from pathlib import Path as _Path
        import re as _re
        from naryad.web.api import ROUTES
        contract = (_Path(__file__).resolve().parent.parent / "docs" / "CONTRACT.md").read_text(encoding="utf-8")
        table = _re.findall(r"^\| `(GET|POST) ([^`?]+)[^`]*` \|", contract, _re.M)
        described = {(verb, path.strip()) for verb, path in table}
        in_code = {(verb, _re.sub(r"\(\[\^/\]\+\)", "{id}", pattern)) for verb, pattern, _ in ROUTES}
        missing = {(v, p) for v, p in in_code
                   if not any(v == dv and _re.sub(r"\{[a-z_]+\}", "{id}", dp) == p
                              for dv, dp in described)}
        self.assertEqual(missing, set(), "этих ручек нет в таблице docs/CONTRACT.md")

    def test_every_error_code_is_in_the_contract(self):
        """Код ответа, который умеет отдавать сервер, должен быть назван в контракте."""
        from http import HTTPStatus as _Status
        from pathlib import Path as _Path
        import re as _re
        root = _Path(__file__).resolve().parent.parent
        source = (root / "naryad" / "web" / "api.py").read_text(encoding="utf-8")
        contract = (root / "docs" / "CONTRACT.md").read_text(encoding="utf-8")
        section = contract[contract.index("## API движка"):]
        used = {int(getattr(_Status, name)) for name in _re.findall(r"HTTPStatus\.([A-Z_]+)", source)}
        used.discard(200)
        used.discard(204)   # пустой ответ на предварительный запрос, не ошибка
        for code in sorted(used):
            self.assertIn(str(code), section, f"код {code} сервер отдаёт, а контракт о нём молчит")

    def test_routing(self):
        self.assertEqual(self.engine.handle("GET", "/api/nothing")[0], 404)
        self.assertEqual(self.engine.handle("GET", "/api/days/day-9")[0], 404)
        status, payload = self.engine.handle("DELETE", "/api/days")
        self.assertEqual(status, 405)
        self.assertIn("GET, POST", payload["error"])
        self.assertEqual(self.engine.handle("GET", "/api/days/")[0], 200)  # хвостовая косая не мешает

    def test_labor(self):
        payload = ok(self.engine.handle("GET", "/api/labor"))
        self.assertEqual(payload["preset"], "current")
        self.assertIn("likely", payload["presets"])
        norms = {n["name"]: n for n in payload["norms"]}
        self.assertEqual(norms["max_shift_min"]["value"], 600)
        self.assertTrue(norms["max_shift_min"]["p160"] and norms["max_shift_min"]["status"])


class TestPlanAndExplain(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.engine = Engine()
        ok(cls.engine.handle("POST", "/api/days", None, PARK7))
        cls.planned = ok(cls.engine.handle("POST", "/api/days/day-1/plan", None, {}))

    def test_plan(self):
        plan = Plan.from_dict(self.planned["plan"])
        self.assertEqual(len(plan.vehicles), 350)
        self.assertEqual(len(plan.drivers), 717)
        self.assertEqual(self.planned["violations"], [])
        self.assertEqual(self.planned["plan"]["transfers"], [])  # один парк: перебрасывать некуда
        self.assertIn("326 из 326", self.planned["summary"]["answer"])
        self.assertLess(self.planned["seconds"], 10)

    def test_state_before_events_is_the_plan(self):
        state = ok(self.engine.handle("GET", "/api/days/day-1/state"))
        self.assertEqual(state["meta"], {"day_id": "day-1", "date": "2026-10-05", "events": 0,
                                         "edits": 0, "labor": "current"})
        self.assertEqual(len(state["duties"]), 350)
        self.assertTrue(all(len(d["vehicles"]) == 1 for d in state["duties"]))
        one = next(d for d in state["duties"] if d["duty_id"] == DUTY)
        vehicle = next(a["vehicle_id"] for a in self.planned["plan"]["vehicle_assignments"] if a["duty_id"] == DUTY)
        self.assertEqual(one["vehicles"], [{"from": "04:50", "to": "25:20", "vehicle_id": vehicle}])
        self.assertEqual(state["log"], [])

    def test_explanations(self):
        for path, word in ((f"/api/days/day-1/explain/vehicle/{DUTY}", "автобус"),
                           (f"/api/days/day-1/explain/driver/{SHIFT}", "водитель"),
                           ("/api/days/day-1/explain/summary", "Сводка")):
            payload = ok(self.engine.handle("GET", path))
            self.assertEqual(set(payload), {"question", "answer", "reasons", "numbers"}, path)
            self.assertIn(word, payload["question"])
        interval = ok(self.engine.handle("GET", f"/api/days/day-1/explain/interval/{ROUTE}", {"t": "08:30"}))
        self.assertEqual(set(interval), {"question", "answer", "reasons", "numbers"})
        self.assertEqual(set(interval["numbers"]),
                         {"planned_min", "actual_min", "planned_buses", "running_buses"})
        self.assertIn("08:30", interval["question"])

    def test_intervals_over_the_day_and_at_a_moment(self):
        whole = ok(self.engine.handle("GET", "/api/days/day-1/intervals"))
        self.assertEqual(set(whole), {"question", "answer", "reasons", "numbers", "routes"})
        self.assertEqual(whole["numbers"]["worst_growth"], 1.0)
        self.assertEqual(len(whole["routes"]), whole["numbers"]["routes_total"])
        self.assertTrue(all(r["planned_min"] > 0 for r in whole["routes"]))
        moment = ok(self.engine.handle("GET", "/api/days/day-1/intervals", {"t": "08:30"}, None))
        self.assertIn("08:30", moment["question"])
        self.assertTrue(all(r["at"] == "08:30" for r in moment["routes"]))

    def test_intervals_follow_events_not_the_morning_plan(self):
        """Диспетчеру нужно, что с интервалами сейчас, а не что было утром.

        Свой движок: тест применяет событие, а день этого класса общий.
        """
        engine = Engine()
        ok(engine.handle("POST", "/api/days", None, PARK7))
        ok(engine.handle("POST", "/api/days/day-1/plan", None, {}))
        before = ok(engine.handle("GET", "/api/days/day-1/intervals"))["numbers"]["worst_growth"]
        vehicle = engine.days["day-1"].state.vehicle_at(DUTY, 8 * 60 + 40)
        event = {"type": "breakdown", "vehicle_id": vehicle, "at": "08:40"}
        options = ok(engine.handle("POST", "/api/days/day-1/events/options", None, event))["options"]
        nothing = next(o["index"] for o in options if o["kind"] == "none")
        ok(engine.handle("POST", "/api/days/day-1/events/apply", None,
                         {"event": event, "option": nothing}))
        after = ok(engine.handle("GET", "/api/days/day-1/intervals"))
        self.assertGreater(after["numbers"]["worst_growth"], before)
        self.assertIsNotNone(after["numbers"]["worst_route"])
        self.assertIn("вырос", after["answer"])

    def test_intervals_errors(self):
        status, payload = self.engine.handle("GET", "/api/days/day-1/intervals", {"t": "утром"}, None)
        self.assertEqual(status, 400)
        self.assertIn("ЧЧ:ММ", payload["error"])
        engine = Engine()
        second = ok(engine.handle("POST", "/api/days", None, PARK7))["day_id"]
        status, payload = engine.handle("GET", f"/api/days/{second}/intervals")
        self.assertEqual(status, 409)
        self.assertIn("Сначала постройте план", payload["error"])

    def test_explain_errors(self):
        self.assertEqual(self.engine.handle("GET", "/api/days/day-1/explain/vehicle/нет")[0], 404)
        self.assertEqual(self.engine.handle("GET", "/api/days/day-1/explain/driver/нет")[0], 404)
        status, payload = self.engine.handle("GET", f"/api/days/day-1/explain/unfilled/{DUTY}")
        self.assertEqual(status, 404)
        self.assertIn("explain/vehicle", payload["error"])
        status, payload = self.engine.handle("GET", f"/api/days/day-1/explain/interval/{ROUTE}")
        self.assertEqual(status, 400)
        self.assertIn("t", payload["error"])
        self.assertEqual(self.engine.handle("GET", f"/api/days/day-1/explain/interval/{ROUTE}", {"t": "8.30"})[0], 400)

    def test_too_early(self):
        ok(self.engine.handle("POST", "/api/days", None, PARK7))
        fresh = ok(self.engine.handle("GET", "/api/days"))["days"][-1]["day_id"]
        for method, path in (("GET", f"/api/days/{fresh}/state"), ("GET", f"/api/days/{fresh}/explain/summary"),
                             ("POST", f"/api/days/{fresh}/events/options")):
            status, payload = self.engine.handle(method, path, None, {})
            self.assertEqual(status, 409, path)
            self.assertIn("plan", payload["error"])

    def test_history_from(self):
        engine = Engine()
        ok(engine.handle("POST", "/api/days", None, PARK7))
        ok(engine.handle("POST", "/api/days", None, dict(PARK7, date="2026-10-06")))
        ok(engine.handle("POST", "/api/days/day-1/plan", None, {}))
        status, payload = engine.handle("POST", "/api/days/day-2/plan", None, {"history_from": "day-9"})
        self.assertEqual(status, 404)
        status, payload = engine.handle("POST", "/api/days/day-1/plan", None, {"history_from": "day-2"})
        self.assertEqual(status, 409)  # у day-2 плана ещё нет
        second = ok(engine.handle("POST", "/api/days/day-2/plan", None, {"history_from": "day-1"}))
        self.assertEqual(second["violations"], [])
        status, payload = engine.handle("POST", "/api/days/day-1/plan", None, {"history_from": "day-2"})
        self.assertEqual(status, 400)  # history_from должен быть раньше
        series = [(engine.days[d].day, engine.days[d].plan) for d in ("day-1", "day-2")]
        self.assertEqual(check_rest(series), [])
        self.assertTrue(engine.days["day-2"].history_before.drivers)  # водители помнят первый день


class TestEvents(unittest.TestCase):

    def setUp(self):
        self.engine = Engine()
        ok(self.engine.handle("POST", "/api/days", None, PARK7))
        ok(self.engine.handle("POST", "/api/days/day-1/plan", None, {}))
        self.vehicle = self.engine.days["day-1"].state.vehicle_at(DUTY, 8 * 60 + 40)
        self.event = {"type": "breakdown", "vehicle_id": self.vehicle, "at": "08:40"}

    def test_options(self):
        payload = ok(self.engine.handle("POST", "/api/days/day-1/events/options", None, self.event))
        self.assertEqual(payload["event"], dict(self.event, duration_min=None))
        options = payload["options"]
        self.assertTrue(1 <= len(options) <= 4)  # до трёх лучших плюс «не заменять»
        self.assertEqual([o["kind"] for o in options].count("none"), 1)
        self.assertEqual([o["index"] for o in options], list(range(len(options))))
        self.assertEqual(options[0]["cost"], min(o["cost"] for o in options))
        for option in options:
            self.assertIn(option["kind"], KINDS)
            self.assertEqual(set(option["explanation"]), {"question", "answer", "reasons", "numbers"})
        self.assertEqual(self.engine.days["day-1"].log, [])  # ничего не изменилось

    def test_every_kind_is_in_the_contract(self):
        """Вид варианта, который отдаёт движок, должен быть описан в контракте."""
        from pathlib import Path as _Path
        import re as _re
        contract = (_Path(__file__).resolve().parent.parent / "docs" / "CONTRACT.md").read_text(encoding="utf-8")
        row = next(line for line in contract.splitlines() if line.startswith("| kind |"))
        described = set(_re.findall(r"`([a-z_]+)`", row))
        self.assertEqual(set(KINDS), described, "список видов в тесте и в контракте разошёлся")
        source = (_Path(__file__).resolve().parent.parent / "naryad" / "ops" / "replan.py").read_text(encoding="utf-8")
        in_code = set(_re.findall(r'Option\(\s*"([a-z_]+)"', source))
        self.assertTrue(in_code <= described,
                        f"движок отдаёт виды, которых нет в контракте: {sorted(in_code - described)}")

    def test_apply_and_log(self):
        options = ok(self.engine.handle("POST", "/api/days/day-1/events/options", None, self.event))["options"]
        payload = ok(self.engine.handle("POST", "/api/days/day-1/events/apply", None,
                                        {"event": self.event, "option": len(options) - 1}))
        self.assertEqual(payload["violations"], [])
        self.assertEqual(payload["meta"]["events"], 1)
        self.assertEqual(payload["down_vehicles"], [{"vehicle_id": self.vehicle, "since": "08:40"}])
        duty = next(d for d in payload["duties"] if d["duty_id"] == DUTY)
        self.assertEqual(duty["vehicles"][0], {"from": "04:50", "to": "08:40", "vehicle_id": self.vehicle})
        log = ok(self.engine.handle("GET", "/api/days/day-1/log"))["log"]
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["option"], len(options) - 1)
        self.assertEqual(log[0]["title"], options[-1]["title"])
        state = ok(self.engine.handle("GET", "/api/days/day-1/state"))
        self.assertEqual(state, {k: v for k, v in payload.items() if k != "violations"})

    def test_temporary_breakdown_and_no_show(self):
        short = ok(self.engine.handle("POST", "/api/days/day-1/events/options", None,
                                      dict(self.event, duration_min=20)))
        self.assertEqual(short["event"]["duration_min"], 20)
        self.assertTrue(all(o["lost_minutes"] <= 20 for o in short["options"]))
        driver = self.engine.days["day-1"].state.driver_at(SHIFT, 5 * 60)
        payload = ok(self.engine.handle("POST", "/api/days/day-1/events/options", None,
                                        {"type": "no_show", "driver_id": driver, "at": "04:30"}))
        self.assertEqual(payload["event"], {"type": "no_show", "driver_id": driver, "at": "04:30"})
        self.assertEqual(payload["options"][0]["kind"] in ("none", "free_driver", "reserve"), True)

    def test_event_errors(self):
        cases = (({"type": "fire", "vehicle_id": self.vehicle, "at": "08:40"}, 400, "type"),
                 ({"type": "breakdown", "vehicle_id": self.vehicle, "at": "8-40"}, 400, "ЧЧ:ММ"),
                 ({"type": "breakdown", "vehicle_id": "P07-V9999", "at": "08:40"}, 404, "автобуса"),
                 ({"type": "breakdown", "vehicle_id": self.vehicle, "at": "03:00"}, 400, "не на линии"),
                 ({"type": "breakdown", "vehicle_id": self.vehicle, "at": "08:40", "duration_min": 0}, 400, "duration_min"),
                 ({"type": "no_show", "driver_id": "P07-D9999", "at": "08:40"}, 404, "водителя"))
        for body, status, word in cases:
            got, payload = self.engine.handle("POST", "/api/days/day-1/events/options", None, body)
            self.assertEqual(got, status, body)
            self.assertIn(word, payload["error"], body)
        got, payload = self.engine.handle("POST", "/api/days/day-1/events/apply", None,
                                          {"event": self.event, "option": 99})
        self.assertEqual(got, 400)
        self.assertIn("option", payload["error"])

    def test_events_in_time_order(self):
        ok(self.engine.handle("POST", "/api/days/day-1/events/apply", None, {"event": self.event, "option": 0}))
        again, payload = self.engine.handle("POST", "/api/days/day-1/events/options", None, self.event)
        self.assertEqual(again, 400)
        self.assertIn("не на линии", payload["error"])  # сошедший автобус уже не на линии
        other = self.engine.days["day-1"].state.vehicle_at("P07-R01-WD02", 8 * 60)
        early, payload = self.engine.handle("POST", "/api/days/day-1/events/options", None,
                                            {"type": "accident", "vehicle_id": other, "at": "08:00"})
        self.assertEqual(early, 400)
        self.assertIn("по порядку времени", payload["error"])

    def test_replan_clears_events(self):
        ok(self.engine.handle("POST", "/api/days/day-1/events/apply", None, {"event": self.event, "option": 0}))
        ok(self.engine.handle("POST", "/api/days/day-1/plan", None, {}))
        state = ok(self.engine.handle("GET", "/api/days/day-1/state"))
        self.assertEqual((state["meta"]["events"], state["log"], state["down_vehicles"]), (0, [], []))


class TestManualEdits(unittest.TestCase):
    """Ручная правка плана диспетчером: кого можно поставить, отказ, force, журнал."""

    def setUp(self):
        self.engine = Engine()
        ok(self.engine.handle("POST", "/api/days", None, PARK7))
        ok(self.engine.handle("POST", "/api/days/day-1/plan", None, {}))
        self.record = self.engine.days["day-1"]
        self.state = ok(self.engine.handle("GET", "/api/days/day-1/state"))

    def edit(self, body):
        return self.engine.handle("POST", "/api/days/day-1/edits", None, body)

    def options(self, **query):
        return ok(self.engine.handle("GET", "/api/days/day-1/edits/options", query, None))

    def busy_vehicle(self, except_duty: str = DUTY) -> str:
        return next(d["vehicles"][0]["vehicle_id"] for d in self.state["duties"]
                    if d["duty_id"] != except_duty and d["vehicles"])

    # --- кого можно поставить --------------------------------------------------

    def test_options_offer_only_free_and_suitable_vehicles(self):
        answer = self.options(duty_id=DUTY)
        self.assertEqual((answer["duty_id"], answer["from"], answer["to"]), (DUTY, "04:50", "25:20"))
        self.assertEqual(answer["shown"], min(answer["total"], 50))
        day, state = self.record.day, self.record.state
        duty, standing = day.duties[DUTY], set()
        for segments in state.vehicles.values():
            standing |= {seg.who for seg in segments}
        self.assertTrue(answer["total"] > 0)
        for item in answer["vehicles"]:
            vehicle = day.vehicles[item["vehicle_id"]]
            self.assertNotIn(vehicle.id, standing, "предложен автобус, который уже на наряде")
            self.assertEqual(vehicle.condition, "ok")
            self.assertEqual(vehicle.park_id, duty.park_id)
            self.assertIn(vehicle.cls, day.routes[duty.route_id].allowed_classes)

    def test_options_need_exactly_one_target(self):
        self.assertEqual(self.engine.handle("GET", "/api/days/day-1/edits/options", {}, None)[0], 400)
        self.assertEqual(self.engine.handle("GET", "/api/days/day-1/edits/options",
                                            {"duty_id": DUTY, "shift_id": SHIFT}, None)[0], 400)

    def test_offered_vehicle_can_actually_be_set(self):
        """Что показано в списке выбора, то и ставится без нарушений."""
        for item in self.options(duty_id=DUTY)["vehicles"][:3]:
            engine = Engine()
            ok(engine.handle("POST", "/api/days", None, PARK7))
            ok(engine.handle("POST", "/api/days/day-1/plan", None, {}))
            status, payload = engine.handle("POST", "/api/days/day-1/edits", None,
                                            {"type": "set_vehicle", "duty_id": DUTY,
                                             "vehicle_id": item["vehicle_id"]})
            self.assertEqual((status, payload["new_violations"]), (200, []), item)

    def test_offered_driver_can_actually_be_set(self):
        answer = self.options(shift_id=SHIFT)
        self.assertTrue(answer["total"] > 0)
        self.assertEqual(answer["for_vehicle"], self.record.state.vehicle_at(DUTY, 4 * 60 + 50))
        status, payload = self.edit({"type": "set_driver", "shift_id": SHIFT,
                                     "driver_id": answer["drivers"][0]["driver_id"]})
        self.assertEqual((status, payload["new_violations"]), (200, []))

    # --- постановка и снятие ---------------------------------------------------

    def test_set_free_vehicle_changes_state_and_log(self):
        vehicle = self.options(duty_id=DUTY)["vehicles"][0]["vehicle_id"]
        payload = ok(self.edit({"type": "set_vehicle", "duty_id": DUTY, "vehicle_id": vehicle}))
        self.assertEqual(payload["new_violations"], [])
        duty = next(d for d in payload["duties"] if d["duty_id"] == DUTY)
        self.assertEqual(duty["vehicles"], [{"from": "04:50", "to": "25:20", "vehicle_id": vehicle}])
        self.assertEqual((payload["meta"]["edits"], payload["meta"]["events"]), (1, 0))
        entry = payload["log"][0]
        self.assertEqual(entry["kind"], "edit")
        self.assertEqual(entry["type"], "set_vehicle")
        self.assertEqual((entry["forced"], entry["violations"]), (False, []))
        self.assertIn("Диспетчер поставил автобус", entry["title"])
        state = ok(self.engine.handle("GET", "/api/days/day-1/state"))
        self.assertEqual(state, {k: v for k, v in payload.items()
                                 if k not in ("violations", "new_violations")})

    def test_clear_puts_duty_into_unfilled_and_set_takes_it_back(self):
        payload = ok(self.edit({"type": "clear_vehicle", "duty_id": DUTY}))
        self.assertNotIn(DUTY, [d["duty_id"] for d in payload["duties"]])
        self.assertIn({"id": DUTY, "reason": "removed_by_dispatcher"}, payload["unfilled"])
        vehicle = self.options(duty_id=DUTY)["vehicles"][0]["vehicle_id"]
        back = ok(self.edit({"type": "set_vehicle", "duty_id": DUTY, "vehicle_id": vehicle}))
        self.assertNotIn(DUTY, [u["id"] for u in back["unfilled"]])

    def test_window_leaves_the_rest_of_the_duty_alone(self):
        before = next(d for d in self.state["duties"] if d["duty_id"] == DUTY)["vehicles"][0]
        payload = ok(self.edit({"type": "clear_vehicle", "duty_id": DUTY, "from": "10:00", "to": "12:00"}))
        duty = next(d for d in payload["duties"] if d["duty_id"] == DUTY)
        self.assertEqual(duty["vehicles"], [{"from": before["from"], "to": "10:00",
                                             "vehicle_id": before["vehicle_id"]},
                                            {"from": "12:00", "to": before["to"],
                                             "vehicle_id": before["vehicle_id"]}])
        self.assertEqual(payload["unfilled"], [], "наряд закрыт частично, он не незакрытый")

    def test_dispatcher_moves_bus_from_one_duty_to_another(self):
        """Снять с второстепенного и поставить на важный - две правки, ноль нарушений."""
        donor = next(d["duty_id"] for d in self.state["duties"] if d["duty_id"] != DUTY)
        moved = next(d["vehicles"][0]["vehicle_id"] for d in self.state["duties"]
                     if d["duty_id"] == donor)
        ok(self.edit({"type": "clear_vehicle", "duty_id": donor, "reason": "нужен на важном маршруте"}))
        offered = [v["vehicle_id"] for v in self.options(duty_id=DUTY)["vehicles"]]
        self.assertIn(moved, offered, "освободившийся автобус должен попасть в список выбора")
        payload = ok(self.edit({"type": "set_vehicle", "duty_id": DUTY, "vehicle_id": moved}))
        self.assertEqual(payload["new_violations"], [])
        self.assertEqual(payload["meta"]["edits"], 2)
        self.assertEqual(payload["log"][0]["reason"], "нужен на важном маршруте")

    # --- отказ и ответственность диспетчера -------------------------------------

    def test_edit_against_norms_is_refused_and_changes_nothing(self):
        busy = self.busy_vehicle()
        status, payload = self.edit({"type": "set_vehicle", "duty_id": DUTY, "vehicle_id": busy})
        self.assertEqual(status, 409)
        self.assertEqual([v["code"] for v in payload["violations"]], ["vehicle_twice"])
        self.assertIn("force", payload["hint"])
        self.assertEqual(ok(self.engine.handle("GET", "/api/days/day-1/state")), self.state,
                         "отклонённая правка не должна менять состояние дня")

    def test_force_applies_and_records_what_was_broken(self):
        busy = self.busy_vehicle()
        payload = ok(self.edit({"type": "set_vehicle", "duty_id": DUTY, "vehicle_id": busy,
                                "force": True, "reason": "распоряжение начальника колонны"}))
        self.assertEqual([v["code"] for v in payload["new_violations"]], ["vehicle_twice"])
        entry = payload["log"][0]
        self.assertTrue(entry["forced"])
        self.assertEqual(entry["reason"], "распоряжение начальника колонны")
        self.assertEqual([v["code"] for v in entry["violations"]], ["vehicle_twice"])

    def test_old_violations_are_not_blamed_on_the_next_edit(self):
        """Нарушение, которое уже было в дне, не мешает следующей законной правке."""
        filled = [d for d in self.state["duties"] if d["duty_id"] != DUTY and d["vehicles"]]
        donor, third = filled[0]["duty_id"], filled[1]["duty_id"]
        ok(self.edit({"type": "set_vehicle", "duty_id": DUTY, "force": True,
                      "vehicle_id": filled[0]["vehicles"][0]["vehicle_id"]}))
        payload = ok(self.edit({"type": "clear_vehicle", "duty_id": third}))
        self.assertEqual(payload["new_violations"], [], "старое нарушение приписано новой правке")
        self.assertEqual([v["code"] for v in payload["violations"]], ["vehicle_twice"],
                         "общий список нарушений дня должен помнить прежнее нарушение")
        self.assertIn(donor, payload["violations"][0]["ids"])

    # --- связь с остальным API ---------------------------------------------------

    def test_event_after_edit_still_works(self):
        """Запись правки в журнале не должна ломать проверку порядка событий."""
        other = next(d["duty_id"] for d in self.state["duties"] if d["duty_id"] != DUTY)
        ok(self.edit({"type": "clear_vehicle", "duty_id": other}))
        vehicle = self.record.state.vehicle_at(DUTY, 8 * 60 + 40)
        event = {"type": "breakdown", "vehicle_id": vehicle, "at": "08:40"}
        payload = ok(self.engine.handle("POST", "/api/days/day-1/events/apply", None,
                                        {"event": event, "option": 0}))
        self.assertEqual((payload["meta"]["events"], payload["meta"]["edits"]), (1, 1))
        self.assertEqual([e["kind"] for e in payload["log"]], ["edit", "event"])

    def test_driver_memory_follows_the_edit(self):
        driver = self.record.state.driver_at(SHIFT, 5 * 60)
        self.assertIsNotNone(self.record.history_after.drivers.get(driver))
        ok(self.edit({"type": "clear_driver", "shift_id": SHIFT}))
        self.assertIsNone(self.record.history_after.drivers.get(driver, DriverState()).last_end,
                          "снятый водитель не должен считаться работавшим")

    def test_replan_clears_edits(self):
        ok(self.edit({"type": "clear_vehicle", "duty_id": DUTY}))
        ok(self.engine.handle("POST", "/api/days/day-1/plan", None, {}))
        state = ok(self.engine.handle("GET", "/api/days/day-1/state"))
        self.assertEqual((state["meta"]["edits"], state["log"]), (0, []))
        self.assertEqual(state, self.state)

    def test_edit_needs_a_plan(self):
        second = ok(self.engine.handle("POST", "/api/days", None, PARK7))["day_id"]
        for method, path, query in (("POST", f"/api/days/{second}/edits", None),
                                    ("GET", f"/api/days/{second}/edits/options", {"duty_id": DUTY})):
            status, payload = self.engine.handle(method, path, query,
                                                 {"type": "clear_vehicle", "duty_id": DUTY})
            self.assertEqual(status, 409, path)
            self.assertIn("Сначала постройте план", payload["error"])

    def test_edit_errors(self):
        cases = [
            ({"type": "нет"}, 400, "type:"),
            ({"type": "clear_vehicle"}, 400, "нужно поле duty_id"),
            ({"type": "clear_driver"}, 400, "нужно поле shift_id"),
            ({"type": "clear_vehicle", "duty_id": "нет такого"}, 404, "Нет наряда"),
            ({"type": "clear_driver", "shift_id": "нет такой"}, 404, "Нет смены"),
            ({"type": "set_vehicle", "duty_id": DUTY, "vehicle_id": "нет"}, 404, "Нет автобуса"),
            ({"type": "set_driver", "shift_id": SHIFT, "driver_id": "нет"}, 404, "Нет водителя"),
            ({"type": "set_vehicle", "duty_id": DUTY}, 400, "нужно поле vehicle_id"),
            ({"type": "set_driver", "shift_id": SHIFT}, 400, "нужно поле driver_id"),
            ({"type": "set_driver", "shift_id": SHIFT, "driver_id": 7}, 400, "нужно поле driver_id"),
            ({"type": "clear_vehicle", "duty_id": DUTY, "from": "03:00"}, 400, "выходит за время"),
            ({"type": "clear_vehicle", "duty_id": DUTY, "from": "12:00", "to": "10:00"}, 400, "пустое"),
            ({"type": "clear_vehicle", "duty_id": DUTY, "from": "утром"}, 400, "ЧЧ:ММ"),
            ({"type": "clear_vehicle", "duty_id": DUTY, "force": "да"}, 400, "force:"),
            ({"type": "clear_vehicle", "duty_id": DUTY, "reason": 5}, 400, "reason:"),
            ([], 400, "объект JSON"),
        ]
        for body, code, text in cases:
            status, payload = self.edit(body)
            self.assertEqual(status, code, body)
            self.assertIn(text, payload["error"], body)
        status, payload = self.engine.handle("GET", "/api/days/day-1/edits/options", {"duty_id": ""}, None)
        self.assertEqual((status, payload["error"]), (400, "нужно поле duty_id: номер наряда"))
        self.assertEqual(self.engine.days["day-1"].log, [], "ошибки не должны ничего записывать")

    # --- список выбора обязан быть честным: всё из него ставится без нарушений ----

    def test_options_do_not_touch_driver_memory(self):
        """GET ничего не меняет: память водителей не растёт от просмотра списка."""
        state = self.record.state
        before = len(state.history.drivers)
        self.options(shift_id=SHIFT)
        self.options(duty_id=DUTY)
        self.assertEqual(len(state.history.drivers), before,
                         "просмотр кандидатов дописал пустые записи в память водителей")

    def test_own_vehicle_is_false_when_the_duty_has_no_bus(self):
        """None == None не делает водителя без закреплённого автобуса «своим»."""
        ok(self.edit({"type": "clear_driver", "shift_id": SHIFT}))
        ok(self.edit({"type": "clear_vehicle", "duty_id": DUTY}))
        answer = self.options(shift_id=SHIFT)
        self.assertIsNone(answer["for_vehicle"])
        self.assertEqual(answer["for_vehicles"], [])
        self.assertTrue(answer["drivers"])
        self.assertFalse(any(d["own_vehicle"] for d in answer["drivers"]))

    def test_broken_bus_needs_force_on_every_duty(self):
        """Неисправный автобус, уже поставленный с force на один наряд, на другой без force не идёт."""
        day = self.record.day
        broken = next(v.id for v in sorted(day.vehicles.values(), key=lambda v: v.id)
                      if v.condition != "ok" and v.cls in day.routes[day.duties[DUTY].route_id].allowed_classes)
        first, second = "P07-R01-WD02", "P07-R01-WD03"
        self.assertEqual(self.edit({"type": "set_vehicle", "duty_id": first, "vehicle_id": broken,
                                    "from": "10:00", "to": "11:00"})[0], 409)
        ok(self.edit({"type": "set_vehicle", "duty_id": first, "vehicle_id": broken,
                      "from": "10:00", "to": "11:00", "force": True}))
        status, payload = self.edit({"type": "set_vehicle", "duty_id": second, "vehicle_id": broken,
                                     "from": "12:00", "to": "13:00"})
        self.assertEqual(status, 409, "вторая постановка неисправного автобуса прошла без force")
        self.assertIn("vehicle_broken", [v["code"] for v in payload["violations"]])
        forced = ok(self.edit({"type": "set_vehicle", "duty_id": second, "vehicle_id": broken,
                               "from": "12:00", "to": "13:00", "force": True}))
        self.assertTrue(forced["log"][-1]["forced"])
        self.assertEqual([v["code"] for v in forced["new_violations"]], ["vehicle_broken"])

    def test_bus_in_repair_after_a_short_breakdown_is_not_offered(self):
        """Сошедший на два часа автобус в окне ремонта не предлагается и не ставится."""
        bus = self.record.state.vehicle_at(DUTY, 8 * 60 + 40)
        event = {"type": "breakdown", "vehicle_id": bus, "at": "08:40", "duration_min": 120}
        after = ok(self.engine.handle("POST", "/api/days/day-1/events/apply", None,
                                      {"event": event, "option": 0}))
        self.assertEqual(after["repairs"], [{"vehicle_id": bus, "from": "08:40", "to": "10:40"}])
        self.assertEqual(after["down_vehicles"], [], "сход на время - не выбытие")
        other = "P07-R01-WD02"
        offered = [v["vehicle_id"] for v in self.options(duty_id=other, **{"from": "09:00", "to": "10:00"})["vehicles"]]
        self.assertNotIn(bus, offered)
        status, payload = self.edit({"type": "set_vehicle", "duty_id": other, "vehicle_id": bus,
                                     "from": "09:00", "to": "10:00"})
        self.assertEqual(status, 409)
        self.assertEqual([v["code"] for v in payload["violations"]], ["in_repair"])
        self.assertIn("10:40", payload["violations"][0]["text"])

    def test_offered_vehicle_fits_the_permits_of_the_drivers_on_the_duty(self):
        """На маршруте с двумя классами предлагать можно только то, что поведут водители смен."""
        raw = generate(preset="park7", day="2026-10-05", seed=1, moment="morning")
        next(r for r in raw["routes"] if r["id"] == ROUTE)["allowed_classes"] = ["big", "extra_big"]
        engine = Engine()
        ok(engine.handle("POST", "/api/days", None, raw))
        ok(engine.handle("POST", "/api/days/day-1/plan", None, {}))
        day, state = engine.days["day-1"].day, engine.days["day-1"].state
        duty = next(d for d in sorted(day.duties.values(), key=lambda d: d.id)
                    if d.route_id == ROUTE and any(
                        "extra_big" not in day.drivers[s.who].classes
                        for sh in day.shifts_by_duty[d.id] for s in state.drivers.get(sh.id, [])))
        answer = ok(engine.handle("GET", "/api/days/day-1/edits/options", {"duty_id": duty.id}, None))
        self.assertTrue(answer["vehicles"])
        self.assertEqual([v for v in answer["vehicles"] if v["class"] == "extra_big"], [],
                         "предложен автобус, к классу которого у водителя смены нет допуска")
        for item in answer["vehicles"][:3]:
            status, payload = engine.handle("POST", "/api/days/day-1/edits", None,
                                            {"type": "set_vehicle", "duty_id": duty.id,
                                             "vehicle_id": item["vehicle_id"]})
            self.assertEqual((status, payload["new_violations"]), (200, []), item)
            ok(engine.handle("POST", "/api/days/day-1/plan", None, {}))

    def test_offered_driver_fits_every_bus_in_the_window(self):
        """Если в окне смены на наряде два автобуса разных классов, нужен допуск к обоим."""
        raw = generate(preset="park7", day="2026-10-05", seed=1, moment="morning")
        next(r for r in raw["routes"] if r["id"] == ROUTE)["allowed_classes"] = ["big", "extra_big"]
        engine = Engine()
        ok(engine.handle("POST", "/api/days", None, raw))
        ok(engine.handle("POST", "/api/days/day-1/plan", None, {}))
        day, state = engine.days["day-1"].day, engine.days["day-1"].state
        duty, shift = "P07-R01-WD02", "P07-R01-WD02-S1"
        big = state.vehicle_at(duty, 9 * 60)
        extra = next(v.id for v in sorted(day.vehicles.values(), key=lambda v: v.id)
                     if v.cls == "extra_big" and v.condition == "ok" and not state.vehicle_busy_after(v.id, 0))
        ok(engine.handle("POST", "/api/days/day-1/edits", None, {"type": "clear_driver", "shift_id": shift}))
        ok(engine.handle("POST", "/api/days/day-1/edits", None,
                         {"type": "set_vehicle", "duty_id": duty, "vehicle_id": extra, "from": "09:00",
                          "to": hm(day.shifts[shift].end)}))
        answer = ok(engine.handle("GET", "/api/days/day-1/edits/options", {"shift_id": shift}, None))
        self.assertEqual(answer["for_vehicles"], [big, extra])
        self.assertEqual(answer["for_vehicle"], big)
        self.assertTrue(answer["drivers"])
        for item in answer["drivers"]:
            self.assertTrue({"big", "extra_big"} <= set(day.drivers[item["driver_id"]].classes), item)
        status, payload = engine.handle("POST", "/api/days/day-1/edits", None,
                                        {"type": "set_driver", "shift_id": shift,
                                         "driver_id": answer["drivers"][0]["driver_id"]})
        self.assertEqual((status, payload["new_violations"]), (200, []))

    def test_transferred_bus_cannot_be_set_in_its_home_park(self):
        """Автобус, утром отданный соседнему парку, на наряд родного парка не ставится."""
        engine = Engine()
        ok(engine.handle("POST", "/api/days", None, city_day()))
        ok(engine.handle("POST", "/api/days/day-1/plan", None, {}))
        day, state = engine.days["day-1"].day, engine.days["day-1"].state
        moved, to_park = next(iter(sorted(state.transfers.items())))
        home = day.vehicles[moved].park_id
        self.assertNotEqual(home, to_park)
        busy = next(d for d, segs in state.vehicles.items() if any(s.who == moved for s in segs))
        ok(engine.handle("POST", "/api/days/day-1/edits", None, {"type": "clear_vehicle", "duty_id": busy}))
        target = next(d.id for d in sorted(day.duties.values(), key=lambda d: d.id)
                      if d.park_id == home and d.type == "line"
                      and day.vehicles[moved].cls in day.routes[d.route_id].allowed_classes)
        offered = ok(engine.handle("GET", "/api/days/day-1/edits/options", {"duty_id": target}, None))
        self.assertNotIn(moved, [v["vehicle_id"] for v in offered["vehicles"]])
        status, payload = engine.handle("POST", "/api/days/day-1/edits", None,
                                        {"type": "set_vehicle", "duty_id": target, "vehicle_id": moved})
        self.assertEqual(status, 409)
        self.assertEqual([v["code"] for v in payload["violations"]], ["vehicle_park"])
        self.assertIn(to_park, payload["violations"][0]["text"])

    def test_plan_note_counts_events_and_edits_apart(self):
        """Заметка плана не называет ручные правки событиями."""
        bus = self.record.state.vehicle_at(DUTY, 8 * 60 + 40)
        ok(self.engine.handle("POST", "/api/days/day-1/events/apply", None,
                              {"event": {"type": "breakdown", "vehicle_id": bus, "at": "08:40"}, "option": 0}))
        other = "P07-R02-WD01"
        ok(self.edit({"type": "clear_vehicle", "duty_id": other}))
        ok(self.edit({"type": "set_vehicle", "duty_id": other,
                      "vehicle_id": self.options(duty_id=other)["vehicles"][0]["vehicle_id"]}))
        second = ok(self.engine.handle("POST", "/api/days", None, dict(PARK7, date="2026-10-06")))["day_id"]
        notes = ok(self.engine.handle("POST", f"/api/days/{second}/plan", None, {"history_from": "day-1"}))["notes"]
        self.assertTrue(any("событий 1 и ручных правок 2" in note for note in notes), notes)


class TestRegistryImport(unittest.TestCase):
    """Загрузка реестра перевозчика через API: CSV, Excel, отчёт о понятом."""

    META = {"date": "2026-10-05", "day_type": "weekday", "moment": "morning"}

    def setUp(self):
        self.engine = Engine()

    @staticmethod
    def park7_csv() -> dict:
        from pathlib import Path as _Path
        folder = _Path(__file__).resolve().parent.parent / "data" / "samples" / "park7_weekday_csv"
        return {path.stem: path.read_text(encoding="utf-8-sig") for path in sorted(folder.glob("*.csv"))}

    def post(self, body):
        return self.engine.handle("POST", "/api/days/import", None, body)

    def test_park7_csv_becomes_a_day_that_can_be_planned(self):
        payload = ok(self.post({"files": self.park7_csv(), "meta": self.META}))
        self.assertEqual(payload["day_id"], "day-1")
        self.assertEqual(payload["counts"], {"parks": 1, "routes": 24, "duties": 350,
                                             "shifts": 717, "vehicles": 420, "drivers": 1315})
        self.assertEqual(payload["report"]["errors"], [])
        plan = ok(self.engine.handle("POST", "/api/days/day-1/plan", None, {}))
        self.assertEqual(plan["violations"], [])
        self.assertEqual(len(plan["plan"]["vehicle_assignments"]), 350)

    def test_russian_registry_is_understood_and_reported(self):
        from tests.test_registry import RUSSIAN
        payload = ok(self.post({"files": RUSSIAN, "meta": self.META}))
        line = next(x for x in payload["report"]["entities"] if x["entity"] == "vehicles")
        self.assertEqual(line["matched"]["condition"], "Тех. состояние")
        self.assertEqual(line["derived"], ["id"], "код собирается из гаражного номера")
        self.assertTrue(payload["warnings"], "то, что заполнено само, должно быть видно")
        plan = ok(self.engine.handle("POST", f"/api/days/{payload['day_id']}/plan", None, {}))
        self.assertEqual(plan["violations"], [])

    def test_excel_workbook(self):
        from tests.test_registry import TestExcel, workbook
        from naryad.data.registry import encode
        payload = ok(self.post({"workbook": encode(workbook(TestExcel.SHEETS)), "meta": self.META}))
        self.assertEqual(payload["counts"]["duties"], 2)
        plan = ok(self.engine.handle("POST", f"/api/days/{payload['day_id']}/plan", None, {}))
        self.assertEqual(len(plan["plan"]["vehicle_assignments"]), 2)

    def test_dry_run_reads_but_creates_nothing(self):
        payload = ok(self.post({"files": self.park7_csv(), "meta": self.META, "dry_run": True}))
        self.assertIsNone(payload["day_id"])
        self.assertEqual(payload["counts"]["vehicles"], 420)
        self.assertEqual(ok(self.engine.handle("GET", "/api/days"))["days"], [])

    def test_dry_run_answer_has_the_same_shape_as_the_real_one(self):
        """Интерфейс показывает «вот что я понял» по warnings и при предварительном просмотре."""
        from tests.test_registry import RUSSIAN
        dry = ok(self.post({"files": RUSSIAN, "meta": self.META, "dry_run": True}))
        real = ok(self.post({"files": RUSSIAN, "meta": self.META}))
        self.assertEqual(set(dry), set(real))
        self.assertEqual((dry["moment"], dry["preset"]), ("morning", None))
        self.assertTrue(dry["warnings"])
        self.assertEqual(dry["warnings"], real["warnings"])

    def test_warnings_are_not_doubled(self):
        """Общая проверка считается один раз: предупреждение в ответе одно, а не два."""
        from tests.test_registry import RUSSIAN
        payload = ok(self.post({"files": RUSSIAN, "meta": self.META}))
        self.assertEqual(len(payload["warnings"]), len(set(payload["warnings"])), payload["warnings"])
        self.assertEqual(payload["warnings"], payload["report"]["warnings"])

    def test_broken_workbook_is_refused_not_crashed(self):
        import io as _io
        import zipfile as _zipfile
        from naryad.data.registry import encode
        buffer = _io.BytesIO()
        with _zipfile.ZipFile(buffer, "w") as book:
            book.writestr("xl/workbook.xml", "<<< это не xml")
        status, payload = self.post({"workbook": encode(buffer.getvalue()), "meta": self.META})
        self.assertEqual(status, 400)
        self.assertIn("повреждена", payload["error"])
        self.assertIn("xl/workbook.xml", payload["error"])

    def test_base64_with_line_breaks_is_read(self):
        import base64 as _base64
        from tests.test_registry import TestExcel, workbook
        wrapped = _base64.encodebytes(workbook(TestExcel.SHEETS)).decode()
        self.assertIn("\n", wrapped)
        payload = ok(self.post({"workbook": wrapped, "meta": self.META}))
        self.assertEqual(payload["counts"]["duties"], 2)

    def test_unclosed_quote_in_a_big_csv_is_a_400(self):
        from tests.test_registry import RUSSIAN
        files = dict(RUSSIAN)
        files["Автобусы"] = ("Гаражный номер;Класс;Топливо;Парк;Тех. состояние\n"
                            "\"7002;большой;газ;П7;исправен\n" + "7003;большой;газ;П7;исправен\n" * 6000)
        status, payload = self.post({"files": files, "meta": self.META})
        self.assertEqual(status, 400)
        self.assertIn("Автобусы", payload["error"])
        self.assertIn("кавычка", payload["error"])

    def test_numeric_overflow_is_an_unknown_value(self):
        from tests.test_registry import RUSSIAN
        files = dict(RUSSIAN)
        files["Маршруты"] = files["Маршруты"].replace(";160", ";1e999")
        status, payload = self.post({"files": files, "meta": self.META})
        self.assertEqual(status, 400)
        self.assertNotIn("kind", payload, "переполнение числа дошло до 500")
        self.assertTrue(any("1e999" in e and "Время оборота" in e for e in payload["report"]["errors"]),
                        payload["report"]["errors"])

    def test_registry_with_errors_is_refused_with_the_report(self):
        files = self.park7_csv()
        files["vehicles"] = files["vehicles"].replace("ok", "работает как-то")
        status, payload = self.post({"files": files, "meta": self.META})
        self.assertEqual(status, 400)
        self.assertIn("с ошибками", payload["error"])
        self.assertTrue(any("работает как-то" in e for e in payload["report"]["errors"]))
        self.assertEqual(payload["report"]["unknown_values"][0]["field"], "condition")
        self.assertEqual(ok(self.engine.handle("GET", "/api/days"))["days"], [],
                         "отклонённый реестр не должен создавать день")

    def test_import_errors(self):
        cases = [
            ({}, "хотя бы один файл"),
            ({"files": self.park7_csv()}, "meta:"),
            ({"files": self.park7_csv(), "meta": {"date": "2026-10-05"}}, "meta:"),
            ({"files": {"parks": 1}, "meta": self.META}, "files:"),
            ({"files": {str(i): "" for i in range(30)}, "meta": self.META}, "не больше"),
            ({"workbook": "это не base64!!", "meta": self.META}, "base64"),
            ({"workbook": 5, "meta": self.META}, "workbook:"),
            ({"files": self.park7_csv(), "meta": self.META, "dry_run": "да"}, "dry_run:"),
            ([], "объект JSON"),
        ]
        for body, text in cases:
            status, payload = self.post(body)
            self.assertEqual(status, 400, body if isinstance(body, list) else sorted(body))
            self.assertIn(text, payload["error"])

    def test_not_an_excel_file(self):
        from naryad.data.registry import encode
        status, payload = self.post({"workbook": encode(b"obviously not a workbook"),
                                     "meta": self.META})
        self.assertEqual(status, 400)
        self.assertIn("zip", payload["error"])


class TestNoCrash(unittest.TestCase):
    """Ошибка не уходит наружу обрывом связи и не ломает сервис для следующих запросов."""

    def setUp(self):
        self.engine = Engine()
        ok(self.engine.handle("POST", "/api/days", None, PARK7))

    def days(self) -> int:
        return len(ok(self.engine.handle("GET", "/api/days"))["days"])

    def test_day_without_meta_rejected_and_list_survives(self):
        empty = {"meta": {}, "parks": [], "routes": [], "duties": [],
                 "shifts": [], "vehicles": [], "drivers": []}
        status, payload = self.engine.handle("POST", "/api/days", None, empty)
        self.assertEqual(status, 400)
        self.assertIn("meta", json.dumps(payload, ensure_ascii=False))
        self.assertEqual(self.days(), 1, "битый день не должен попадать в память")
        self.assertEqual(ok(self.engine.handle("GET", "/api/days/day-1"))["meta"]["date"], "2026-10-05")

    def test_day_that_cannot_be_read_is_rejected(self):
        raw = ok(self.engine.handle("GET", "/api/days/day-1"))
        spoiled = dict(raw, meta=dict(raw["meta"]), duties=[dict(d) for d in raw["duties"]])
        spoiled["duties"][0]["start"] = "утром"  # вместо ЧЧ:ММ
        status, payload = self.engine.handle("POST", "/api/days", None, spoiled)
        self.assertIn(status, (400, 500))
        self.assertEqual(self.days(), 1)

    def test_bodies_that_are_not_objects(self):
        for body in ([1, 2], "привет", 7, True):
            for path in ("/api/days", "/api/days/day-1/plan"):
                status, payload = self.engine.handle("POST", path, None, body)
                self.assertEqual(status, 400, (path, body))
                self.assertIn("error", payload)

    def test_engine_error_becomes_understandable_500(self):
        self.engine.days["day-1"].day.meta.pop("day_type")  # имитируем ошибку движка
        with contextlib.redirect_stderr(io.StringIO()) as noise:
            status, payload = self.engine.handle("GET", "/api/days")
        self.assertEqual(status, 500)
        self.assertIn("Внутренняя ошибка", payload["error"])
        self.assertNotIn("Traceback", json.dumps(payload, ensure_ascii=False))
        self.assertIn("Traceback", noise.getvalue(), "трассировка должна уйти в журнал сервера")

    def test_rejected_replan_keeps_plan_and_driver_memory(self):
        ok(self.engine.handle("POST", "/api/days/day-1/plan", None, {}))
        record = self.engine.days["day-1"]
        before = len(record.history_after.drivers)
        self.assertGreater(before, 0)
        for body, status in (({"history_from": 123}, 400), ({"history_from": "day-9"}, 404),
                             ({"gas_parks": ["P99"]}, 404), ({"transfers": "да"}, 400)):
            got, _ = self.engine.handle("POST", "/api/days/day-1/plan", None, body)
            self.assertEqual(got, status, body)
            self.assertIsNotNone(record.plan, body)
            self.assertEqual(len(record.history_after.drivers), before, body)
            self.assertEqual(len(record.history_before.drivers), 0, body)

    def test_second_event_answers_instead_of_breaking(self):
        """Событие, после которого дневная проверка что-то нашла, раньше рвало связь."""
        ok(self.engine.handle("POST", "/api/days/day-1/plan", None, {}))
        state = self.engine.days["day-1"].state
        first = {"type": "breakdown", "vehicle_id": state.vehicle_at(DUTY, 8 * 60 + 40), "at": "08:40"}
        ok(self.engine.handle("POST", "/api/days/day-1/events/apply", None, {"event": first, "option": 1}))
        second = {"type": "accident",
                  "vehicle_id": self.engine.days["day-1"].state.vehicle_at(DUTY, 9 * 60 + 40),
                  "at": "09:40"}
        after = ok(self.engine.handle("POST", "/api/days/day-1/events/apply", None,
                                      {"event": second, "option": 0}))
        self.assertEqual(after["meta"]["events"], 2)
        for violation in after["violations"]:
            self.assertEqual(set(violation), {"code", "text", "ids"})
            self.assertTrue(violation["code"] and violation["text"])
        self.assertEqual(len(ok(self.engine.handle("GET", "/api/days/day-1/log"))["log"]), 2)


class TestTransfers(unittest.TestCase):
    """План на несколько парков: лишние автобусы закрывают наряды соседа (А7)."""

    def setUp(self):
        self.engine = Engine()

    def add(self, raw: dict) -> str:
        return ok(self.engine.handle("POST", "/api/days", None, raw))["day_id"]

    def plan(self, day_id: str, body: dict | None = None) -> dict:
        return ok(self.engine.handle("POST", f"/api/days/{day_id}/plan", None, body or {}))

    def test_city_transfers_by_default(self):
        day_id = self.add(city_day())
        planned = self.plan(day_id)
        transfers = planned["plan"]["transfers"]
        self.assertTrue(transfers, "в городе с нехваткой переброски должны появиться")
        self.assertEqual(planned["violations"], [])
        self.assertTrue(all(set(t) == {"vehicle_id", "to_park"} for t in transfers))
        self.assertEqual(ok(self.engine.handle("GET", f"/api/days/{day_id}/state"))["transfers"],
                         transfers)

    def test_without_transfers_fewer_duties_closed(self):
        day_id = self.add(city_day())
        with_them = len(self.plan(day_id)["plan"]["vehicle_assignments"])
        without = self.plan(day_id, {"transfers": False})
        self.assertEqual(without["plan"]["transfers"], [])
        self.assertEqual(without["violations"], [])
        self.assertLess(len(without["plan"]["vehicle_assignments"]), with_them)

    def test_one_park_has_nothing_to_transfer(self):
        day_id = self.add(city_day(parks=1, short_park="P01"))
        self.assertEqual(self.plan(day_id, {"transfers": True})["plan"]["transfers"], [])

    def test_gas_vehicle_only_to_park_with_gas(self):
        day_id = self.add(city_day(gas_park="P01"))
        default = self.plan(day_id)["plan"]["transfers"]
        self.assertTrue(default, "без ограничения газовые автобусы перебрасываются")
        both = self.plan(day_id, {"gas_parks": ["P01", "P02"]})["plan"]["transfers"]
        self.assertEqual(len(both), len(default))
        blocked = self.plan(day_id, {"gas_parks": ["P01"]})
        self.assertEqual(blocked["plan"]["transfers"], [],
                         "в парк без газовой инфраструктуры газовый автобус не отдаём")
        self.assertEqual(blocked["violations"], [])

    def test_bad_transfer_options(self):
        day_id = self.add(city_day())
        cases = (({"transfers": "да"}, 400, "transfers"),
                 ({"gas_parks": "P01"}, 400, "gas_parks"),
                 ({"gas_parks": [1]}, 400, "gas_parks"),
                 ({"gas_parks": ["P09"]}, 404, "P09"))
        for body, status, word in cases:
            got, payload = self.engine.handle("POST", f"/api/days/{day_id}/plan", None, body)
            self.assertEqual(got, status, body)
            self.assertIn(word, payload["error"], body)


class TestRestBetweenDays(unittest.TestCase):
    """Нормы отдыха между днями: главное, что мы продаём, должно быть видно в ответе.

    До этой правки API отвечал «нарушений 0», ничего про отдых не проверив:
    check_rest вызывался только из командной строки.
    """

    def setUp(self):
        self.engine = Engine()
        self.ids = [ok(self.engine.handle("POST", "/api/days", None, dict(PARK7, date=date)))["day_id"]
                    for date in ("2026-10-05", "2026-10-06", "2026-10-07")]

    def plan(self, index, body=None):
        return ok(self.engine.handle("POST", f"/api/days/{self.ids[index]}/plan", None, body or {}))

    def test_first_day_is_clean(self):
        first = self.plan(0)
        self.assertEqual(first["violations"], [])
        self.assertEqual(first["rest_violations"], [])
        self.assertEqual(first["notes"], [])

    def test_days_planned_separately_show_rest_violations(self):
        self.plan(0)
        second = self.plan(1)
        self.assertEqual(second["violations"], [], "сам день законен")
        self.assertTrue(second["rest_violations"], "а отдых между днями нарушен")
        self.assertTrue(any("history_from" in note for note in second["notes"]))
        codes = {v["code"] for v in second["rest_violations"]}
        self.assertTrue(codes <= {"daily_rest", "rest_ratio", "weekly_rest"}, codes)
        for violation in second["rest_violations"]:
            self.assertEqual(set(violation), {"code", "text", "ids"})

    def test_chained_days_have_no_rest_violations(self):
        self.plan(0)
        self.assertEqual(self.plan(1, {"history_from": self.ids[0]})["rest_violations"], [])
        self.assertEqual(self.plan(2, {"history_from": self.ids[1]})["rest_violations"], [])

    def test_memory_follows_what_actually_happened(self):
        """Водитель, которого не допустили до начала смены, не считается работавшим."""
        self.plan(0)
        record = self.engine.days[self.ids[0]]
        state, day = record.state, record.day
        shift = min((s for s in day.shifts.values()
                     if s.start > 9 * 60 and day.duties[s.duty_id].type == "line"
                     and state.driver_at(s.id, s.start) is not None),
                    key=lambda s: (s.start, s.id))
        driver = state.driver_at(shift.id, shift.start)
        self.assertIsNotNone(record.history_after.drivers.get(driver))
        event = {"type": "no_show", "driver_id": driver, "at": hm(shift.start - 20)}
        options = ok(self.engine.handle("POST", f"/api/days/{self.ids[0]}/events/options", None, event))
        chosen = next(o["index"] for o in options["options"] if o["kind"] != "none")
        ok(self.engine.handle("POST", f"/api/days/{self.ids[0]}/events/apply", None,
                              {"event": event, "option": chosen}))
        after = record.history_after.drivers
        self.assertIsNone(after.get(driver, DriverState()).last_end,
                          "не вышедший водитель не должен считаться работавшим")
        self.assertTrue(any("применено событий" in note
                            for note in self.plan(1, {"history_from": self.ids[0]})["notes"]))

    def test_recalculating_the_same_day_is_not_counted_as_yesterday(self):
        """Повторный расчёт того же дня - не вчерашний день: нарушений отдыха быть не должно.

        Интерфейс диспетчера на каждое «Рассчитать» загружает день заново,
        и в памяти лежит несколько копий одной даты.
        """
        raw = generate("park7", "2026-10-05", seed=1, moment="morning")
        for _ in range(3):
            day_id = ok(self.engine.handle("POST", "/api/days", None, raw))["day_id"]
            answer = ok(self.engine.handle("POST", f"/api/days/{day_id}/plan", None, {}))
            self.assertEqual(answer["rest_violations"], [])
            self.assertEqual(answer["notes"], [])

    def test_other_city_is_not_counted_as_yesterday(self):
        """День другого города не должен считаться предыдущей сменой тех же водителей."""
        self.plan(0)
        other = ok(self.engine.handle("POST", "/api/days", None,
                                      {"preset": "case", "date": "2026-10-06", "seed": 1}))["day_id"]
        answer = ok(self.engine.handle("POST", f"/api/days/{other}/plan", None, {}))
        self.assertEqual(answer["rest_violations"], [])
        self.assertEqual(answer["notes"], [])


class TestExamplesFile(unittest.TestCase):
    """data/samples/api_examples.json - ровно то, что отдаёт живой код."""

    def test_examples_match(self):
        def strip(value):
            if isinstance(value, dict):
                return {k: strip(v) for k, v in value.items() if k != "seconds"}
            if isinstance(value, list):
                return [strip(v) for v in value]
            return value
        stored = json.loads(EXAMPLES_FILE.read_text(encoding="utf-8"))
        self.assertEqual(strip(stored), strip(make_examples()),
                         "образцы устарели: python -m naryad.web.api --write-examples")


class TestHttp(unittest.TestCase):
    """Отдельный сервер API: JSON, ошибки, заголовки CORS."""

    @classmethod
    def setUpClass(cls):
        Handler.engine = Engine()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, method, path, body=None, origin=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        if origin:
            headers["Origin"] = origin
        request = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, dict(response.headers), json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), json.loads(error.read())

    def test_body_that_is_too_large_is_refused_before_reading(self):
        """Реестр - первое большое тело в API: заявленный размер проверяем сразу."""
        import http.client
        from naryad.web.api import MAX_BODY
        link = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        link.putrequest("POST", "/api/days/import")
        link.putheader("Content-Type", "application/json")
        link.putheader("Content-Length", str(MAX_BODY + 1))
        link.endheaders()
        answer = link.getresponse()
        self.assertEqual(answer.status, 413)
        self.assertIn("больше", json.loads(answer.read())["error"])
        link.close()

    def test_broken_content_length(self):
        import http.client
        link = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
        link.putrequest("POST", "/api/days")
        link.putheader("Content-Length", "many")
        link.endheaders()
        self.assertEqual(link.getresponse().status, 400)
        link.close()

    def test_round_trip(self):
        status, headers, payload = self.call("POST", "/api/days", PARK7)
        self.assertEqual(status, 200)
        self.assertNotIn("Access-Control-Allow-Origin", headers, "без Origin заголовок не нужен")
        self.assertIn("charset=utf-8", headers["Content-Type"])
        day_id = payload["day_id"]
        self.assertEqual(self.call("GET", f"/api/days/{day_id}/state")[0], 409)
        status, headers, payload = self.call("POST", f"/api/days/{day_id}/plan", {})
        self.assertEqual(status, 200)
        self.assertEqual(payload["violations"], [])
        status, headers, payload = self.call("GET", f"/api/days/{day_id}/explain/interval/{ROUTE}?t=08:30")
        self.assertEqual(status, 200)
        self.assertIn("planned_min", payload["numbers"])
        self.assertEqual(self.call("GET", "/nothing")[0], 404)
        self.assertEqual(self.call("GET", "/api/labor")[2]["preset"], "current")

    def test_only_own_pages_may_read_the_day(self):
        """День - это реестр перевозчика: отдаём его только страницам с этого компьютера."""
        own = self.call("GET", "/api/labor", origin="http://localhost:8000")
        self.assertEqual(own[1]["Access-Control-Allow-Origin"], "http://localhost:8000")
        self.assertEqual(own[1]["Vary"], "Origin")
        for stranger in ("https://example.com", "http://localhost.evil.com", "null"):
            status, headers, _ = self.call("GET", "/api/labor", origin=stranger)
            self.assertEqual(status, 200, stranger)  # сам ответ не прячем
            self.assertNotIn("Access-Control-Allow-Origin", headers, stranger)
        request = urllib.request.Request(self.base + "/api/days", method="OPTIONS",
                                         headers={"Origin": "https://example.com"})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 403)

    def test_bad_json_and_options(self):
        request = urllib.request.Request(self.base + "/api/days", data="{не json".encode("utf-8"), method="POST")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 400)
        self.assertIn("JSON", json.loads(caught.exception.read())["error"])
        request = urllib.request.Request(self.base + "/api/days", method="OPTIONS",
                                         headers={"Origin": "http://127.0.0.1:8000"})
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.headers["Access-Control-Allow-Methods"], "GET, POST, OPTIONS")
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "http://127.0.0.1:8000")


if __name__ == "__main__":
    unittest.main()
