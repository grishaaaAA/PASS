import unittest

from naryad.core.model import Day, Plan
from naryad.ops.replan import OpsState, Segment
from naryad.web.api import DayRecord
from naryad.web.assignment_history import affected, history
from naryad.web.dispatcher import registry


class TestAssignmentHistory(unittest.TestCase):
    def test_bus_replacement_and_gap_inside_driver_shift(self):
        raw = registry("P07", "2026-10-12")
        day = Day.from_dict(raw)
        duty = next(d for d in day.duties.values() if d.route_id)
        shift = day.shifts_by_duty[duty.id][0]
        bus, replacement = list(day.vehicles)[:2]
        driver = next(iter(day.drivers))
        plan = Plan(vehicles={duty.id: bus}, drivers={shift.id: driver})
        morning = OpsState.from_plan(day, plan)
        current = OpsState.from_plan(day, plan)
        split = shift.start + 60
        current.vehicles[duty.id] = [
            Segment(duty.start, split, bus),
            Segment(split + 15, duty.end, replacement),
        ]
        ids = affected(morning, current)
        self.assertTrue({duty.route_id, bus, replacement, driver}.issubset(ids))
        rec = DayRecord("test-day", raw, day, plan, current,
                        log=[{"kind": "event", "title": "Замена", "affected_ids": ids}])

        driver_day, = history([rec], "drv", driver, "P07")
        rows = driver_day["assignments"]
        self.assertEqual([row["bus"]["id"] if row["bus"] else None for row in rows], [bus, None, replacement])
        self.assertEqual(rows[0]["to"], rows[1]["from"])
        self.assertEqual(rows[1]["to"], rows[2]["from"])
        self.assertEqual([row["bus"]["id"] for row in driver_day["previous"]], [bus])

        bus_day, = history([rec], "veh", replacement, "P07")
        self.assertEqual([row["driver"]["id"] if row["driver"] else None for row in bus_day["assignments"]], [driver, None])
        self.assertEqual(bus_day["log"][0]["title"], "Замена")
        self.assertEqual(history([rec], "drv", driver, "P01"), [])
        route_day, = history([rec], "route", duty.route_id, "P07")
        self.assertEqual(route_day["assignments"][0]["route"]["id"], duty.route_id)
