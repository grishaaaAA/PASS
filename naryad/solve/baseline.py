"""
Упрощённая модель ручной расстановки - точка отсчёта для сравнения.

Диспетчер идёт по нарядам в порядке выезда и на каждый ставит первый
свободный исправный автобус нужного класса, пока автобусы или лимит
выпуска не кончатся. Важность маршрутов и равномерность интервалов он
не учитывает: на это у человека утром нет времени. Это допущение о
ручной работе, а не описание реального порядка в парке: настоящий
порядок узнаем из стандарта «Планирование, подготовка и выпуск автобусов
на линию».
"""

from __future__ import annotations

from collections import Counter

from naryad.core.model import Day, Plan

from .vehicles import allowed_classes


def baseline_vehicles(day: Day) -> Plan:
    plan = Plan(meta={"made_by": "naryad.solve.baseline", "stage": "vehicles"})
    used, released = set(), Counter()
    vehicles = sorted((v for v in day.vehicles.values() if v.condition == "ok"),
                      key=lambda v: v.board_number)
    duties = sorted((d for d in day.duties.values() if d.day_type == day.day_type),
                    key=lambda d: (d.start, d.id))
    for duty in duties:
        park = day.parks[duty.park_id]
        if park.state == "down":
            plan.unfilled[duty.id] = "park_down"
            continue
        if released[park.id] >= park.release(day.day_type):
            plan.unfilled[duty.id] = "release_limit"
            continue
        classes = allowed_classes(day, duty)
        vehicle = next((v for v in vehicles if v.id not in used and v.park_id == duty.park_id
                        and v.cls in classes), None)
        if vehicle is None:
            plan.unfilled[duty.id] = "no_vehicle"
            continue
        used.add(vehicle.id)
        released[park.id] += 1
        plan.vehicles[duty.id] = vehicle.id
    return plan


def baseline_drivers(day: Day, vehicle_plan: Plan) -> Plan:
    """Ручная расстановка водителей, упрощённо.

    Водитель идёт на смену своего закреплённого автобуса, остальные смены
    в порядке начала получают первого свободного водителя с допуском.
    Проверяются график, медосмотр и допуск, но не отдых после вчерашней
    смены: вручную на сотни водителей это не отследить. Это допущение,
    реальный порядок узнаем у перевозчика.
    """
    plan = Plan(vehicles=dict(vehicle_plan.vehicles), unfilled=dict(vehicle_plan.unfilled),
                transfers=dict(vehicle_plan.transfers),
                meta={"made_by": "naryad.solve.baseline", "stage": "drivers"})
    ready = [d for d in sorted(day.drivers.values(), key=lambda d: d.tab_number)
             if d.schedule == "work" and d.medical != "failed"]
    used = set()
    shifts = sorted((s for duty_id in vehicle_plan.vehicles
                     for s in day.shifts_by_duty.get(duty_id, [])), key=lambda s: (s.start, s.id))
    for shift in shifts:
        duty = day.duties[shift.duty_id]
        vehicle = day.vehicles[vehicle_plan.vehicles[duty.id]]
        fits = [d for d in ready if d.id not in used and d.park_id == duty.park_id
                and vehicle.cls in d.classes]
        driver = next((d for d in fits if d.home_vehicle_id == vehicle.id), fits[0] if fits else None)
        if driver is None:
            plan.unfilled[shift.id] = "no_driver"
            continue
        used.add(driver.id)
        plan.drivers[shift.id] = driver.id
    return plan
