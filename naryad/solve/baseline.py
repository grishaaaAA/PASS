"""
Упрощённые модели ручной расстановки - точка отсчёта для сравнения.

Это допущения о ручной работе, а не описание реального порядка в парке:
настоящий порядок узнаем из стандарта «Планирование, подготовка и выпуск
автобусов на линию». Модели нарочно аккуратные: диспетчер закрывает
сначала линию, ставит свои автобусы на свои маршруты и сверяет отдых
водителей со вчерашним нарядом. Сравнение с ними показывает, что даёт
сама оптимизация, а не то, что ручной способ сделан заведомо плохо.
"""

from __future__ import annotations

from collections import Counter

from naryad.core.invariants import load_labor, may_depart, rest_minutes, shift_limit, work_minutes
from naryad.core.model import Day, Plan

from .drivers import day_base
from .vehicles import allowed_classes


def baseline_vehicles(day: Day) -> Plan:
    """Ручная расстановка автобусов.

    Диспетчер идёт по нарядам на линии в порядке выезда, резерв - после
    линии. На наряд ставит закреплённый за маршрутом автобус, если он
    свободен, иначе первый свободный исправный автобус нужного класса,
    пока автобусы или лимит выпуска не кончатся. Важность маршрутов и
    равномерность интервалов он не учитывает: утром на это нет времени.
    """
    plan = Plan(meta={"made_by": "naryad.solve.baseline", "stage": "vehicles"})
    used, released = set(), Counter()
    vehicles = sorted((v for v in day.vehicles.values() if v.condition == "ok"),
                      key=lambda v: v.board_number)
    duties = sorted((d for d in day.duties.values() if d.day_type == day.day_type),
                    key=lambda d: (d.type != "line", d.start, d.id))
    for duty in duties:
        park = day.parks[duty.park_id]
        if park.state == "down":
            plan.unfilled[duty.id] = "park_down"
            continue
        if released[park.id] >= park.release(day.day_type):
            plan.unfilled[duty.id] = "release_limit"
            continue
        classes = allowed_classes(day, duty)
        fits = [v for v in vehicles if v.id not in used and v.park_id == duty.park_id and v.cls in classes]
        vehicle = next((v for v in fits if duty.route_id and v.home_route_id == duty.route_id),
                       fits[0] if fits else None)
        if vehicle is None:
            plan.unfilled[duty.id] = "no_vehicle"
            continue
        used.add(vehicle.id)
        released[park.id] += 1
        plan.vehicles[duty.id] = vehicle.id
    return plan


def baseline_drivers(day: Day, vehicle_plan: Plan, yesterday: dict | None = None,
                     labor: dict | None = None) -> Plan:
    """Ручная расстановка водителей.

    Смены на линии раньше резерва, внутри - в порядке начала. Водитель идёт
    на смену своего закреплённого автобуса, остальные смены получают
    первого свободного водителя с допуском.
    Проверяются график, медосмотр, допуск и отдых по вчерашнему наряду:
    не меньше 11 ч и не меньше двойной смены (как в labor.json).
    Сокращённый отдых до 9 ч и счёт смен подряд вручную не ведутся.
    Смена, которая с подготовкой и медосмотрами длиннее дневной нормы,
    остаётся пустой: на неё нельзя поставить никого.

    yesterday - водитель -> (конец вчерашней смены от общей точки отсчёта,
    её длина); дополняется сменами этого дня. None - отдых не проверяется
    совсем (так видно, от чего защищает проверка).
    """
    labor = labor or load_labor()
    plan = Plan(vehicles=dict(vehicle_plan.vehicles), unfilled=dict(vehicle_plan.unfilled),
                transfers=dict(vehicle_plan.transfers),
                meta={"made_by": "naryad.solve.baseline", "stage": "drivers"})
    base = day_base(day)

    def rested(driver_id: str, start: int) -> bool:
        if yesterday is None or driver_id not in yesterday:
            return True
        end, length = yesterday[driver_id]
        need = max(labor["min_daily_rest_min"],
                   labor["rest_to_work_ratio"] * work_minutes(length, labor) - labor["meal_break_assumed_min"])
        return rest_minutes(end, base + start, labor) >= need

    ready = [d for d in sorted(day.drivers.values(), key=lambda d: d.tab_number)
             if may_depart(d, day.meta.get("moment"))]
    used = set()
    shifts = sorted((s for duty_id in vehicle_plan.vehicles for s in day.shifts_by_duty.get(duty_id, [])),
                    key=lambda s: (day.duties[s.duty_id].type != "line", s.start, s.id))
    limit = shift_limit(labor)
    for shift in shifts:
        if work_minutes(shift.length, labor) > limit:
            plan.unfilled[shift.id] = "no_driver"
            continue
        duty = day.duties[shift.duty_id]
        vehicle = day.vehicles[vehicle_plan.vehicles[duty.id]]
        fits = [d for d in ready if d.id not in used and d.park_id == duty.park_id
                and vehicle.cls in d.classes and rested(d.id, shift.start)]
        driver = next((d for d in fits if d.home_vehicle_id == vehicle.id), fits[0] if fits else None)
        if driver is None:
            plan.unfilled[shift.id] = "no_driver"
            continue
        used.add(driver.id)
        plan.drivers[shift.id] = driver.id
    if yesterday is not None:
        for shift_id, driver_id in plan.drivers.items():
            shift = day.shifts[shift_id]
            yesterday[driver_id] = (base + shift.end, shift.length)
    return plan
