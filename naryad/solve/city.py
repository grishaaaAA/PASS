"""
Весь город (А7): расстановка по всем паркам и переброски между ними.

Запуск: python -m naryad.solve.city [--park P03 --share 0.25]

Порядок:
1. Каждый парк расставляет свои автобусы (А3, точный оптимум внутри парка).
2. Остатки: в одних парках есть лишние исправные автобусы, в других -
   незакрытые наряды того же класса.
3. Переброска: лишние автобусы уходят туда, где их отсутствие дороже
   всего, но только если польза больше цены переброски. Цена переброски
   задаётся в тех же единицах, что цена пропуска наряда (А3). Расчёт
   точный - поток минимальной стоимости: лишние автобусы (парк, класс,
   топливо) -> маршруты с пустыми нарядами -> лимит выпуска принимающего
   парка. Так одновременно учитываются классы, газ и лимиты всех парков.
   При равной пользе автобусы берутся понемногу из разных парков.

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
import random
import time
from collections import Counter, defaultdict, deque

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
    spare = defaultdict(list)  # (парк, класс, топливо) -> лишние исправные автобусы
    for v in sorted(day.vehicles.values(), key=lambda v: (v.park_id, v.board_number)):
        if v.condition == "ok" and v.id not in used and day.parks[v.park_id].state != "down":
            spare[(v.park_id, v.cls, v.fuel)].append(v)
    released = Counter(day.duties[d].park_id for d in plan.vehicles)

    flow = _Flow()
    lost_by_route = {}
    for key, duties in _groups(day).items():
        if duties[0].type != "line":
            continue
        lost = sorted((d for d in duties if plan.unfilled.get(d.id) == "no_vehicle"),
                      key=lambda d: (d.end - d.start, d.id), reverse=True)
        if not lost:
            continue
        lost_by_route[key] = lost
        park = duties[0].park_id
        costs = drop_costs(day, duties)  # первые len(lost) - выпавшие, по возрастанию цены
        for k in range(len(lost)):       # k-й вернувшийся автобус возвращает k-й по цене пропуск
            flow.add(("route", key), ("park", park), 1, -(costs[len(lost) - 1 - k][1] - cost))
        for group in spare:
            g_park, cls, fuel = group
            if g_park != park and cls in allowed_classes(day, duties[0]) and not (
                    gas_parks is not None and fuel == "gas" and park not in gas_parks):
                flow.add(("group", group), ("route", key), len(spare[group]), 0.0)
    for park_id in {lost[0].park_id for lost in lost_by_route.values()}:
        park = day.parks[park_id]
        flow.add(("park", park_id), "T", max(0, park.release(day.day_type) - released[park_id]), 0.0)
    for group, vehicles in spare.items():  # чуть дороже каждый следующий из той же группы: берём понемногу
        for k in range(len(vehicles)):
            flow.add("S", ("group", group), 1, 1e-6 * (k + 1))
    flow.run("S", "T")

    for (route_key, group), n in sorted(flow.used(("route",), ("group",)).items(), key=str):
        for _ in range(n):
            duty = lost_by_route[route_key].pop(0)
            vehicle = spare[group].pop(0)
            plan.vehicles[duty.id] = vehicle.id
            plan.transfers[vehicle.id] = duty.park_id
            plan.unfilled.pop(duty.id)
    return plan


class _Flow:
    """Поток минимальной стоимости: кратчайшие пути (Беллман-Форд с очередью).

    Пути набираются по одному, пока очередной путь выгоден (цена < 0):
    цены путей не убывают, поэтому остановка на первом невыгодном даёт
    самую выгодную переброску.
    """

    def __init__(self):
        self.edges = []                 # [куда, остаток, цена, обратное ребро]
        self.out = defaultdict(list)    # узел -> номера рёбер
        self.start = {}                 # номер ребра -> откуда

    def add(self, u, v, cap: int, cost: float) -> None:
        if cap <= 0:
            return
        self.out[u].append(len(self.edges))
        self.start[len(self.edges)] = u
        self.edges.append([v, cap, cost, len(self.edges) + 1])
        self.out[v].append(len(self.edges))
        self.start[len(self.edges)] = v
        self.edges.append([u, 0, -cost, len(self.edges) - 1])

    def run(self, s, t) -> None:
        while True:
            dist, prev, queue, inside = {s: 0.0}, {}, deque([s]), {s}
            while queue:
                u = queue.popleft()
                inside.discard(u)
                for e in self.out[u]:
                    v, cap, c, _ = self.edges[e]
                    if cap > 0 and dist[u] + c < dist.get(v, float("inf")) - 1e-12:
                        dist[v], prev[v] = dist[u] + c, e
                        if v not in inside:
                            inside.add(v)
                            queue.append(v)
            if t not in dist or dist[t] >= -1e-12:
                return
            v = t
            while v != s:
                e = prev[v]
                self.edges[e][1] -= 1
                self.edges[self.edges[e][3]][1] += 1
                v = self.start[e]

    def used(self, to_kind: tuple, from_kind: tuple) -> dict:
        """Сколько пущено по рёбрам «откуда -> куда» заданных видов: {(куда, откуда): n}."""
        out = {}
        for e in range(0, len(self.edges), 2):
            u, (v, cap, _, back) = self.start[e], self.edges[e]
            sent = self.edges[back][1]
            if sent and isinstance(u, tuple) and isinstance(v, tuple) \
                    and u[0] == from_kind[0] and v[0] == to_kind[0]:
                out[(v[1], u[1])] = out.get((v[1], u[1]), 0) + sent
        return out


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
