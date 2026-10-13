"""Правила выбора между законными вариантами. Запуск: python -m unittest

Нормы такие правила не нарушают, поэтому проверки законности плана их не
ловят: проверка намеренными порчами кода 13.10.2026 показала, что их можно
было сломать незаметно. Здесь на каждое правило - маленький пример, где
правило и решает, кого или что взять.
"""

from __future__ import annotations

import threading
import unittest

from naryad.core.invariants import load_labor
from naryad.core.model import Day
from naryad.ops.replan import (RESERVE_COST_PER_HOUR, Breakdown, Losses, OpsState, free_drivers,
                               options_for)
from naryad.solve.drivers import History, day_base, solve_drivers
from naryad.solve.vehicles import solve_vehicles

from tests.test_core import SAMPLES
from tests.test_ops import MORNING, important_running, morning_state
from tests.test_permits import DUTY, SHIFT, person, tiny

SAMPLE = SAMPLES / "park7_weekday.json"
OTHER_ROUTE = "P07-R02"   # маршрут парка №7, у которого в маленьком дне нет нарядов


def small_day(boards: tuple, drivers: list, homes: dict | None = None) -> Day:
    """Один наряд маршрута 3 (смена 06:10-14:55), эти автобусы и водители; homes - борт -> маршрут."""
    raw = tiny((DUTY,), boards, drivers)
    for vehicle in raw["vehicles"]:
        if vehicle["board_number"] in (homes or {}):
            vehicle["home_route_id"] = homes[vehicle["board_number"]]
    return Day.from_dict(raw)


class TestVehicleChoice(unittest.TestCase):

    def test_bus_of_another_route_goes_last(self):
        """Своего автобуса у маршрута нет: берётся ничей, а закреплённый за другим маршрутом - последним,
        хотя по номеру он первый."""
        day = small_day(("7003", "7079"), [person(1)], {"7003": OTHER_ROUTE})
        self.assertEqual(solve_vehicles(day).vehicles, {DUTY: "P07-V0079"})

    def test_bus_of_another_make_is_not_taken_from_another_route(self):
        """Водитель обкатан только на ЛиАЗ, на наряд встал VOLGABUS: из простаивающих ЛиАЗ берётся ничей,
        а не закреплённый за другим маршрутом с меньшим номером."""
        day = small_day(("7003", "7079", "7083"), [person(1, makes=["ЛиАЗ"])], {"7079": OTHER_ROUTE})
        self.assertEqual(solve_vehicles(day).vehicles, {DUTY: "P07-V0003"}, "по номеру первым идёт VOLGABUS 7003")
        plan = solve_drivers(day, solve_vehicles(day))
        self.assertEqual((plan.vehicles, plan.drivers), ({DUTY: "P07-V0083"}, {SHIFT: "T1"}))

    def test_bus_is_not_swapped_when_it_closes_nothing(self):
        """Автобус другой марки ставится, только если закрывает больше смен. Водитель обкатан на МАЗ, а МАЗ
        в парке нет: обмен VOLGABUS на ЛиАЗ ничего не даёт, и автобусы не должны меняться туда-сюда без конца."""
        day = small_day(("7003", "7079"), [person(1, makes=["МАЗ"])])
        done = {}
        worker = threading.Thread(target=lambda: done.update(plan=solve_drivers(day, solve_vehicles(day))), daemon=True)
        worker.start()
        worker.join(60)
        self.assertFalse(worker.is_alive(), "обмен автобусов по марке зациклился")
        self.assertEqual(done["plan"].vehicles, {DUTY: "P07-V0003"})
        self.assertIn(SHIFT, done["plan"].unfilled)


class TestDriverChoice(unittest.TestCase):

    def test_full_rest_before_reduced(self):
        """Сокращённый отдых (9-11 ч) - запас на крайний случай: если есть водитель с полным отдыхом,
        ставят его, даже если по номеру первым идёт уставший."""
        day = small_day(("7003",), [person(1), person(2)])
        history = History()
        tired = history.get("T1")   # вчера кончил так, что до смены 10 ч отдыха
        tired.last_end, tired.last_length, tired.in_row = day_base(day) + day.shifts[SHIFT].start - 600, 4 * 60, 1
        self.assertEqual(solve_drivers(day, solve_vehicles(day), history).drivers, {SHIFT: "T2"})
        alone = small_day(("7003",), [person(1)])   # других нет - выходит и после сокращённого отдыха
        history = History()
        tired = history.get("T1")
        tired.last_end, tired.last_length, tired.in_row = day_base(alone) + alone.shifts[SHIFT].start - 600, 4 * 60, 1
        self.assertEqual(solve_drivers(alone, solve_vehicles(alone), history).drivers, {SHIFT: "T1"})

    def test_free_driver_with_full_rest_is_offered_first(self):
        """Замена внутри дня: водитель после сокращённого отдыха в списке свободных - последним."""
        state = morning_state()
        duty = important_running(state)
        labor = load_labor()
        found = free_drivers(state, duty, MORNING, MORNING + 60, labor)
        self.assertGreater(len(found), 2)
        first = found[0]
        tired = state.history.get(first)
        tired.last_end, tired.last_length, tired.in_row = day_base(state.day) + MORNING - 600, 4 * 60, 1
        again = free_drivers(state, duty, MORNING, MORNING + 60, labor)
        self.assertEqual(again[-1], first)
        self.assertEqual(again[:-1], found[1:])


class TestReplacementChoice(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        day = Day.load(SAMPLE)
        cls.state = OpsState.from_plan(day, solve_drivers(day, solve_vehicles(day)))
        cls.cases = []   # (наряд, все варианты на сход его автобуса)
        for t in range(6 * 60, 22 * 60, 89):
            for duty in sorted(day.duties.values(), key=lambda d: d.id)[::7]:
                if duty.type != "line" or not duty.start <= t < duty.end - 60:
                    continue
                vehicle = cls.state.vehicle_at(duty.id, t)
                if vehicle:
                    cls.cases.append((duty, options_for(cls.state, Breakdown(vehicle, t), limit=None)))

    def donors(self):
        out = [(duty, option, options) for duty, options in self.cases for option in options if option.kind == "donor"]
        self.assertGreater(len(out), 10, "сходов, где есть смысл снять автобус с другого маршрута, должно быть много")
        return out

    def test_donor_only_when_cheaper_than_doing_nothing(self):
        """Снять автобус с другого маршрута предлагается, только если это дешевле, чем не заменять."""
        for duty, donor, options in self.donors():
            none = next(o for o in options if o.kind == "none")
            self.assertLess(donor.cost, none.cost, f"{duty.id}: {donor.title}")

    def test_donor_comes_from_another_route(self):
        """Автобус со своего же маршрута - не замена: интервал на маршруте всё равно растёт."""
        day = self.state.day
        for duty, donor, _ in self.donors():
            route = day.routes[duty.route_id].number
            self.assertFalse(donor.title.endswith(f"с маршрута {route}"), f"{duty.id}: {donor.title}")

    def test_spent_reserve_has_a_price(self):
        """Час израсходованного резерва стоит RESERVE_COST_PER_HOUR: резерв не бесплатный, но дешевле линии."""
        day = self.state.day
        reserve = next(d for d in sorted(day.duties.values(), key=lambda d: d.id) if d.type == "reserve")
        losses = Losses(self.state)
        self.assertAlmostEqual(losses.cost(reserve.id, reserve.start, reserve.start + 120), 2 * RESERVE_COST_PER_HOUR)
        line = important_running(self.state)
        self.assertGreater(losses.cost(line.id, MORNING, MORNING + 120), 2 * RESERVE_COST_PER_HOUR)


if __name__ == "__main__":
    unittest.main()
