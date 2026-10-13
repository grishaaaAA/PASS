"""Сервер рабочего места диспетчера (naryad/web/dispatcher.py) поверх движка. Запуск: python -m unittest

Проверяется то, что интерфейс получает от сервера: вчерашний день для норм
отдыха, повторный расчёт той же даты, дни на диске, понятные ошибки.
"""

from __future__ import annotations

import http.client
import json
import shutil
import tempfile
import threading
import unittest
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

from naryad.web import dispatcher
from naryad.web.api import Engine
from naryad.web.marks import MarksStore

DAY = date(2026, 10, 12)


class Server:
    """Сервер рабочего места на свободном порту, со своей папкой дней и базой отметок."""

    def __init__(self, folder: Path | None):
        self.tmp = tempfile.mkdtemp(prefix="naryad-disp-")
        dispatcher.Handler.engine = Engine(store=folder)
        dispatcher.Handler.marks = MarksStore(Path(self.tmp) / "marks.sqlite")
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), dispatcher.Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def close(self):
        self.http.shutdown()
        self.http.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, method, path, body=None, raw=None, headers=None):
        link = http.client.HTTPConnection("127.0.0.1", self.http.server_address[1], timeout=120)
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        link.request(method, path, body=data, headers={"Content-Type": "application/json", **(headers or {})})
        answer = link.getresponse()
        payload = json.loads(answer.read() or b"null")
        link.close()
        return answer.status, payload

    def plan_day(self, day: date):
        status, registry = self.call("GET", f"/x/registry/P07?date={day.isoformat()}")
        assert status == 200, registry
        status, made = self.call("POST", "/api/days", registry)
        assert status == 200, made
        status, plan = self.call("POST", f"/api/days/{made['day_id']}/plan", {})
        assert status == 200, plan
        return made["day_id"], plan


class TestYesterday(unittest.TestCase):

    def setUp(self):
        self.server = Server(None)

    def tearDown(self):
        self.server.close()

    def test_second_day_has_no_false_rest_violations(self):
        """Интерфейс шлёт план без history_from; сервер сам цепляет вчерашний день."""
        first, _ = self.server.plan_day(DAY)
        second, plan = self.server.plan_day(DAY + timedelta(days=1))
        self.assertEqual(plan["rest_violations"], [])
        engine = dispatcher.Handler.engine
        self.assertEqual(engine.days[second].source, first, "память водителей взята со вчерашнего дня")

    def test_without_the_chain_there_would_be_violations(self):
        """Сторож: без цепочки два независимых дня дают нарушения - значит, тест выше не пустой."""
        engine = Engine()
        for offset in (0, 1):
            raw = dispatcher.registry("P07", (DAY + timedelta(days=offset)).isoformat())
            day_id = engine.handle("POST", "/api/days", None, raw)[1]["day_id"]
            plan = engine.handle("POST", f"/api/days/{day_id}/plan", None, {})[1]
        self.assertGreater(len(plan["rest_violations"]), 0)

    def test_explicit_history_from_is_not_overridden(self):
        first, _ = self.server.plan_day(DAY)
        status, registry = self.server.call("GET", f"/x/registry/P07?date={(DAY + timedelta(days=2)).isoformat()}")
        status, made = self.server.call("POST", "/api/days", registry)
        status, plan = self.server.call("POST", f"/api/days/{made['day_id']}/plan", {"history_from": first})
        self.assertEqual(status, 200, plan)
        self.assertEqual(dispatcher.Handler.engine.days[made["day_id"]].source, first)

    def test_recalculation_replaces_the_day(self):
        self.server.plan_day(DAY)
        second, _ = self.server.plan_day(DAY)
        self.assertEqual(list(dispatcher.Handler.engine.days), [second])

    def test_day_log_counts_what_recalculation_erases(self):
        """Перед повторным расчётом интерфейс спрашивает, сколько событий и правок пропадёт."""
        day_id, plan = self.server.plan_day(DAY)
        ask = f"/x/day-log?date={DAY.isoformat()}&parks=P07"
        self.assertEqual(self.server.call("GET", ask), (200, {"events": 0, "edits": 0}))
        duty = plan["plan"]["vehicle_assignments"][0]["duty_id"]
        status, state = self.server.call("POST", f"/api/days/{day_id}/edits", {"type": "clear_vehicle", "duty_id": duty})
        self.assertEqual(status, 200, state)
        self.assertEqual(self.server.call("GET", ask), (200, {"events": 0, "edits": 1}))
        other = f"/x/day-log?date={(DAY + timedelta(days=1)).isoformat()}&parks=P07"
        self.assertEqual(self.server.call("GET", other), (200, {"events": 0, "edits": 0}))


class TestStore(unittest.TestCase):

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="naryad-days-"))
        self.server = Server(self.folder)

    def tearDown(self):
        self.server.close()
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_day_survives_restart_and_recalculation_removes_old_files(self):
        old, _ = self.server.plan_day(DAY)
        new, _ = self.server.plan_day(DAY)
        names = sorted(p.name for p in self.folder.iterdir())
        self.assertEqual(names, [f"{new}.day.json", f"{new}.live.json"], "файлы заменённого дня убраны")
        again = Engine(store=self.folder)
        self.assertEqual(list(again.days), [new])
        self.assertNotEqual(old, new)

    def test_main_refuses_a_file_instead_of_a_folder(self):
        target = self.folder / "файл"
        target.write_text("x", encoding="utf-8")
        self.assertEqual(dispatcher.main(["--store", str(target), "--no-open", "--port", "0"]), 1)


class TestErrors(unittest.TestCase):

    def setUp(self):
        self.server = Server(None)

    def tearDown(self):
        self.server.close()

    def test_bad_requests_get_an_answer(self):
        status, payload = self.server.call("POST", "/x/marks", raw=b"[1, 2]")
        self.assertEqual(status, 400)
        self.assertIn("объект JSON", payload["error"])
        status, payload = self.server.call("PUT", "/x/geo/routes/missing", raw=b"[]")
        self.assertEqual(status, 400)
        link = http.client.HTTPConnection("127.0.0.1", self.server.http.server_address[1], timeout=10)
        link.putrequest("POST", "/x/marks")
        link.putheader("Content-Length", "many")
        link.endheaders()
        self.assertEqual(link.getresponse().status, 400)
        link.close()


if __name__ == "__main__":
    unittest.main()
