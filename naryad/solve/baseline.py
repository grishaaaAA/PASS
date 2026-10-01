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
