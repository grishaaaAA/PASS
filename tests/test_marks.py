"""Отметки диспетчера: сохранение по парку и дню, журнал, проверка запроса."""

import tempfile
import unittest
from pathlib import Path

from naryad.web.marks import MarksError, MarksStore


class MarksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = MarksStore(Path(self.tmp.name) / "marks.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def test_saved_by_park_and_day(self):
        self.store.set("2026-10-07", [{"kind": "veh", "id": "P07-V0001", "value": False},
                                      {"kind": "drv", "id": "P07-D0002", "value": True},
                                      {"kind": "route", "id": "P03-R01", "value": False}])
        got = self.store.get(["P07"], "2026-10-07")
        self.assertEqual(got["veh"], {"P07-V0001": False})
        self.assertEqual(got["drv"], {"P07-D0002": True})
        self.assertEqual(got["route"], {})                              # другой парк не попал
        self.assertEqual(self.store.get(["P07"], "2026-10-08")["veh"], {})  # на следующий день отметок нет

    def test_last_value_wins_and_journal_keeps_history(self):
        for value in (False, True, False):
            self.store.set("2026-10-07", [{"kind": "veh", "id": "P07-V0001", "value": value}], who="127.0.0.1")
        self.assertFalse(self.store.get(["P07"], "2026-10-07")["veh"]["P07-V0001"])
        j = self.store.journal(["P07"], "2026-10-07")
        self.assertEqual([(x["prev"], x["value"]) for x in j], [(True, False), (False, True), (None, False)])
        self.assertEqual(j[0]["who"], "127.0.0.1")

    def test_survives_reopen(self):
        self.store.set("2026-10-07", [{"kind": "drv", "id": "P07-D0009", "value": False}])
        again = MarksStore(self.store.path)
        self.assertEqual(again.get(["P07"], "2026-10-07")["drv"], {"P07-D0009": False})

    def test_bad_request_changes_nothing(self):
        bad = [[{"kind": "bus", "id": "P07-V1", "value": True}], [{"kind": "veh", "id": "V1", "value": True}],
               [{"kind": "veh", "id": "P07-V1", "value": "да"}], []]
        for items in bad:
            with self.assertRaises(MarksError):
                self.store.set("2026-10-07", items)
        with self.assertRaises(MarksError):
            self.store.set("07.10.2026", [{"kind": "veh", "id": "P07-V1", "value": True}])
        # вторая отметка с ошибкой - первая тоже не записана
        with self.assertRaises(MarksError):
            self.store.set("2026-10-07", [{"kind": "veh", "id": "P07-V1", "value": True}, {"kind": "x", "id": "P07-V2", "value": True}])
        self.assertEqual(self.store.get(["P07"], "2026-10-07")["veh"], {})


if __name__ == "__main__":
    unittest.main()
