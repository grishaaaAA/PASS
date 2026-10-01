"""
Расстановка автобусов по нарядам (А3): лучший возможный план.

Когда автобусов хватает, закрываются все наряды. Когда не хватает,
решается главный вопрос - какие наряды оставить пустыми. Ответ задаёт
«цена пропуска» наряда:

    цена = вес важности маршрута x часы наряда x (n / (n - k + 1)) ** 3

где n - нарядов на маршруте, k - какой по счёту наряд маршрута выпадает.
Дробь n / (n - k + 1) - во сколько раз вырастет интервал: первый
выпавший наряд из 20 почти незаметен, десятый - уже рост интервала вдвое.
Куб делает каждый следующий пропуск на том же маршруте резко дороже,
поэтому нехватка размазывается по маршрутам, а не обнуляет один из них.

Веса важности 2 / 1,5 / 1 и куб выбраны сравнением вариантов на днях с
нехваткой 15% (naryad.solve.compare): при них важные маршруты не теряют
нарядов и ни на одном маршруте интервал не растёт больше чем на 25%.
Веса 4 / 2 / 1 с первой степенью отдавали всю нехватку мелким
второстепенным маршрутам (рост интервала до 1,6 раза).
Внутри маршрута первыми выпадают самые короткие наряды (пиковые ОДН):
так теряется меньше часов работы на линии. Резерв выпадает раньше любого
наряда на линии.

Почему это лучший план, а не просто хороший. Каждый маршрут допускает
один класс автобусов, поэтому задача распадается на части «парк x класс»
с двумя ограничениями: автобусов класса и лимит выпуска парка. Цена
каждого следующего пропуска на маршруте не меньше предыдущей. Для такой
задачи жадный выбор - каждый следующий автобус туда, где его отсутствие
дороже всего - даёт точный оптимум (жадный алгоритм на ламинарном
матроиде). Тесты сверяют это полным перебором на малых примерах.

Какой именно автобус на какой наряд: сначала маршрут получает «свои»
автобусы (закреплённые за ним), потом остальные.
"""

from __future__ import annotations

from collections import defaultdict

from naryad.core.model import Day, Duty, Plan

PRIORITY_WEIGHT = {1: 2.0, 2: 1.5, 3: 1.0}
GROWTH_POWER = 3
RESERVE_VALUE = 1e-3  # резерв дешевле любого наряда на линии


def allowed_classes(day: Day, duty: Duty) -> tuple:
    if duty.type == "line":
        return day.routes[duty.route_id].allowed_classes
    return (duty.vehicle_class,)


def _cost(weight: float, minutes_: int, n: int, k: int) -> float:
    """Цена k-го пропуска на маршруте из n нарядов."""
    return weight * minutes_ / 60 * (n / (n - k + 1)) ** GROWTH_POWER


def drop_costs(day: Day, duties: list, weights: dict = PRIORITY_WEIGHT) -> list:
    """Наряды маршрута в порядке выпадения и цена каждого пропуска.

    Возвращает [(наряд, цена)], где цены не убывают: первым выпадает
    самый дешёвый.
    """
    duties = sorted(duties, key=lambda d: (d.end - d.start, d.id))
    n = len(duties)
    if not duties:
        return []
    if duties[0].type == "reserve":
        return [(d, RESERVE_VALUE * (d.end - d.start) / 60) for d in duties]
    weight = weights[day.routes[duties[0].route_id].priority]
    return [(d, _cost(weight, d.end - d.start, n, k)) for k, d in enumerate(duties, start=1)]


def _groups(day: Day) -> dict:
    """Наряды дня по группам: маршрут или резерв парка по классу."""
    groups = defaultdict(list)
    for duty in day.duties.values():
        if duty.day_type != day.day_type:
            continue
        key = duty.route_id if duty.type == "line" else ("reserve", duty.park_id, duty.vehicle_class)
        groups[key].append(duty)
    return groups


def choose_duties(day: Day, weights: dict = PRIORITY_WEIGHT) -> tuple:
    """Какие наряды закрыть: жадно по убыванию цены пропуска.

    Возвращает (множество закрываемых нарядов, причины для остальных).
    """
    supply = defaultdict(int)  # (парк, класс) -> исправных автобусов
    for v in day.vehicles.values():
        if v.condition == "ok":
            supply[(v.park_id, v.cls)] += 1
    limit = {p.id: (0 if p.state == "down" else p.release(day.day_type)) for p in day.parks.values()}

    units = []  # (цена, наряд): заполняем от дорогих к дешёвым
    for duties in _groups(day).values():
        units.extend((cost, duty.id) for duty, cost in drop_costs(day, duties, weights))
    units.sort(key=lambda u: (-u[0], u[1]))

    chosen, reasons = set(), {}
    for _, duty_id in units:
        duty = day.duties[duty_id]
        classes = allowed_classes(day, duty)
        cls = next((c for c in classes if supply[(duty.park_id, c)] > 0), None)
        if day.parks[duty.park_id].state == "down":
            reasons[duty_id] = "park_down"
        elif limit[duty.park_id] <= 0:
            reasons[duty_id] = "release_limit"
        elif cls is None:
            reasons[duty_id] = "no_vehicle"
        else:
            supply[(duty.park_id, cls)] -= 1
            limit[duty.park_id] -= 1
            chosen.add(duty_id)
    return chosen, reasons


def solve_vehicles(day: Day, weights: dict = PRIORITY_WEIGHT) -> Plan:
    """План по автобусам: лучшее закрытие нарядов + свои автобусы на свои маршруты."""
    chosen, reasons = choose_duties(day, weights)
    free = defaultdict(list)  # (парк, класс) -> исправные автобусы по бортовым номерам
    for v in sorted(day.vehicles.values(), key=lambda v: v.board_number):
        if v.condition == "ok":
            free[(v.park_id, v.cls)].append(v)

    plan = Plan(meta={"made_by": "naryad.solve.vehicles", "stage": "vehicles"})
    plan.unfilled.update(reasons)
    # Сначала маршруты забирают закреплённых за ними, потом остальные.
    ordered = sorted(chosen, key=lambda d: (day.duties[d].type != "line", d))
    waiting = []
    for duty_id in ordered:
        duty = day.duties[duty_id]
        for cls in allowed_classes(day, duty):
            pool = free[(duty.park_id, cls)]
            own = next((v for v in pool if duty.route_id and v.home_route_id == duty.route_id), None)
            if own is not None:
                pool.remove(own)
                plan.vehicles[duty_id] = own.id
                break
        else:
            waiting.append(duty_id)
    for duty_id in waiting:
        duty = day.duties[duty_id]
        cls = next(c for c in allowed_classes(day, duty) if free[(duty.park_id, c)])
        pool = free[(duty.park_id, cls)]
        # чужие закреплённые - в последнюю очередь
        vehicle = next((v for v in pool if v.home_route_id is None), pool[0])
        pool.remove(vehicle)
        plan.vehicles[duty_id] = vehicle.id
    return plan


def total_cost(day: Day, plan: Plan, weights: dict = PRIORITY_WEIGHT) -> float:
    """Суммарная цена пропусков плана: чем меньше, тем лучше.

    Считается по тем нарядам, которые реально выпали: длинный выпавший
    наряд стоит дороже короткого, и каждый следующий пропуск на маршруте -
    дороже предыдущего.
    """
    total = 0.0
    for duties in _groups(day).values():
        missing = [d for d in duties if d.id not in plan.vehicles]
        if not missing:
            continue
        missing.sort(key=lambda d: (d.end - d.start, d.id))
        n = len(duties)
        if missing[0].type == "reserve":
            total += sum(RESERVE_VALUE * (d.end - d.start) / 60 for d in missing)
            continue
        weight = weights[day.routes[missing[0].route_id].priority]
        total += sum(_cost(weight, d.end - d.start, n, k) for k, d in enumerate(missing, start=1))
    return total
