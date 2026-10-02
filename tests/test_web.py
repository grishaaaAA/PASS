"""Проверки сервера генератора: вводные, сводка, выгрузка, раздача файлов."""

import io
import json
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

from naryad.web.server import Handler, export, humanize, parse_inputs, summarize


class TestInputs(unittest.TestCase):
    def test_case_inputs(self):
        params = parse_inputs({"preset": "case", "days": "3", "seed": "2", "park_count": "3",
                               "mix_medium": "0", "mix_big": "600", "mix_extra_big": "400"})
        self.assertEqual(params["days"], 3)
        self.assertEqual(params["park_count"], 3)
        self.assertEqual(params["class_mix"], {"medium": 0, "big": 600, "extra_big": 400})

    def test_park7_ignores_city(self):
        params = parse_inputs({"preset": "park7", "park_count": "3"})
        self.assertNotIn("park_count", params)

    def test_errors_are_readable(self):
        for raw, text in [({"days": "99"}, "Дней"), ({"park_count": "abc"}, "нужно число"),
                          ({"start": "05.10.2026"}, "ГГГГ-ММ-ДД"), ({"preset": "x"}, "Настройка"),
                          ({"mix_medium": "0", "mix_big": "0", "mix_extra_big": "0"}, "Классы")]:
            with self.assertRaisesRegex(ValueError, text):
                parse_inputs(raw)


class TestSummary(unittest.TestCase):
    def test_summary(self):
        res = summarize(parse_inputs({"preset": "case", "days": "2", "seed": "1"}))
        self.assertEqual(len(res["days"]), 2)
        self.assertEqual(len(res["parks"]), 7)
        self.assertEqual(res["routes"], 118)
        self.assertEqual(sum(p["line"] + p["reserve"] for p in res["parks"]), 1890)

    def test_humanize(self):
        names = {"P06": "Автобусный парк №6"}
        self.assertEqual(humanize("парк P06: класс medium - не хватает 2", names),
                         "Автобусный парк №6: класс «средний» - не хватает 2")


class TestExport(unittest.TestCase):
    def test_series_zip(self):
        name, kind, body = export(parse_inputs({"preset": "park7", "days": "3"}), "series_zip")
        self.assertTrue(name.endswith(".zip"))
        files = zipfile.ZipFile(io.BytesIO(body)).namelist()
        self.assertEqual(len([f for f in files if f.startswith("days/")]), 3)
        self.assertIn("series.csv", files)

    def test_day_json_and_csv(self):
        params = parse_inputs({"preset": "park7", "days": "5"})
        _, _, body = export(params, "day_json")
        self.assertEqual(json.loads(body)["meta"]["date"], params["start"])
        _, _, body = export(params, "day_csv")
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

    def get(self, path):
        return urllib.request.urlopen(self.base + path)

    def test_page_and_assets(self):
        self.assertIn("AUTODISP", self.get("/").read().decode())
        self.assertEqual(self.get("/ds/fonts/Ubuntu-Regular.ttf").status, 200)

    def test_no_files_outside_static(self):
        for path in ("/../server.py", "/%2e%2e/server.py", "/ds/../../server.py"):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                self.get(path)
            self.assertEqual(caught.exception.code, 404)

    def test_generate_and_error(self):
        request = urllib.request.Request(self.base + "/api/generate", method="POST",
                                         data=json.dumps({"preset": "park7", "days": "1"}).encode())
        self.assertEqual(len(json.load(urllib.request.urlopen(request))["parks"]), 1)
        bad = urllib.request.Request(self.base + "/api/generate", method="POST", data=b"{oops")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(bad)
        self.assertEqual(caught.exception.code, 400)


if __name__ == "__main__":
    unittest.main()
