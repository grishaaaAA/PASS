"""Проверки сервера генератора: вводные парков, сводка дня, выгрузка, раздача файлов."""

import io
import json
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

from naryad.web.server import Handler, defaults, export, humanize, parse_request, summarize

DAY = "2026-10-05"


def request(**changes):
    parks = [dict(p, enabled=True, sheet=False) for p in defaults()["parks"]]
    for park in parks:
        park.update(changes.get(park["id"], {}))
    return {"date": DAY, "seed": 1, "parks": parks}


class TestDefaults(unittest.TestCase):
    def test_seven_parks_as_in_case(self):
        parks = defaults()["parks"]
        self.assertEqual(len(parks), 7)
        self.assertEqual(sum(sum(p["classes"].values()) for p in parks), 1890)
        self.assertEqual(sum(p["routes"] for p in parks), 118)
        sheet = [p for p in parks if p["sheet"]]
        self.assertEqual([p["id"] for p in sheet], ["P07"])
        self.assertEqual(sum(sheet[0]["sheet"]["classes"].values()), 350)


class TestRequest(unittest.TestCase):
    def test_only_enabled_parks(self):
        parsed = parse_request(request(P01={"enabled": False}, P07={"sheet": True}))
        self.assertEqual(len(parsed["setup"]), 6)
        self.assertEqual([p for p in parsed["setup"] if p["id"] == "P07"], [{"id": "P07", "sheet": True}])
        self.assertAlmostEqual(parsed["setup"][0]["readiness"], 0.893)

    def test_errors_are_readable(self):
        none = request(**{p["id"]: {"enabled": False} for p in defaults()["parks"]})
        cases = [({"date": ""}, "Дата"), (none, "Отметьте"),
                 (request(P01={"routes": "abc"}), "нужно число"),
                 (request(P01={"readiness": 30}), "исправных %"),
                 (request(P02={"sheet": True}), "справки")]
        for raw, text in cases:
            raw = dict(request(), **raw) if "parks" not in raw else raw
            with self.assertRaisesRegex(ValueError, text):
                parse_request(raw)

    def test_too_many_routes(self):
        with self.assertRaisesRegex(ValueError, "уменьшите число маршрутов"):
            summarize(parse_request(request(P01={"classes": {"medium": 0, "big": 5, "extra_big": 0},
                                                 "routes": 9})))


class TestSummary(unittest.TestCase):
    def test_case_day(self):
        res = summarize(parse_request(request()))
        self.assertEqual(len(res["parks"]), 7)
        self.assertEqual(res["totals"]["line"] + res["totals"]["reserve"], 1890)
        self.assertEqual(res["weekday"], "понедельник")

    def test_changed_park(self):
        res = summarize(parse_request(request(P02={"readiness": 70}, P07={"sheet": True})))
        by_id = {p["id"]: p for p in res["parks"]}
        self.assertEqual(by_id["P07"]["line"] + by_id["P07"]["reserve"], 350)
        self.assertTrue(by_id["P02"]["issues"])
        self.assertTrue(all("P02" not in text for text in by_id["P02"]["issues"]))

    def test_humanize(self):
        self.assertEqual(humanize("парк P06: класс medium - не хватает 2"),
                         "Автобусный парк №6: класс «средний» - не хватает 2")


class TestExport(unittest.TestCase):
    def test_json_matches_selection(self):
        parsed = parse_request(request(P01={"enabled": False}))
        name, _, body = export(parsed, "json")
        data = json.loads(body)
        self.assertTrue(name.endswith(".json"))
        self.assertEqual([p["id"] for p in data["parks"]], ["P02", "P03", "P05", "P06", "P07", "P08"])

    def test_csv(self):
        _, _, body = export(parse_request(request()), "csv")
        self.assertIn("vehicles.csv", zipfile.ZipFile(io.BytesIO(body)).namelist())


class TestHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def post(self, path, payload: bytes):
        return urllib.request.urlopen(urllib.request.Request(self.base + path, data=payload, method="POST"))

    def test_page_and_assets(self):
        self.assertIn("AUTODISP", urllib.request.urlopen(self.base + "/").read().decode())
        self.assertEqual(urllib.request.urlopen(self.base + "/ds/fonts/Ubuntu-Regular.ttf").status, 200)

    def test_no_files_outside_static(self):
        for path in ("/../server.py", "/%2e%2e/server.py", "/ds/../../server.py"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(self.base + path)
            self.assertEqual(caught.exception.code, 404)

    def test_bad_path_answers_instead_of_dropping(self):
        """Нулевой байт в пути роняет pathlib: ответ должен быть обычным 404.

        Браузер так не умеет, а сырой запрос умеет, и раньше сервер отвечал
        пустотой с трассировкой в журнале.
        """
        import socket
        for raw in (b"GET /\x00 HTTP/1.1\r\nHost: x\r\n\r\n",
                    b"GET /app.js\x00.css HTTP/1.1\r\nHost: x\r\n\r\n",
                    b"GET /ds/\x00/style.css HTTP/1.1\r\nHost: x\r\n\r\n"):
            link = socket.create_connection(("127.0.0.1", self.server.server_address[1]), timeout=5)
            link.sendall(raw)
            answer = link.recv(200)
            link.close()
            self.assertTrue(answer, f"пустой ответ на {raw!r}")
            self.assertIn(b"404", answer.split(b"\r\n")[0], answer[:80])

    def test_generate_export_and_errors(self):
        payload = json.dumps(request(P07={"sheet": True})).encode()
        self.assertEqual(len(json.load(self.post("/api/generate", payload))["parks"]), 7)
        reply = self.post("/api/export", json.dumps(dict(request(), format="csv")).encode())
        self.assertIn("attachment", reply.headers["Content-Disposition"])
        for bad in (b"{oops", b"[1]", json.dumps(dict(request(), format="pdf")).encode()):
            path = "/api/export" if b"pdf" in bad else "/api/generate"
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.post(path, bad)
            self.assertEqual(caught.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
