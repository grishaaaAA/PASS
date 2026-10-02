"""
Расстановка водителей по сменам (А4) с памятью о прошлых днях.

Что гарантируется (жёстко):
- допуск к классу автобуса, свой парк, по графику работает, медосмотр
  не провален, одна смена в день, смена не длиннее дневной нормы;
- отдых после предыдущей смены по Приказу Минтранса № 160: не меньше
  11 ч (до 9 ч - не больше 3 раз между еженедельными отдыхами, п. 17) и
  не меньше двойного времени работы (п. 16);
- не больше 6 смен подряд без отдыха 45 ч (п. 20). Что было до первого
  дня с историей, неизвестно: пустая история значит, что перед ним был
  еженедельный отдых. В рабочем режиме историю надо загружать из
  вчерашнего наряда.

Что оптимизируется:
1. Какие смены закрыть. Главное - число закрытых смен с весом важности
   маршрута. Смены разбираются от ценных к дешёвым, и если свободного
   водителя нет, его ищут перестановкой уже назначенных (поиск
   увеличивающей цепочки). Такой порядок даёт лучший возможный набор
   закрытых смен (жадный алгоритм на трансверсальном матроиде).
2. Кого ставить. При выборе водителя порядок предпочтений: закреплённый
   за этим автобусом; затем водители, чей закреплённый автобус сегодня не
   работает (остальные нужны своему автобусу); затем тот, кому не нужен
   сокращённый отдых; затем тот, у кого меньше часов с начала месяца. После расстановки обменами
   сажаем закреплённых водителей на свои автобусы. Это правило, а не
   точный оптимум: главная цель (число смен) от него не страдает.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from naryad.core.invariants import load_labor, shift_limit
from naryad.core.model import Day, Plan

from .vehicles import PRIORITY_WEIGHT, RESERVE_VALUE


def day_base(day: Day) -> int:
    """Начало суток дня в минутах от общей точки отсчёта."""
    return date.fromisoformat(day.meta["date"]).toordinal() * 1440


@dataclass
class DriverState:
    """Что помним о водителе из прошлых дней."""

    last_end: int | None = None      # конец последней смены, минуты от общей точки
    last_length: int = 0             # длина последней смены
    in_row: int = 0                  # смен подряд с последнего отдыха 45 ч
    reduced: int = 0                 # сокращённых отдыхов (9-11 ч) с последнего отдыха 45 ч
    month: str = ""                  # месяц, за который считаются часы
    month_minutes: int = 0           # отработано минут в этом месяце


@dataclass
class History:
    drivers: dict = field(default_factory=dict)

    def get(self, driver_id: str) -> DriverState:
        return self.drivers.setdefault(driver_id, DriverState())


def rest_status(state: DriverState, start: int, labor: dict) -> str | None:
    """Можно ли выйти на смену, начинающуюся в start.

    None - нельзя; "ok" - можно; "reduced" - можно, но отдых сокращённый.
    """
    if state.last_end is None:
        return "ok"
    gap = start - state.last_end
    if gap >= labor["min_weekly_rest_min"]:
        return "ok"
    if state.in_row >= labor["max_shifts_between_weekly_rests"]:
        return None
    need = labor["rest_to_work_ratio"] * state.last_length - labor["meal_break_assumed_min"]
    if gap < need:
        return None
    if gap >= labor["min_daily_rest_min"]:
        return "ok"
    if gap >= labor["min_daily_rest_reduced_min"] and state.reduced < labor["max_reduced_rests"]:
        return "reduced"
    return None


def _shift_value(day: Day, duty, minutes_: int) -> float:
    if duty.type == "reserve":
        return RESERVE_VALUE * minutes_ / 60
    return PRIORITY_WEIGHT[day.routes[duty.route_id].priority] * minutes_ / 60


def solve_drivers(day: Day, vehicle_plan: Plan, history: History | None = None,
                  labor: dict | None = None) -> Plan:
    """Водители на смены закрытых нарядов. История обновляется по итогам дня."""
    labor = labor or load_labor()
    history = history if history is not None else History()
    base, month = day_base(day), day.meta["date"][:7]
    limit = shift_limit(labor)

    plan = Plan(vehicles=dict(vehicle_plan.vehicles), unfilled=dict(vehicle_plan.unfilled),
                transfers=dict(vehicle_plan.transfers),
                meta={**vehicle_plan.meta, "stage": "drivers", "made_by": "naryad.solve.drivers"})

    shifts = []
    for duty_id, vehicle_id in vehicle_plan.vehicles.items():
        duty = day.duties[duty_id]
        for shift in day.shifts_by_duty.get(duty_id, []):
            shifts.append((shift, duty, day.vehicles[vehicle_id]))
    shifts.sort(key=lambda t: (-_shift_value(day, t[1], t[0].length), t[0].id))

    available = [d for d in day.drivers.values()
                 if d.schedule == "work" and d.medical != "failed"]
    working_vehicles = set(vehicle_plan.vehicles.values())
    candidates = {}
    for shift, duty, vehicle in shifts:
        options = []
        if shift.length <= limit:
            for d in available:
                if d.park_id != duty.park_id or vehicle.cls not in d.classes:
                    continue
                state = history.get(d.id)
                status = rest_status(state, base + shift.start, labor)
                if status is None:
                    continue
                if state.month != month:
                    hours = 0
                else:
                    hours = state.month_minutes
                home_here = d.home_vehicle_id == vehicle.id
                home_elsewhere = not home_here and d.home_vehicle_id in working_vehicles
                options.append((not home_here, home_elsewhere, status == "reduced", hours, d.id))
        candidates[shift.id] = [o[-1] for o in sorted(options)]

    sys.setrecursionlimit(max(sys.getrecursionlimit(), 4 * len(available) + 100))
    holder = {}   # водитель -> смена
    taken = {}    # смена -> водитель

    def augment(shift_id: str, seen: set) -> bool:
        for driver_id in candidates[shift_id]:
            if driver_id in seen:
                continue
            seen.add(driver_id)
            if driver_id not in holder or augment(holder[driver_id], seen):
                holder[driver_id] = shift_id
                taken[shift_id] = driver_id
                return True
        return False

    for shift, _, _ in shifts:
        free = next((d for d in candidates[shift.id] if d not in holder), None)
        if free is not None:
            holder[free] = shift.id
            taken[shift.id] = free
        elif not augment(shift.id, set()):
            plan.unfilled[shift.id] = "no_driver"
    _keep_home(day, vehicle_plan, candidates, holder, taken)
    plan.drivers.update(taken)

    for shift_id, driver_id in taken.items():
        shift = day.shifts[shift_id]
        state = history.get(driver_id)
        if rest_status(state, base + shift.start, labor) == "reduced":
            state.reduced += 1
        if state.last_end is not None and base + shift.start - state.last_end >= labor["min_weekly_rest_min"]:
            state.in_row, state.reduced = 0, 0
        state.in_row += 1
        state.last_end, state.last_length = base + shift.end, shift.length
        if state.month != month:
            state.month, state.month_minutes = month, 0
        state.month_minutes += shift.length
    return plan


def _keep_home(day: Day, vehicle_plan: Plan, candidates: dict, holder: dict, taken: dict) -> None:
    """Обмены, которые сажают закреплённых водителей на свои автобусы.

    Меняются только люди, набор закрытых смен остаётся прежним. Каждый
    обмен строго добавляет водителя на своём автобусе, поэтому цикл
    конечен.
    """
    vehicle_of = {s: vehicle_plan.vehicles[day.shifts[s].duty_id] for s in candidates}
    homes = defaultdict(list)
    for d in day.drivers.values():
        if d.home_vehicle_id:
            homes[d.home_vehicle_id].append(d.id)
    allowed = {s: set(c) for s, c in candidates.items()}

    def at_home(driver_id: str, shift_id: str) -> bool:
        return day.drivers[driver_id].home_vehicle_id == vehicle_of[shift_id]

    def improve_once() -> bool:
        for shift_id, current in taken.items():
            if at_home(current, shift_id):
                continue
            for home in homes.get(vehicle_of[shift_id], []):
                if home not in allowed[shift_id]:
                    continue
                other = holder.get(home)
                if other is None:
                    del holder[current]
                    holder[home], taken[shift_id] = shift_id, home
                    return True
                if not at_home(home, other) and current in allowed[other]:
                    holder[home], taken[shift_id] = shift_id, home
                    holder[current], taken[other] = other, current
                    return True
        return False

    while improve_once():
        pass
