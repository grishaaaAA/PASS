"""Весь город и переброски между парками (А7). Запуск: python -m unittest"""

import dataclasses
import itertools
import time
import unittest
from collections import Counter, defaultdict

from naryad.core.invariants import check_plan
from naryad.core.model import Day, Plan
from naryad.data.generate import generate
from naryad.solve.city import TRANSFER_COST, add_transfers, solve_city, with_park_shortage
from naryad.solve.vehicles import _groups, allowed_classes, drop_costs, solve_vehicles

from tests.test_core import WEEKDAY

CITY = Day.from_dict(generate("case", WEEKDAY, seed=1))
TWO_PARKS = dict(park_count=2, release_per_park=16, routes_total=6,
                 class_mix={"medium": 12, "big": 12, "extra_big": 8})
THREE_PARKS = dict(park_count=3, release_per_park=16, routes_total=9,
                   class_mix={"medium": 12, "big": 12, "extra_big": 8})


def line_lost(day, plan, park_id=None):
    return [d for d in day.duties.values() if d.day_type == day.day_type and d.type == "line"
            and (park_id is None or d.park_id == park_id) and d.id not in plan.vehicles]


class TestCity(unittest.TestCase):
    """Общий CITY один на модуль, поэтому каждый тест обязан его не портить.

    Сценарный день делает with_park_shortage, и он копирует все словари дня.
    Сторож ниже ловит, если кто-то снова начнёт менять общий объект.
    """

    @classmethod
    def setUpClass(cls):
        cls.guard = (len(CITY.parks), sorted((p.id, p.state, p.release_weekday)
                                             for p in CITY.parks.values()),
                     sum(v.condition == "ok" for v in CITY.vehicles.values()))

    def tearDown(self):
        now = (len(CITY.parks), sorted((p.id, p.state, p.release_weekday)
                                       for p in CITY.parks.values()),
               sum(v.condition == "ok" for v in CITY.vehicles.values()))
        self.assertEqual(now, self.guard, "тест изменил общий CITY: сценарии станут зависеть друг от друга")

    def test_normal_day_no_transfers(self):
        plan = solve_city(CITY)
        self.assertEqual(check_plan(CITY, plan), [])
        self.assertEqual(plan.transfers, {})
        self.assertEqual(line_lost(CITY, plan), [])

    def test_emergency_in_one_park(self):
        day = with_park_shortage(CITY, "P03", 0.25)
        without = solve_city(day, transfers=False, drivers=False)
        start = time.perf_counter()
        plan = solve_city(day)
        self.assertLess(time.perf_counter() - start, 10.0)
        self.assertEqual(check_plan(day, plan), [])
        self.assertTrue(line_lost(day, without, "P03"))
        self.assertEqual(line_lost(day, plan), [])
        self.assertTrue(all(p == "P03" for p in plan.transfers.values()))
        # водителей без пройденного медосмотра утром не выпускают, поэтому смены
        # без водителя возможны - но только на резерве, линия закрыта целиком
        driverless = [sid for sid, reason in plan.unfilled.items() if reason == "no_driver"]
        self.assertTrue(all(day.duties[day.shifts[sid].duty_id].type == "reserve"
                            for sid in driverless),
                        f"смены линии без водителя: {driverless}")
        donors = Counter(day.vehicles[v].park_id for v in plan.transfers)
        self.assertGreater(len(donors), 1)  # нагрузка распределена между парками

    def test_transfer_only_when_worth_it(self):
        day = with_park_shortage(CITY, "P03", 0.25)
        plan = add_transfers(day, solve_vehicles(day), cost=1e9)
        self.assertEqual(plan.transfers, {})

    def test_gas_buses_stay_in_gas_parks(self):
        day = with_park_shortage(CITY, "P03", 0.4)
        plan = add_transfers(day, solve_vehicles(day), gas_parks={"P07"})
        self.assertTrue(plan.transfers)
        self.assertFalse([v for v in plan.transfers if day.vehicles[v].fuel == "gas"])
        self.assertEqual(check_plan(day, plan, drivers=False), [])

    def test_park_down_gives_nothing(self):
        day = with_park_shortage(CITY, "P03", 0.25)
        donor = "P05"
        day.parks[donor] = dataclasses.replace(day.parks[donor], state="down")
        plan = add_transfers(day, solve_vehicles(day))
        self.assertTrue(plan.transfers)
        self.assertFalse([v for v in plan.transfers if day.vehicles[v].park_id == donor])

    def test_release_limit_respected(self):
        day = with_park_shortage(CITY, "P03", 0.25)
        park = day.parks["P03"]
        released = Counter(day.duties[d].park_id for d in solve_vehicles(day).vehicles)["P03"]
        day.parks["P03"] = dataclasses.replace(park, release_weekday=released + 5,
                                               release_weekend=released + 5)
        plan = add_transfers(day, solve_vehicles(day))
        self.assertEqual(len(plan.transfers), 5)
        self.assertEqual(check_plan(day, plan, drivers=False), [])

    def test_plan_round_trip_with_transfers(self):
        day = with_park_shortage(CITY, "P03", 0.25)
        plan = add_transfers(day, solve_vehicles(day))
        self.assertEqual(Plan.from_dict(plan.to_dict()).transfers, plan.transfers)

    def test_greedy_matches_brute_force(self):
        """Малый город из двух парков: польза перебросок как у полного перебора."""
        checked = 0
        for seed in range(1, 7):
            base = Day.from_dict(generate("case", WEEKDAY, seed=seed, **TWO_PARKS))
            first = sorted(base.parks)[0]
            for share in (0.3, 0.5, 0.7):
                day = with_park_shortage(base, first, share, seed)
                before = solve_vehicles(day)
                after = add_transfers(day, Plan(vehicles=dict(before.vehicles),
                                                unfilled=dict(before.unfilled)))
                self.assertAlmostEqual(_gain(day, before, after), _best_gain(day, before), places=6)
                checked += 1
        self.assertEqual(checked, 18)

    def test_exact_with_gas_and_several_limits(self):
        """Нехватка в двух парках, урезанные лимиты выпуска, газ: польза как у полного перебора."""
        checked = binding = 0
        # (seed, доля в первом парке, во втором, мест сверх выпуска); третий - тот, где жадный
        # выбор по одному автобусу ошибался: польза 365,5 вместо 373,1
        for seed, first, second, extra in ((1, 0.5, 0.4, 1), (2, 0.6, 0.6, 2), (3, 0.4, 0.7, 2), (4, 0.5, 0.4, 2)):
            base = Day.from_dict(generate("case", WEEKDAY, seed=seed, **THREE_PARKS))
            parks = sorted(base.parks)
            day = with_park_shortage(with_park_shortage(base, parks[0], first, seed), parks[1], second, seed + 1)
            before = solve_vehicles(day)
            released = Counter(day.duties[d].park_id for d in before.vehicles)
            for park_id in parks[:2]:  # принимающим паркам оставляем место только на часть автобусов
                room = released[park_id] + extra
                day.parks[park_id] = dataclasses.replace(day.parks[park_id], release_weekday=room,
                                                         release_weekend=room)
            for gas in ({parks[0]}, {parks[1], parks[2]}, None):
                after = add_transfers(day, Plan(vehicles=dict(before.vehicles),
                                                unfilled=dict(before.unfilled)), gas_parks=gas)
                self.assertEqual(check_plan(day, after, drivers=False), [])
                if gas is not None:
                    self.assertFalse([v for v, p in after.transfers.items()
                                      if day.vehicles[v].fuel == "gas" and p not in gas])
                exact = _exact_gain(day, before, gas)
                self.assertAlmostEqual(_gain(day, before, after), exact, places=6)
                binding += any(Counter(day.duties[d].park_id for d in after.vehicles)[p]
                               == day.parks[p].release_weekday for p in parks[:2])
                checked += 1
        self.assertEqual(checked, 12)
        self.assertGreater(binding, 0)  # лимит выпуска действительно ограничивал


def _exact_gain(day, before, gas_parks):
    """Полный перебор: сколько автобусов вернуть на каждый маршрут; подбор бортов - паросочетанием."""
    used = set(before.vehicles.values())
    spare = [v for v in sorted(day.vehicles.values(), key=lambda v: v.id)
             if v.condition == "ok" and v.id not in used and day.parks[v.park_id].state != "down"]
    released = Counter(day.duties[d].park_id for d in before.vehicles)
    routes = []
    for key, duties in _groups(day).items():
        lost = [d for d in duties if before.unfilled.get(d.id) == "no_vehicle"]
        if duties[0].type == "line" and lost:
            routes.append((duties[0], [c for _, c in drop_costs(day, duties)], len(lost)))

    def fits(vehicle, duty):
        return (vehicle.park_id != duty.park_id and vehicle.cls in allowed_classes(day, duty)
                and not (gas_parks is not None and vehicle.fuel == "gas" and duty.park_id not in gas_parks))

    def matched(slots):
        owner = {}

        def augment(i, seen):
            for v in spare:
                if fits(v, slots[i]) and v.id not in seen:
                    seen.add(v.id)
                    if v.id not in owner or augment(owner[v.id], seen):
                        owner[v.id] = i
                        return True
            return False
        return all(augment(i, set()) for i in range(len(slots)))

    best = 0.0
    for counts in itertools.product(*(range(m + 1) for _, _, m in routes)):
        per_park = Counter()
        for (duty, _, _), t in zip(routes, counts):
            per_park[duty.park_id] += t
        if any(released[p] + n > day.parks[p].release(day.day_type) for p, n in per_park.items()):
            continue
        gain = sum(sum(costs[m - 1 - k] - TRANSFER_COST for k in range(t))
                   for (_, costs, m), t in zip(routes, counts))
        if gain > best and matched([duty for (duty, _, _), t in zip(routes, counts) for _ in range(t)]):
            best = gain
    return best


def _route_state(day, plan):
    """Маршрут -> (цены пропусков по порядку, сколько выпало)."""
    out = {}
    for key, duties in _groups(day).items():
        if duties[0].type == "line":
            out[key] = ([c for _, c in drop_costs(day, duties)],
                        sum(d.id not in plan.vehicles for d in duties))
    return out


def _gain(day, before, after):
    gain = 0.0
    b, a = _route_state(day, before), _route_state(day, after)
    for key, (costs, m_before) in b.items():
        m_after = a[key][1]
        gain += sum(costs[m_after:m_before])
    return gain - TRANSFER_COST * len(after.transfers)


def _best_gain(day, before):
    """Полный перебор: сколько автобусов перебросить на каждый маршрут."""
    used = set(before.vehicles.values())
    pool = Counter(v.cls for v in day.vehicles.values()
                   if v.condition == "ok" and v.id not in used)
    released = Counter(day.duties[d].park_id for d in before.vehicles)
    routes = [(key, day.duties[next(d.id for d in duties)]) for key, duties in _groups(day).items()
              if duties[0].type == "line"]
    states = _route_state(day, before)
    choices = []
    for key, duty in routes:
        costs, m = states[key]
        own_spare = sum(1 for v in day.vehicles.values() if v.condition == "ok" and v.id not in used
                        and v.park_id == duty.park_id and v.cls in allowed_classes(day, duty))
        choices.append(range(m + 1) if m and own_spare == 0 else range(1))
    best = 0.0
    for counts in itertools.product(*choices):
        need, per_park = Counter(), Counter()
        for (key, duty), t in zip(routes, counts):
            cls = allowed_classes(day, duty)[0]
            need[(cls, duty.park_id)] += t
            per_park[duty.park_id] += t
        ok = all(per_park[p] + released[p] <= day.parks[p].release(day.day_type) for p in per_park)
        for (cls, park), t in need.items():
            other = sum(1 for v in day.vehicles.values() if v.condition == "ok" and v.id not in used
                        and v.cls == cls and v.park_id != park)
            ok = ok and t <= other
        by_class = defaultdict(int)
        for (cls, _), t in need.items():
            by_class[cls] += t
        ok = ok and all(t <= pool[c] for c, t in by_class.items())
        if not ok:
            continue
        gain = 0.0
        for (key, duty), t in zip(routes, counts):
            costs, m = states[key]
            gain += sum(costs[m - t:m]) - TRANSFER_COST * t
        best = max(best, gain)
    return best


if __name__ == "__main__":
    unittest.main()
