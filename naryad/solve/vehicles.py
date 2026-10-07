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
нехваткой 15% (naryad.solve.compare): при них важные маршруты почти не
теряют нарядов (в среднем 0,2 против 4,9 у аккуратного ручного способа).
Плата за это - второстепенные маршруты: на них интервал в отдельные
моменты дня растёт больше чем на 25% (в среднем на 0,9 маршрута против
1,9 у ручного способа, при нехватке 20% - 4,9 против 3,1). Рост
интервала в цене пропуска считается по нарядам за день, а не по
автобусам в каждый момент - это упрощение. Веса 4 / 2 / 1 с первой
степенью отдавали всю нехватку мелким второстепенным маршрутам (рост
до 1,6 раза по нарядам за день).
Внутри маршрута первыми выпадают самые короткие наряды (пиковые ОДН):
так теряется меньше часов работы на линии. Резерв выпадает раньше любого
наряда на линии.

Почему это лучший план, а не просто хороший. Наряды одного маршрута
взаимозаменяемы, цена каждого следующего пропуска на маршруте не меньше
предыдущей. Наборы нарядов, которые можно закрыть исправными автобусами
допустимых классов в пределах лимита выпуска парка, образуют матроид
(трансверсальный, усечённый лимитом). Для такой задачи жадный выбор -
каждый следующий автобус туда, где его отсутствие дороже всего, с
проверкой «набор ещё выполним» - даёт точный оптимум. Если маршрут
допускает несколько классов, проверка ищет цепочку перестановок нарядов
между классами. Тесты сверяют это полным перебором на малых примерах.

Какой именно автобус на какой наряд: сначала маршрут получает «свои»
автобусы (закреплённые за ним), потом остальные.
"""

from __future__ import annotations

from collections import defaultdict

from naryad.core.model import Day, Duty, Plan

PRIORITY_WEIGHT = {1: 2.0, 2: 1.5, 3: 1.0}
GROWTH_POWER = 3
STOP_SHARE = 0.25     # маршрут встал совсем: считаем, что осталась четверть автобуса
RESERVE_VALUE = 1e-3  # резерв дешевле любого наряда на линии


def allowed_classes(day: Day, duty: Duty) -> tuple:
    if duty.type == "line":
        return day.routes[duty.route_id].allowed_classes
    return (duty.vehicle_class,)


def _cost(weight: float, minutes_: int, n: int, k: int) -> float:
    """Цена k-го пропуска на маршруте из n нарядов.

    Рост интервала - это нарядов по плану, делённое на то, сколько
    работает после пропуска: n / (n - k). Та же формула, что у пересчёта
    внутри дня (naryad/ops/replan.py, класс Losses), чтобы утренняя цена и
    дневная считались одинаково. Если маршрут встал совсем, считаем, что
    осталась четверть автобуса: полная остановка дороже любой частичной
    потери, но не бесконечна.
    """
    return weight * minutes_ / 60 * (n / max(n - k, STOP_SHARE)) ** GROWTH_POWER


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


class _Classes:
    """Какой класс автобусов закрывает каждый выбранный наряд парка.

    Наряд маршрута может допускать несколько классов. Новый наряд берёт
    свободный класс, а если свободных нет - ищется цепочка перестановок:
    наряд A уступает свой класс и переходит на другой допустимый, где есть
    место, и так далее. Так набор выбранных нарядов остаётся выполнимым
    тогда и только тогда, когда автобусов хватает на всех.
    """

    def __init__(self, supply: dict):
        self.free = dict(supply)              # (парк, класс) -> свободных автобусов
        self.of = {}                          # наряд -> класс
        self.holders = defaultdict(list)      # (парк, класс) -> наряды

    def add(self, day: Day, duty) -> bool:
        park = duty.park_id
        start = allowed_classes(day, duty)
        parent = {c: None for c in start}     # класс -> (откуда, какой наряд переезжает)
        queue = list(start)
        while queue:
            cls = queue.pop(0)
            if self.free.get((park, cls), 0) > 0:
                self._shift(day, duty, park, cls, parent)
                return True
            for other in self.holders[(park, cls)]:
                for nxt in allowed_classes(day, day.duties[other]):
                    if nxt not in parent:
                        parent[nxt] = (cls, other)
                        queue.append(nxt)
        return False

    def _shift(self, day: Day, duty, park: str, cls: str, parent: dict) -> None:
        self.free[(park, cls)] -= 1
        while parent[cls] is not None:        # по цепочке назад: каждый наряд переезжает
            prev, mover = parent[cls]
            self.holders[(park, prev)].remove(mover)
            self.holders[(park, cls)].append(mover)
            self.of[mover] = cls
            cls = prev
        self.holders[(park, cls)].append(duty.id)
        self.of[duty.id] = cls


def choose_duties(day: Day, weights: dict = PRIORITY_WEIGHT) -> tuple:
    """Какие наряды закрыть: жадно по убыванию цены пропуска.

    Возвращает (выбранные наряды -> класс автобуса, причины для остальных).
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

    classes, reasons = _Classes(supply), {}
    for _, duty_id in units:
        duty = day.duties[duty_id]
        if day.parks[duty.park_id].state == "down":
            reasons[duty_id] = "park_down"
        elif limit[duty.park_id] <= 0:
            reasons[duty_id] = "release_limit"
        elif not classes.add(day, duty):
            reasons[duty_id] = "no_vehicle"
        else:
            limit[duty.park_id] -= 1
    return classes.of, reasons


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
        pool = free[(duty.park_id, chosen[duty_id])]
        own = next((v for v in pool if duty.route_id and v.home_route_id == duty.route_id), None)
        if own is None:
            waiting.append(duty_id)
            continue
        pool.remove(own)
        plan.vehicles[duty_id] = own.id
    for duty_id in waiting:
        pool = free[(day.duties[duty_id].park_id, chosen[duty_id])]
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
