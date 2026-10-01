"""
Весь город (А7): расстановка по всем паркам и переброски между ними.

Запуск: python -m naryad.solve.city [--park P03 --share 0.25]

Порядок:
1. Каждый парк расставляет свои автобусы (А3, точный оптимум внутри парка).
2. Остатки: в одних парках есть лишние исправные автобусы, в других -
   незакрытые наряды того же класса.
3. Переброска: лишний автобус уходит туда, где его отсутствие дороже
   всего, но только если польза больше цены переброски. Цена переброски
   задаётся в тех же единицах, что цена пропуска наряда (А3).

Почему так, а не одной общей моделью: свои автобусы парк использует
всегда, переброска - исключение на день, и диспетчеры парков должны
видеть её отдельной строкой.

Допущения (уточнить у перевозчика):
- цена переброски одинакова для любой пары парков: координат парков в
  данных нет;
- газовые автобусы можно перебрасывать только в парки, где есть газовая
  инфраструктура (по сайту перевозчика - парки 3, 5 и 7). В данных
  генератора этого признака нет, поэтому список задаётся параметром
  gas_parks; None - без ограничения;
- водителя на переброшенный автобус даёт принимающий парк.
"""

from __future__ import annotations

import argparse
import dataclasses
import heapq
import random
import time
from collections import Counter, defaultdict

from naryad.core.invariants import check_plan
from naryad.core.model import Day, Plan
from naryad.data.generate import generate

from .drivers import solve_drivers
from .vehicles import _groups, allowed_classes, drop_costs, solve_vehicles

TRANSFER_COST = 1.0  # как потеря 1 часа работы на второстепенном маршруте


def add_transfers(day: Day, plan: Plan, cost: float = TRANSFER_COST,
                  gas_parks: set | None = None) -> Plan:
    """Перебросить лишние автобусы в парки, где не хватает. План меняется на месте."""
    used = set(plan.vehicles.values())
    spare = defaultdict(list)  # класс -> [(парк, автобус)] лишние исправные
    for v in sorted(day.vehicles.values(), key=lambda v: (v.park_id, v.board_number)):
        if v.condition == "ok" and v.id not in used and day.parks[v.park_id].state != "down":
            spare[v.cls].append(v)
    released = Counter(day.duties[d].park_id for d in plan.vehicles)

    heap = []  # (-ценность следующего наряда маршрута, маршрут)
    missing = {}
    for key, duties in _groups(day).items():
        if duties[0].type != "line":
            continue
        lost = [d for d in duties if plan.unfilled.get(d.id) == "no_vehicle"]
        if not lost:
            continue
        costs = drop_costs(day, duties)
        missing[key] = (costs, sorted(lost, key=lambda d: d.end - d.start, reverse=True))
        heapq.heappush(heap, (-costs[len(lost) - 1][1], key))

    while heap:
        value, route_id = heapq.heappop(heap)
        if -value <= cost:
            break
        costs, lost = missing[route_id]
        duty = lost[0]
        park = day.parks[duty.park_id]
        if released[park.id] >= park.release(day.day_type):
            continue
        donor = _donor(spare, allowed_classes(day, duty), duty.park_id, gas_parks)
        if donor is None:
            continue
        spare[donor.cls].remove(donor)
        plan.vehicles[duty.id] = donor.id
        plan.transfers[donor.id] = duty.park_id
        plan.unfilled.pop(duty.id)
        released[park.id] += 1
        lost.pop(0)
        if lost:
            heapq.heappush(heap, (-costs[len(lost) - 1][1], route_id))
    return plan


def _donor(spare: dict, classes: tuple, park_id: str, gas_parks: set | None):
    """Лишний автобус из другого парка: из того, где лишних больше всего."""
    best = None
    for cls in classes:
        by_park = Counter(v.park_id for v in spare[cls] if v.park_id != park_id)
        for v in spare[cls]:
            if v.park_id == park_id or (gas_parks is not None and v.fuel == "gas"
                                        and park_id not in gas_parks):
                continue
            if best is None or by_park[v.park_id] > by_park[best.park_id]:
                best = v
    return best


def solve_city(day: Day, transfers: bool = True, cost: float = TRANSFER_COST,
               gas_parks: set | None = None, drivers: bool = True) -> Plan:
    plan = solve_vehicles(day)
    if transfers:
        add_transfers(day, plan, cost, gas_parks)
    return solve_drivers(day, plan) if drivers else plan


def with_park_shortage(day: Day, park_id: str, share: float, seed: int = 1) -> Day:
    """Копия дня, где в одном парке доля исправных автобусов не вышла (ЧП в парке)."""
    rng = random.Random(seed)
    out = dataclasses.replace(day, vehicles=dict(day.vehicles))
    ok = sorted((v for v in day.vehicles.values() if v.park_id == park_id and v.condition == "ok"),
                key=lambda v: v.id)
    for v in rng.sample(ok, round(len(ok) * share)):
        out.vehicles[v.id] = dataclasses.replace(v, condition="repair")
    return out


def city_report(day: Day, plan: Plan) -> dict:
    """По каждому парку: закрыто на линии, резерв, переброски, смены."""
    rows = {}
    for park in sorted(day.parks.values(), key=lambda p: p.id):
        lines = [d for d in day.duties.values() if d.park_id == park.id
                 and d.day_type == day.day_type and d.type == "line"]
        shifts = [s for d in lines if d.id in plan.vehicles for s in day.shifts_by_duty.get(d.id, [])]
        rows[park.id] = {
            "на линии": f"{sum(d.id in plan.vehicles for d in lines)}/{len(lines)}",
            "важных потеряно": sum(d.id not in plan.vehicles and day.routes[d.route_id].priority == 1
                                   for d in lines),
            "принято": sum(1 for v, p in plan.transfers.items() if p == park.id),
            "отдано": sum(1 for v in plan.transfers if day.vehicles[v].park_id == park.id),
            "смены": f"{sum(s.id in plan.drivers for s in shifts)}/{len(shifts)}",
        }
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--park", default="P03", help="парк, где случилось ЧП")
    parser.add_argument("--share", type=float, default=0.25, help="доля автобусов, не вышедших в этом парке")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)
    day = with_park_shortage(Day.from_dict(generate("case", "2026-10-05", seed=args.seed)),
                             args.park, args.share, args.seed)
    for label, with_transfers in (("без перебросок", False), ("с перебросками", True)):
        start = time.perf_counter()
        plan = solve_city(day, transfers=with_transfers)
        seconds = time.perf_counter() - start
        problems = check_plan(day, plan)
        print(f"\n{label}: {seconds:.1f} с, нарушений в плане: {len(problems)}")
        rows = city_report(day, plan)
        keys = list(next(iter(rows.values())))
        print("парк  " + "  ".join(f"{k:>15}" for k in keys))
        for park_id, row in rows.items():
            print(f"{park_id:5} " + "  ".join(f"{row[k]!s:>15}" for k in keys))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
