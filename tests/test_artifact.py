"""
Генератор страницы по ссылке (static/generator.js) выдаёт данные того же формата:
их принимает та же проверка на Python, а цифры сходятся с вводными.
Нужен Node.js; без него проверки пропускаются.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from naryad.data.check import check
from naryad.data.city import default_setup, resolve
from naryad.web.build_artifact import build, data

GENERATOR = Path(__file__).resolve().parents[1] / "naryad/web/static/generator.js"
NODE = shutil.which("node")

RUN = """
const D = %s;
const G = require(%s)(D);
const out = {};
for (const [name, req] of Object.entries(%s)) {
  try {
    const parsed = G.parseRequest(req);
    out[name] = {data: G.generate(parsed.setup, parsed.day, parsed.seed), summary: G.summarize(parsed),
                 csv: Object.keys(G.csvFiles(G.generate(parsed.setup, parsed.day, parsed.seed)))};
  } catch (e) { out[name] = {error: e.message}; }
}
process.stdout.write(JSON.stringify(out));
"""


def park_requests() -> dict:
    parks = [dict(p, enabled=True, sheet=False, readiness=round(p["readiness"] * 100, 1))
             for p in default_setup(resolve("case"))]
    def req(day, seed=1, **changes):
        items = []
        for p in parks:
            item = dict(p, **changes.get(p["id"], {}))
            items.append(item)
        return {"date": day, "seed": seed, "parks": items}
    return {
        "weekday": req("2026-10-05"),
        "weekend": req("2026-10-04", seed=7),
        "mixed": req("2026-10-06", seed=3, P01={"enabled": False}, P07={"sheet": True},
                     P02={"readiness": 70, "routes": 9}),
        "bad": req("2026-10-05", P03={"routes": 900}),
        "too_many_routes": req("2026-10-05", P03={"routes": 280}),
        "none": req("2026-10-05", **{p["id"]: {"enabled": False} for p in parks}),
    }


@unittest.skipIf(NODE is None, "нет Node.js")
class TestArtifactGenerator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = RUN % (json.dumps(data(), ensure_ascii=False), json.dumps(str(GENERATOR)),
                      json.dumps(park_requests(), ensure_ascii=False))
        done = subprocess.run([NODE, "-e", code], capture_output=True, text=True, check=True)
        cls.out = json.loads(done.stdout)

    def test_python_check_accepts_data(self):
        for name in ("weekday", "weekend", "mixed"):
            report = check(self.out[name]["data"])
            self.assertEqual(report["errors"], [], name)

    def test_numbers_match_inputs(self):
        weekday = self.out["weekday"]["data"]
        self.assertEqual(len(weekday["parks"]), 7)
        self.assertEqual(len(weekday["routes"]), 118)
        self.assertEqual(len(weekday["duties"]), 1890)
        for park in weekday["parks"]:
            own = [v for v in weekday["vehicles"] if v["park_id"] == park["id"]]
            ok = sum(v["condition"] == "ok" for v in own)
            self.assertEqual(len(own) - ok, round(len(own) * (1 - park["tech_readiness"])))
        self.assertEqual(self.out["weekday"]["summary"]["weekday"], "понедельник")
        self.assertEqual(self.out["weekend"]["data"]["meta"]["day_type"], "weekend")

    def test_mixed_selection(self):
        mixed = self.out["mixed"]
        ids = [p["id"] for p in mixed["data"]["parks"]]
        self.assertNotIn("P01", ids)
        p7 = [p for p in mixed["summary"]["parks"] if p["id"] == "P07"][0]
        self.assertEqual(p7["line"] + p7["reserve"], 350)
        p2 = [p for p in mixed["summary"]["parks"] if p["id"] == "P02"][0]
        self.assertEqual(p2["routes"], 9)
        self.assertTrue(p2["issues"])
        self.assertTrue(all("P02" not in text for text in p2["issues"]))

    def test_errors_are_readable(self):
        self.assertEqual(self.out["bad"]["error"], "Автобусный парк №3, маршрутов: от 1 до 300")
        self.assertIn("уменьшите число маршрутов", self.out["too_many_routes"]["error"])
        self.assertEqual(self.out["none"]["error"], "Отметьте хотя бы один парк")

    def test_csv_files(self):
        self.assertEqual(sorted(self.out["weekday"]["csv"]),
                         sorted(["parks.csv", "routes.csv", "duties.csv", "shifts.csv", "vehicles.csv",
                                 "drivers.csv", "meta.json"]))

    def test_build(self):
        html = build(Path(__file__).resolve().parents[1] / "dist" / "test_build.html").read_text()
        self.assertIn("<title>Генератор AUTODISP</title>", html)
        self.assertNotIn('src="ds/', html)
        # генератор страницы подключён раньше самой страницы, поэтому сервер не нужен
        self.assertLess(html.index("window.AUTODISP_API = {"), html.index("var API = window.AUTODISP_API ||"))


if __name__ == "__main__":
    unittest.main()
