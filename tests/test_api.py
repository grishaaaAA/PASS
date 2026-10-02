"""API движка (Б5): ручки, ошибки, образцы ответов, живой сервер. Запуск: python -m unittest"""

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from naryad.core.invariants import check_rest
from naryad.core.model import Plan
from naryad.web.api import EXAMPLES_FILE, Engine, Handler, make_examples

PARK7 = {"preset": "park7", "date": "2026-10-05", "seed": 1, "moment": "morning"}
DUTY, SHIFT, ROUTE = "P07-R01-WD01", "P07-R01-WD01-S1", "P07-R01"


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
        self.assertIn("326 из 326", self.planned["summary"]["answer"])
        self.assertLess(self.planned["seconds"], 10)

    def test_state_before_events_is_the_plan(self):
        state = ok(self.engine.handle("GET", "/api/days/day-1/state"))
        self.assertEqual(state["meta"], {"day_id": "day-1", "date": "2026-10-05", "events": 0, "labor": "current"})
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
        self.assertEqual(set(interval), {"planned_min", "actual_min", "planned_buses", "running_buses"})

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
        self.assertTrue(1 <= len(options) <= 3)
        self.assertEqual([o["index"] for o in options], list(range(len(options))))
        self.assertEqual(options[0]["cost"], min(o["cost"] for o in options))
        for option in options:
            self.assertIn(option["kind"], ("none", "idle", "reserve", "donor", "free_driver"))
            self.assertEqual(set(option["explanation"]), {"question", "answer", "reasons", "numbers"})
        self.assertEqual(self.engine.days["day-1"].log, [])  # ничего не изменилось

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

    def call(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"} if data else {})
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, dict(response.headers), json.loads(response.read() or b"null")
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), json.loads(error.read())

    def test_round_trip(self):
        status, headers, payload = self.call("POST", "/api/days", PARK7)
        self.assertEqual(status, 200)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")
        self.assertIn("charset=utf-8", headers["Content-Type"])
        day_id = payload["day_id"]
        self.assertEqual(self.call("GET", f"/api/days/{day_id}/state")[0], 409)
        status, headers, payload = self.call("POST", f"/api/days/{day_id}/plan", {})
        self.assertEqual(status, 200)
        self.assertEqual(payload["violations"], [])
        status, headers, payload = self.call("GET", f"/api/days/{day_id}/explain/interval/{ROUTE}?t=08:30")
        self.assertEqual(status, 200)
        self.assertIn("planned_min", payload)
        self.assertEqual(self.call("GET", "/nothing")[0], 404)
        self.assertEqual(self.call("GET", "/api/labor")[2]["preset"], "current")

    def test_bad_json_and_options(self):
        request = urllib.request.Request(self.base + "/api/days", data="{не json".encode("utf-8"), method="POST")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request)
        self.assertEqual(caught.exception.code, 400)
        self.assertIn("JSON", json.loads(caught.exception.read())["error"])
        request = urllib.request.Request(self.base + "/api/days", method="OPTIONS")
        with urllib.request.urlopen(request) as response:
            self.assertEqual(response.status, 204)
            self.assertEqual(response.headers["Access-Control-Allow-Methods"], "GET, POST, OPTIONS")


if __name__ == "__main__":
    unittest.main()
