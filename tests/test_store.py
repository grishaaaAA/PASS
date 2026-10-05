"""Дни на диске: переживают перезапуск, испорченный файл не мешает остальным. python -m unittest"""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from naryad.web.api import Engine

PARK7 = {"preset": "park7", "date": "2026-10-05", "seed": 1, "moment": "morning"}
DUTY = "P07-R01-WD01"


def ok(result):
    status, payload = result
    assert status == 200, (status, payload)
    return payload


class TestStore(unittest.TestCase):

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp(prefix="naryad-days-"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.folder, ignore_errors=True)

    def engine(self) -> Engine:
        return Engine(store=self.folder)

    def full_day(self, engine: Engine) -> dict:
        """День с планом, событием и вынужденной правкой диспетчера."""
        ok(engine.handle("POST", "/api/days", None, PARK7))
        ok(engine.handle("POST", "/api/days/day-1/plan", None, {}))
        state = ok(engine.handle("GET", "/api/days/day-1/state"))
        bus = state["duties"][0]["vehicles"][0]["vehicle_id"]
        event = {"type": "breakdown", "vehicle_id": bus, "at": "08:40"}
        ok(engine.handle("POST", "/api/days/day-1/events/apply", None, {"event": event, "option": 0}))
        busy = next(d["vehicles"][0]["vehicle_id"] for d in state["duties"] if d["duty_id"] != DUTY)
        ok(engine.handle("POST", "/api/days/day-1/edits", None,
                         {"type": "set_vehicle", "duty_id": DUTY, "vehicle_id": busy,
                          "force": True, "reason": "решение диспетчера"}))
        return ok(engine.handle("GET", "/api/days/day-1/state"))

    def test_day_survives_restart_with_everything_on_it(self):
        first = self.engine()
        before = self.full_day(first)
        again = self.engine()
        self.assertEqual(ok(again.handle("GET", "/api/days/day-1/state")), before)
        self.assertEqual(ok(again.handle("GET", "/api/days"))["days"],
                         ok(first.handle("GET", "/api/days"))["days"])

    def test_forced_edit_keeps_its_reason_and_violations(self):
        """Решение диспетчера, принятое вопреки норме, нельзя потерять при перезапуске."""
        self.full_day(self.engine())
        entry = ok(self.engine().handle("GET", "/api/days/day-1/log"))["log"][-1]
        self.assertTrue(entry["forced"])
        self.assertEqual(entry["reason"], "решение диспетчера")
        self.assertEqual([v["code"] for v in entry["violations"]], ["vehicle_twice"])

    def test_driver_memory_and_explanations_survive(self):
        first = self.engine()
        self.full_day(first)
        again = self.engine()
        self.assertEqual(first.days["day-1"].history_after.drivers,
                         again.days["day-1"].history_after.drivers)
        self.assertEqual(first.days["day-1"].history_before.drivers,
                         again.days["day-1"].history_before.drivers)
        answer = ok(again.handle("GET", f"/api/days/day-1/explain/vehicle/{DUTY}"))
        self.assertIn("answer", answer)

    def test_day_without_a_plan_survives_as_a_day_without_a_plan(self):
        ok(self.engine().handle("POST", "/api/days", None, PARK7))
        again = self.engine()
        self.assertEqual(ok(again.handle("GET", "/api/days"))["days"][0]["planned"], False)
        status, _ = again.handle("GET", "/api/days/day-1/state")
        self.assertEqual(status, 409)
        self.assertEqual(ok(again.handle("POST", "/api/days/day-1/plan", None, {}))["violations"], [])

    def test_numbers_continue_after_restart(self):
        engine = self.engine()
        ok(engine.handle("POST", "/api/days", None, PARK7))
        ok(engine.handle("POST", "/api/days", None, dict(PARK7, date="2026-10-06")))
        again = self.engine()
        self.assertEqual(ok(again.handle("POST", "/api/days", None, PARK7))["day_id"], "day-3")
        self.assertEqual(len(ok(again.handle("GET", "/api/days"))["days"]), 3)

    def test_engine_without_a_folder_writes_nothing(self):
        plain = Engine()
        ok(plain.handle("POST", "/api/days", None, PARK7))
        ok(plain.handle("POST", "/api/days/day-1/plan", None, {}))
        self.assertEqual(list(self.folder.iterdir()), [])
        self.assertEqual(ok(Engine().handle("GET", "/api/days"))["days"], [])

    def test_no_half_written_files_are_left(self):
        self.full_day(self.engine())
        self.assertEqual(sorted(p.name for p in self.folder.iterdir()),
                         ["day-1.day.json", "day-1.live.json"])

    def test_broken_live_file_leaves_the_day_without_a_plan(self):
        self.full_day(self.engine())
        (self.folder / "day-1.live.json").write_text("{ это не json", encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            again = self.engine()
        self.assertIn("не читается", err.getvalue())
        self.assertIn("открыт без плана и журнала", err.getvalue(), "день не пропущен, а открыт без плана")
        self.assertNotIn("пропущен", err.getvalue())
        self.assertEqual(ok(again.handle("GET", "/api/days"))["days"][0]["planned"], False)
        self.assertEqual(ok(again.handle("POST", "/api/days/day-1/plan", None, {}))["violations"], [],
                         "день цел, план строится заново")

    def test_malformed_insides_do_not_crash_the_start(self):
        """Файл нужного формата с испорченной начинкой: день без плана или пропущен, запуск цел."""
        self.full_day(self.engine())
        live = self.folder / "day-1.live.json"
        good = json.loads(live.read_text(encoding="utf-8"))
        spoiled = [dict(good, state={"vehicles": {DUTY: [["06:00", "13:00", "B1"]]}}),
                   dict(good, plan="строка"), dict(good, log="строка"),
                   dict(good, history_after={"P07-D0001": [1, 2, 3]}),
                   dict(good, state=None)]
        for bad in spoiled:
            live.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                again = self.engine()
            self.assertIn("открыт без плана и журнала", err.getvalue())
            listed = ok(again.handle("GET", "/api/days"))["days"]
            self.assertEqual([(d["day_id"], d["planned"], d["edits"]) for d in listed], [("day-1", False, 0)])
        live.write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
        day = self.folder / "day-1.day.json"
        stored = json.loads(day.read_text(encoding="utf-8"))
        for raw in (None, [], "день"):
            day.write_text(json.dumps(dict(stored, raw=raw), ensure_ascii=False), encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                again = self.engine()
            self.assertIn("пропущен", err.getvalue())
            self.assertEqual(ok(again.handle("GET", "/api/days"))["days"], [])

    def test_skipped_day_keeps_its_number_and_its_files(self):
        """Номер пропущенного дня новому не выдаётся, его файлы не перезаписываются."""
        engine = self.engine()
        ok(engine.handle("POST", "/api/days", None, PARK7))
        ok(engine.handle("POST", "/api/days", None, dict(PARK7, date="2026-10-06")))
        ok(engine.handle("POST", "/api/days/day-2/plan", None, {}))
        day, live = self.folder / "day-2.day.json", self.folder / "day-2.live.json"
        stored = json.loads(day.read_text(encoding="utf-8"))
        day.write_text(json.dumps({**stored, "format": 99}, ensure_ascii=False), encoding="utf-8")
        before = (day.read_bytes(), live.read_bytes())
        with contextlib.redirect_stderr(io.StringIO()):
            again = self.engine()
        self.assertEqual(ok(again.handle("POST", "/api/days", None, dict(PARK7, date="2026-10-07")))["day_id"],
                         "day-3")
        ok(again.handle("POST", "/api/days/day-3/plan", None, {}))
        self.assertEqual((day.read_bytes(), live.read_bytes()), before, "файлы пропущенного дня тронуты")

    def test_failed_write_changes_nothing_in_memory(self):
        """Не записалось на диск - значит не применено: ответ «не выполнен» правдив."""
        engine = self.engine()
        ok(engine.handle("POST", "/api/days", None, PARK7))
        ok(engine.handle("POST", "/api/days/day-1/plan", None, {}))
        before = ok(engine.handle("GET", "/api/days/day-1/state"))
        bus = before["duties"][0]["vehicles"][0]["vehicle_id"]

        def full_disk(*args, **kwargs):
            raise OSError(28, "No space left on device")

        engine.store.save_live = full_disk
        requests = (("POST", "/api/days/day-1/edits", {"type": "clear_vehicle", "duty_id": DUTY}),
                    ("POST", "/api/days/day-1/events/apply",
                     {"event": {"type": "breakdown", "vehicle_id": bus, "at": "08:40"}, "option": 0}),
                    ("POST", "/api/days/day-1/plan", {}))
        for method, path, body in requests:
            status, payload = engine.handle(method, path, None, body)
            self.assertEqual(status, 500, path)
            self.assertIn("не применено", payload["error"], path)
        self.assertEqual(ok(engine.handle("GET", "/api/days/day-1/state")), before)
        self.assertEqual(ok(engine.handle("GET", "/api/days"))["days"][0]["edits"], 0)
        engine.store.save_day = full_disk
        status, payload = engine.handle("POST", "/api/days", None, PARK7)
        self.assertEqual(status, 500)
        self.assertIn("не создан", payload["error"])
        self.assertEqual(len(ok(engine.handle("GET", "/api/days"))["days"]), 1)
        self.assertEqual(ok(self.engine().handle("GET", "/api/days/day-1/state")), before,
                         "после перезапуска день тот же, что был на экране")

    def test_second_server_on_the_same_folder_cannot_overwrite_a_day(self):
        first, second = self.engine(), self.engine()
        ok(first.handle("POST", "/api/days", None, PARK7))
        status, payload = second.handle("POST", "/api/days", None, dict(PARK7, date="2026-10-06"))
        self.assertEqual(status, 500)
        self.assertIn("уже есть", payload["error"])
        self.assertEqual(ok(second.handle("GET", "/api/days"))["days"], [])
        stored = json.loads((self.folder / "day-1.day.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["raw"]["meta"]["date"], "2026-10-05", "день первого сервера перезаписан")

    def test_store_path_that_is_a_file_is_a_clear_error(self):
        from naryad.web import api
        from naryad.web.store import Store
        occupied = self.folder / "занято.txt"
        occupied.write_text("файл", encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            Store(occupied)
        self.assertIn("это файл, а не папка", str(caught.exception))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = api.main(["--port", "0", "--store", str(occupied)])
        self.assertEqual(code, 1)
        self.assertIn("Папка дней не создана", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_repair_windows_survive_restart(self):
        """Окно ремонта после схода на время - часть состояния, после перезапуска оно на месте."""
        engine = self.engine()
        ok(engine.handle("POST", "/api/days", None, PARK7))
        ok(engine.handle("POST", "/api/days/day-1/plan", None, {}))
        bus = engine.days["day-1"].state.vehicle_at(DUTY, 8 * 60 + 40)
        event = {"type": "breakdown", "vehicle_id": bus, "at": "08:40", "duration_min": 120}
        before = ok(engine.handle("POST", "/api/days/day-1/events/apply", None, {"event": event, "option": 0}))
        self.assertEqual(before["repairs"], [{"vehicle_id": bus, "from": "08:40", "to": "10:40"}])
        again = self.engine()
        self.assertEqual(ok(again.handle("GET", "/api/days/day-1/state"))["repairs"], before["repairs"])
        status, payload = again.handle("POST", "/api/days/day-1/edits", None,
                                       {"type": "set_vehicle", "duty_id": "P07-R01-WD02", "vehicle_id": bus,
                                        "from": "09:00", "to": "10:00"})
        self.assertEqual((status, [v["code"] for v in payload["violations"]]), (409, ["in_repair"]))

    def test_broken_day_file_is_skipped_and_the_rest_open(self):
        engine = self.engine()
        ok(engine.handle("POST", "/api/days", None, PARK7))
        ok(engine.handle("POST", "/api/days", None, dict(PARK7, date="2026-10-06")))
        (self.folder / "day-1.day.json").write_text("не json вовсе", encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            again = self.engine()
        self.assertIn("day-1.day.json", err.getvalue())
        self.assertEqual([d["day_id"] for d in ok(again.handle("GET", "/api/days"))["days"]], ["day-2"])
        self.assertEqual(ok(again.handle("POST", "/api/days", None, PARK7))["day_id"], "day-3",
                         "номер не должен повторить пропущенный день")

    def test_other_format_version_is_skipped(self):
        ok(self.engine().handle("POST", "/api/days", None, PARK7))
        path = self.folder / "day-1.day.json"
        stored = json.loads(path.read_text(encoding="utf-8"))
        path.write_text(json.dumps({**stored, "format": 99}, ensure_ascii=False), encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            again = self.engine()
        self.assertIn("формат 99", err.getvalue())
        self.assertEqual(ok(again.handle("GET", "/api/days"))["days"], [])

    def test_day_that_no_longer_passes_the_check_is_skipped(self):
        """Проверка данных со временем строже: старый день не должен ронять запуск."""
        ok(self.engine().handle("POST", "/api/days", None, PARK7))
        path = self.folder / "day-1.day.json"
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["raw"]["meta"].pop("day_type")
        path.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            again = self.engine()
        self.assertIn("больше не читаются", err.getvalue())
        self.assertEqual(ok(again.handle("GET", "/api/days"))["days"], [])

    def test_stray_files_are_ignored(self):
        ok(self.engine().handle("POST", "/api/days", None, PARK7))
        (self.folder / "заметка.txt").write_text("привет", encoding="utf-8")
        (self.folder / "вчера.day.json").write_text("{}", encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            again = self.engine()
        self.assertIn("не похоже на номер дня", err.getvalue())
        self.assertEqual([d["day_id"] for d in ok(again.handle("GET", "/api/days"))["days"]], ["day-1"])

    @staticmethod
    def park7_csv() -> dict:
        from pathlib import Path as _Path
        folder = _Path(__file__).resolve().parent.parent / "data" / "samples" / "park7_weekday_csv"
        return {path.stem: path.read_text(encoding="utf-8-sig") for path in sorted(folder.glob("*.csv"))}

    def test_registry_import_is_stored_too(self):
        meta = {"date": "2026-10-05", "day_type": "weekday", "moment": "morning"}
        ok(self.engine().handle("POST", "/api/days/import", None,
                                {"files": self.park7_csv(), "meta": meta}))
        again = self.engine()
        self.assertEqual(ok(again.handle("GET", "/api/days"))["days"][0]["day_id"], "day-1")
        self.assertEqual(len(ok(again.handle("POST", "/api/days/day-1/plan", None, {}))
                             ["plan"]["vehicle_assignments"]), 350)

    def test_dry_run_and_refused_import_store_nothing(self):
        meta = {"date": "2026-10-05", "day_type": "weekday", "moment": "morning"}
        engine = self.engine()
        ok(engine.handle("POST", "/api/days/import", None,
                         {"files": self.park7_csv(), "meta": meta, "dry_run": True}))
        self.assertEqual(list(self.folder.iterdir()), [], "проверочное чтение ничего не создаёт")
        broken = dict(self.park7_csv())
        broken["vehicles"] = broken["vehicles"].replace("ok", "как-то так")
        status, _ = engine.handle("POST", "/api/days/import", None, {"files": broken, "meta": meta})
        self.assertEqual(status, 400)
        self.assertEqual(list(self.folder.iterdir()), [], "отклонённый реестр ничего не создаёт")


if __name__ == "__main__":
    unittest.main()
