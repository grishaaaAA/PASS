"""
Объяснения решений для диспетчера (А6).

Каждая функция возвращает словарь, который интерфейс показывает как есть:

    {
      "question": "Почему наряд P07-R12-WD04 не закрыт?",
      "answer":   одна фраза - главный ответ,
      "reasons":  [пункты с цифрами],
      "numbers":  {те же цифры для таблиц и графиков},
    }

Все цифры пересчитываются по дню и плану в момент вопроса, ничего не
хранится заранее: объяснение не может разойтись с планом.
"""

from __future__ import annotations

from collections import Counter

from naryad.core.invariants import (load_labor, may_depart, rest_minutes, shift_limit,
                                    work_minutes)
from naryad.core.model import REASONS, Day, Plan
from naryad.solve.drivers import History, day_base, rest_status
from naryad.solve.vehicles import allowed_classes, drop_costs, _groups

CLASS_NAMES = {"medium": "средний", "big": "большой", "extra_big": "особо большой"}
SCHEDULE_NAMES = {"day_off": "выходной по графику", "sick": "на больничном", "vacation": "в отпуске"}


def _hm(minutes_: int) -> str:
    return f"{minutes_ // 60} ч {minutes_ % 60:02d} мин"


def _clock(t: int) -> str:
    return f"{t // 60 % 24:02d}:{t % 60:02d}"


def _answer(question: str, answer: str, reasons: list, numbers: dict) -> dict:
    return {"question": question, "answer": answer, "reasons": reasons, "numbers": numbers}


def _duty_name(day: Day, duty) -> str:
    if duty.type == "reserve":
        return f"резервный наряд {duty.id}"
    return f"наряд {duty.id} (маршрут {day.routes[duty.route_id].number})"


# --- интервал ------------------------------------------------------------------

def interval(day: Day, plan: Plan, route_id: str, t: int) -> dict:
    """Интервал на маршруте в момент t: по плану нарядов и по тому, что вышло.

    Работающим считается наряд, у которого в этот момент есть и автобус, и
    водитель: автобус без водителя стоит в парке и пассажиров не везёт.
    """
    route = day.routes[route_id]
    active = [d for d in day.duties.values() if d.route_id == route_id
              and d.day_type == day.day_type and d.start <= t < d.end]
    running = [d for d in active if d.id in plan.vehicles
               and any(s.id in plan.drivers and s.start <= t < s.end
                       for s in day.shifts_by_duty.get(d.id, []))]
    planned = round(route.turnaround_min / len(active)) if active else None
    actual = round(route.turnaround_min / len(running)) if running else None
    return {"planned_min": planned, "actual_min": actual, "planned_buses": len(active),
            "running_buses": len(running)}


def _at(t: int) -> str:
    """Время как в остальном API: часы после полуночи идут дальше 24 (25:15).

    Не _clock: тот переводит за полночь (01:15), и интерфейс не смог бы
    сопоставить этот момент с отрезками состояния дня.
    """
    return f"{t // 60:02d}:{t % 60:02d}"


def _line_duties(day: Day) -> dict:
    """Наряды на линии по маршрутам, только на тип дня этого дня."""
    out: dict = {}
    for duty in day.duties.values():
        if duty.type == "line" and duty.route_id and duty.day_type == day.day_type:
            out.setdefault(duty.route_id, []).append(duty)
    return out


def route_interval(day: Day, state, route_id: str, duties: list | None = None) -> dict:
    """Здоровье интервала маршрута за весь день, по состоянию дня.

    Рост интервала считается так же, как в naryad.solve.compare: нарядов
    маршрута по расписанию, делённое на сколько из них реально работает.
    Иначе цифра на экране диспетчера разошлась бы с цифрой, которую мы
    называем заказчику.

    Работающим считается наряд, у которого есть и автобус, и водитель:
    автобус без водителя стоит и пассажиров не везёт. Этим расчёт
    отличается от compare, который сравнивает только расстановку автобусов.

    Интервал меняется только на границах: начало и конец наряда, начало и
    конец каждого отрезка. Между границами он постоянен, поэтому хватает
    проверки в самих границах.
    """
    route = day.routes[route_id]
    duties = duties if duties is not None else _line_duties(day).get(route_id, [])
    spans = {duty.id: state.running(duty.id) for duty in duties}
    moments = {duty.start for duty in duties} | {duty.end for duty in duties}
    for runs in spans.values():
        for begins, ends in runs:
            moments.update((begins, ends))
    ordered = sorted(moments)

    worst, worst_at, worst_running, worst_active = 1.0, None, 0, 0
    stopped_min, stopped_at, peak = 0, None, 0
    for position, moment in enumerate(ordered):
        active = [d for d in duties if d.start <= moment < d.end]
        if not active:
            continue
        peak = max(peak, len(active))
        running = sum(any(a <= moment < b for a, b in spans[d.id]) for d in active)
        if not running:
            following = ordered[position + 1] if position + 1 < len(ordered) else moment
            stopped_min += following - moment
            if stopped_at is None:
                stopped_at = moment
            continue
        growth = len(active) / running
        if growth > worst:
            worst, worst_at, worst_running, worst_active = growth, moment, running, len(active)

    turn = route.turnaround_min
    planned = worst_active or peak      # в худший момент, а без роста - при полном выпуске
    running_then = worst_running or peak
    return {"route_id": route_id, "number": route.number, "priority": route.priority,
            "duties": len(duties),
            "growth": round(worst, 2), "at": _at(worst_at) if worst_at is not None else None,
            "planned_min": round(turn / planned) if planned else None,
            "actual_min": round(turn / running_then) if running_then else None,
            "planned_buses": planned, "running_buses": running_then,
            "stopped_min": stopped_min,
            "stopped_at": _at(stopped_at) if stopped_at is not None else None}


def route_interval_at(day: Day, state, route_id: str, t: int, duties: list | None = None) -> dict:
    """То же на один момент времени."""
    route = day.routes[route_id]
    duties = duties if duties is not None else _line_duties(day).get(route_id, [])
    active = [d for d in duties if d.start <= t < d.end]
    running = sum(any(a <= t < b for a, b in state.running(d.id)) for d in active)
    turn = route.turnaround_min
    return {"route_id": route_id, "number": route.number, "priority": route.priority,
            "duties": len(duties),
            "growth": round(len(active) / running, 2) if running else None,
            "at": _at(t),
            "planned_min": round(turn / len(active)) if active else None,
            "actual_min": round(turn / running) if running else None,
            "planned_buses": len(active), "running_buses": running,
            "stopped_min": 0, "stopped_at": _at(t) if active and not running else None}


def intervals(day: Day, state, t: int | None = None) -> dict:
    """Интервалы по всем маршрутам: за день целиком или на один момент.

    Собрано одним ответом нарочно: показателю «худший рост интервала по
    парку» иначе пришлось бы спрашивать каждый маршрут отдельно, а за день
    это почти тысяча запросов.
    """
    by_route = _line_duties(day)
    rows = [route_interval_at(day, state, route_id, t, duties) if t is not None
            else route_interval(day, state, route_id, duties)
            for route_id, duties in sorted(by_route.items())]
    working = [r for r in rows if r["duties"]]
    grew = [r for r in working if r["growth"] is not None]
    stopped = [r for r in working if r["stopped_min"] > 0 or r["growth"] is None]
    over = [r for r in grew if r["growth"] > 1.25]
    highest = round(max((r["growth"] for r in grew), default=1.0), 2)
    worst = max(grew, key=lambda r: (r["growth"], r["route_id"]), default=None)
    if worst is not None and worst["growth"] <= 1:
        worst = None        # роста нет, называть «худший» маршрут не за что

    if worst is None:
        answer = "Интервалы как по плану"
    else:
        answer = (f"Хуже всего на маршруте {worst['number']}: интервал вырос "
                  f"в {worst['growth']} раза"
                  + (f", в {worst['at']}" if worst["at"] else ""))
    reasons = []
    if stopped:
        reasons.append("маршруты, где не осталось ни одного автобуса: "
                       + ", ".join(r["number"] for r in stopped[:5]))
    if over:
        reasons.append("интервал вырос больше чем на четверть на маршрутах: "
                       + ", ".join(r["number"] for r in over[:5]))
    if not reasons:
        reasons.append(f"на всех {len(working)} маршрутах интервал в пределах плана")

    head = _answer("Что с интервалами" + (f" в {_at(t)}" if t is not None else " за день"),
                   answer, reasons,
                   {"worst_growth": highest,
                    "worst_route": worst["route_id"] if worst else None,
                    "worst_at": worst["at"] if worst else None,
                    "over_25_percent": len(over), "stopped_routes": len(stopped),
                    "routes_total": len(working)})
    return {**head, "routes": rows}


# --- почему этот автобус -------------------------------------------------------

def why_vehicle(day: Day, plan: Plan, duty_id: str) -> dict:
    duty = day.duties[duty_id]
    question = f"Почему на {_duty_name(day, duty)} этот автобус?"
    vehicle_id = plan.vehicles.get(duty_id)
    if vehicle_id is None:
        return why_unfilled(day, plan, duty_id)
    vehicle = day.vehicles[vehicle_id]
    classes = allowed_classes(day, duty)
    reasons = [f"Автобус {vehicle.board_number}: класс «{CLASS_NAMES[vehicle.cls]}», исправен, "
               f"парк {vehicle.park_id} - подходит наряду (нужен класс "
               f"{', '.join(CLASS_NAMES[c] for c in classes)})"]
    own = duty.route_id is not None and vehicle.home_route_id == duty.route_id
    if own:
        answer = f"Автобус {vehicle.board_number} закреплён за маршрутом {day.routes[duty.route_id].number}"
    else:
        answer = f"Автобус {vehicle.board_number} - свободный исправный автобус нужного класса"
        if duty.route_id is not None:
            homes = [v for v in day.vehicles.values() if v.home_route_id == duty.route_id]
            on_route = sum(1 for v in homes if day.duties.get(_duty_of(plan, v.id) or "", None)
                           and day.duties[_duty_of(plan, v.id)].route_id == duty.route_id)
            elsewhere = sum(1 for v in homes if _duty_of(plan, v.id) and v.id != vehicle_id) - on_route
            broken = Counter(_condition(v.condition) for v in homes if v.condition != "ok")
            nrd = sum(1 for d in day.duties.values() if d.route_id == duty.route_id
                      and d.day_type == day.day_type)
            if homes:
                parts = [f"{on_route} на других нарядах этого маршрута"]
                if elsewhere:
                    parts.append(f"{elsewhere} на других маршрутах")
                if broken:
                    parts.append("не вышли: " + ", ".join(f"{n} {k}" for k, n in broken.items()))
                reasons.append(f"Закреплённых за маршрутом автобусов - {len(homes)}, нарядов - {nrd}: "
                               + "; ".join(parts))
            else:
                reasons.append("За маршрутом не закреплено автобусов")
    if vehicle.home_route_id and not own:
        reasons.append(f"Автобус закреплён за маршрутом {day.routes[vehicle.home_route_id].number}, "
                       f"но там он сегодня не нужен")
    return _answer(question, answer, reasons,
                   {"vehicle": vehicle.board_number, "own_route": own, "class": vehicle.cls})


def _duty_of(plan: Plan, vehicle_id: str) -> str | None:
    return next((d for d, v in plan.vehicles.items() if v == vehicle_id), None)


def _condition(condition: str) -> str:
    return {"repair": "в ремонте", "maintenance": "на ТО", "accident": "после ДТП"}.get(condition, condition)


# --- почему этот водитель ------------------------------------------------------

def why_driver(day: Day, plan: Plan, shift_id: str, history: History | None = None,
               labor: dict | None = None) -> dict:
    labor = labor or load_labor()
    history = history or History()
    shift = day.shifts[shift_id]
    duty = day.duties[shift.duty_id]
    question = f"Почему на смене {shift_id} ({_clock(shift.start)}-{_clock(shift.end)}) этот водитель?"
    driver_id = plan.drivers.get(shift_id)
    if driver_id is None:
        return why_unfilled(day, plan, shift_id, history, labor)
    driver = day.drivers[driver_id]
    vehicle_id = plan.vehicles[duty.id]
    state = history.get(driver_id)
    work, limit = work_minutes(shift.length, labor), shift_limit(labor)
    reasons = [f"Работает по графику, медосмотр {'пройден' if driver.medical == 'passed' else 'ещё не пройден'}, "
               f"допуск к классу «{CLASS_NAMES[day.vehicles[vehicle_id].cls]}»"]
    if work > limit:
        reasons.append(f"ВНИМАНИЕ: смена с подготовкой {_hm(work)} больше дневной нормы {_hm(limit)} "
                       f"- проверка плана считает это нарушением (п. 4 Приказа № 160)")
    else:
        reasons.append(f"Смена {_hm(shift.length)}"
                       + (f" (с подготовкой {_hm(work)})" if work != shift.length else "")
                       + f" - в пределах дневной нормы {_hm(limit)}")
    if state.last_end is not None:
        reasons.append(_rest_given(state, day_base(day) + shift.start, labor))
    if state.month_minutes:
        reasons.append(f"В этом месяце отработал {_hm(state.month_minutes)}")
    own = driver.home_vehicle_id == vehicle_id
    if own:
        answer = f"Водитель {driver.tab_number} закреплён за этим автобусом"
    else:
        answer = f"Водитель {driver.tab_number} свободен и по нормам может выйти на эту смену"
        homes = [d for d in day.drivers.values() if d.home_vehicle_id == vehicle_id and d.id != driver_id]
        for h in homes:
            reasons.append(f"Закреплённый водитель {h.tab_number}: {_driver_blocker(day, plan, h, shift, history, labor)}")
    return _answer(question, answer, reasons, {"driver": driver.tab_number, "own_vehicle": own})


def _driver_blocker(day: Day, plan: Plan, driver, shift, history: History, labor: dict) -> str:
    """Почему конкретный водитель не стоит на этой смене."""
    if driver.schedule != "work":
        return SCHEDULE_NAMES.get(driver.schedule, driver.schedule)
    if driver.schedule == "work" and not may_depart(driver, day.meta.get("moment")):
        return "не прошёл медосмотр"
    other = next((s for s, d in plan.drivers.items() if d == driver.id), None)
    if other is not None:
        return f"работает на смене {other}"
    state = history.get(driver.id)
    if rest_status(state, day_base(day) + shift.start, labor) is None:
        return _rest_missing(state, rest_minutes(state.last_end, day_base(day) + shift.start, labor), labor)
    return "свободен; не поставлен, чтобы закрыть другие смены"


def _rest_given(state, start: int, labor: dict) -> str:
    """Отдых перед сменой словами. Вердикт - тот же rest_status, что у расстановки."""
    rest = rest_minutes(state.last_end, start, labor)
    if rest_status(state, start, labor) is None:
        return "Нарушение: " + _rest_missing(state, rest, labor)
    if rest >= labor["min_weekly_rest_min"]:
        return f"Перед сменой отдыхал {_hm(rest)} - еженедельный отдых, не меньше 45 ч (п. 20 Приказа № 160)"
    need = max(labor["min_daily_rest_min"],
               labor["rest_to_work_ratio"] * work_minutes(state.last_length, labor) - labor["meal_break_assumed_min"])
    if rest >= need:
        return f"После прошлой смены отдыхал {_hm(rest)}, нужно не меньше {_hm(need)} (п. 16-17 Приказа № 160)"
    return (f"После прошлой смены отдыхал {_hm(rest)} - сокращённый отдых: до 9 ч можно не больше "
            f"{labor['max_reduced_rests']} раз между еженедельными отдыхами, это {state.reduced + 1}-й "
            f"(п. 17 Приказа № 160)")


def _rest_missing(state, rest: int, labor: dict) -> str:
    """Почему отдыха не хватает - в том же порядке, что проверяет rest_status."""
    if state.in_row >= labor["max_shifts_between_weekly_rests"]:
        return f"уже {state.in_row} смен подряд, положен еженедельный отдых 45 ч (п. 20 Приказа № 160)"
    need = labor["rest_to_work_ratio"] * work_minutes(state.last_length, labor) - labor["meal_break_assumed_min"]
    if rest < need:
        return (f"не отдохнул: после смены {_hm(state.last_length)} отдых {_hm(rest)}, нужно {_hm(need)} "
                f"(двойное время работы, п. 16 Приказа № 160)")
    if rest < labor["min_daily_rest_reduced_min"]:
        return f"не отдохнул: после прошлой смены {_hm(rest)}, нужно 11 ч, сократить можно до 9 ч (п. 17 Приказа № 160)"
    return (f"не отдохнул: после прошлой смены {_hm(rest)}, а сокращать отдых до 9 ч можно не больше "
            f"{labor['max_reduced_rests']} раз между еженедельными отдыхами, уже {state.reduced} (п. 17 Приказа № 160)")


# --- почему не закрыто ----------------------------------------------------------

def why_unfilled(day: Day, plan: Plan, item_id: str, history: History | None = None,
                 labor: dict | None = None) -> dict:
    labor = labor or load_labor()
    history = history or History()
    if item_id in day.shifts:
        return _why_shift_unfilled(day, plan, item_id, history, labor)
    duty = day.duties[item_id]
    reason = plan.unfilled.get(item_id, "")
    question = f"Почему {_duty_name(day, duty)} не закрыт?"
    park = day.parks[duty.park_id]
    classes = allowed_classes(day, duty)
    ok = [v for v in day.vehicles.values() if v.park_id == duty.park_id and v.cls in classes]
    working = [v for v in ok if v.condition == "ok"]
    busy = [v for v in working if v.id in set(plan.vehicles.values())]
    broken = Counter(_condition(v.condition) for v in ok if v.condition != "ok")
    reasons, numbers = [], {"vehicles_class": len(ok), "vehicles_ok": len(working), "vehicles_busy": len(busy)}
    if reason == "park_down":
        answer = f"Парк {park.name} не выпускает автобусы"
    elif reason == "release_limit":
        answer = f"Выпуск парка исчерпан: {park.release(day.day_type)} автобусов"
    else:
        answer = (f"Не хватило автобусов класса «{', '.join(CLASS_NAMES[c] for c in classes)}»: "
                  f"исправны {len(working)}, все {len(busy)} заняты")
        if broken:
            reasons.append("Не вышли: " + ", ".join(f"{n} {k}" for k, n in broken.items()))
    if duty.type == "reserve":
        reasons.append("Резерв выпадает первым: пустой резерв лучше пустого маршрута")
    else:
        group = _groups(day)[duty.route_id]
        order = [d.id for d, _ in drop_costs(day, group)]
        missing = [d for d in group if d.id not in plan.vehicles]
        route = day.routes[duty.route_id]
        mid = (duty.start + duty.end) // 2
        iv = interval(day, plan, duty.route_id, mid)
        reasons.append(f"На маршруте {route.number} (важность {route.priority}) не закрыто "
                       f"{len(missing)} из {len(group)} нарядов")
        if order and order[0] == duty.id:
            reasons.append(f"Это самый короткий наряд маршрута ({_hm(duty.end - duty.start)}): "
                           f"его потеря стоит меньше всего часов на линии")
        if iv["planned_min"] and iv["actual_min"]:
            reasons.append(f"Интервал в {_clock(mid)}: по плану {iv['planned_min']} мин, "
                           f"сейчас {iv['actual_min']} мин")
        reasons.append("Нехватку система распределяет по цене пропуска: важные маршруты теряют "
                       "наряды последними, каждый следующий пропуск на одном маршруте дороже")
        numbers.update(iv)
    return _answer(question, answer, reasons, {**numbers, "reason": reason,
                                               "reason_text": REASONS.get(reason, "")})


def _why_shift_unfilled(day: Day, plan: Plan, shift_id: str, history: History, labor: dict) -> dict:
    shift = day.shifts[shift_id]
    duty = day.duties[shift.duty_id]
    question = f"Почему смена {shift_id} ({_clock(shift.start)}-{_clock(shift.end)}) без водителя?"
    vehicle = day.vehicles.get(plan.vehicles.get(duty.id, ""))

    # Сначала причины, которые не про водителей: иначе объяснение винит людей,
    # когда дело в автобусе или в длине смены.
    if vehicle is None:
        about_duty = why_unfilled(day, plan, duty.id, history, labor)
        return _answer(question,
                       f"Дело не в водителе: на {_duty_name(day, duty)} нет автобуса",
                       [about_duty["answer"]] + about_duty["reasons"], about_duty["numbers"])
    work, limit = work_minutes(shift.length, labor), shift_limit(labor)
    if work > limit:
        extra = labor.get("prep_before_min", 0) + labor.get("prep_after_min", 0)
        return _answer(question,
                       f"На эту смену нельзя поставить никого: с подготовкой и медосмотром "
                       f"{_hm(work)}, дневная норма {_hm(limit)}",
                       [f"Смена {_hm(shift.length)}, подготовка и медосмотры до и после {_hm(extra)}",
                        "Норма смены - п. 4 Приказа Минтранса № 160; длину смены задают данные наряда, "
                        "а не нарядка",
                        "Это вопрос к перевозчику: либо смена короче, либо установленный режим до 12 ч"],
                       {"shift_min": shift.length, "work_min": work, "limit_min": limit,
                        "prep_min": extra})
    cls = vehicle.cls
    drivers = [d for d in day.drivers.values() if d.park_id == duty.park_id]
    blockers = Counter()
    for d in drivers:
        if cls not in d.classes:
            blockers[f"нет допуска к классу «{CLASS_NAMES[cls]}»"] += 1
        else:
            text = _driver_blocker(day, plan, d, shift, history, labor)
            key = ("работают на других сменах" if text.startswith("работает") else
                   "не отдохнули после прошлой смены" if text.startswith("не отдохнул") else
                   "положен еженедельный отдых" if "45 ч" in text else
                   "свободны, но нужны на другие смены" if text.startswith("свободен") else text)
            blockers[key] += 1
    answer = f"Нет водителя, которому по графику и нормам можно выйти на эту смену"
    reasons = [f"Водителей парка: {len(drivers)}"] + [f"{k}: {n}" for k, n in blockers.most_common()]
    return _answer(question, answer, reasons, {"drivers": len(drivers), **dict(blockers)})


# --- вариант замены -------------------------------------------------------------

def explain_option(state, event, options: list, index: int = 0) -> dict:
    """Что даст вариант замены и чем он лучше следующего."""
    option = options[index]
    duties = len(state.vehicles)
    touched = len({key for kind, key, _ in option.changes if kind.startswith("vehicle") and key})
    reasons = [option.note,
               f"Без работы на линии всего (на всех затронутых маршрутах): {_hm(option.lost_minutes)}",
               f"Меняется нарядов: {touched}, остальные {duties - touched} без изменений"]
    if index + 1 < len(options):
        nxt = options[index + 1]
        reasons.append(f"Следующий вариант «{nxt.title}» хуже: цена {nxt.cost:.1f} против {option.cost:.1f}")
    return _answer(f"Что даст вариант «{option.title}»?", option.title, reasons,
                   {"cost": round(option.cost, 2), "lost_minutes": option.lost_minutes,
                    "kind": option.kind, "touched_duties": touched})


# --- сводка дня -----------------------------------------------------------------

def day_summary(day: Day, plan: Plan, history: History | None = None) -> dict:
    """Сводка для диспетчера: что закрыто, что нет и где сосредоточены проблемы."""
    lines = [d for d in day.duties.values() if d.day_type == day.day_type and d.type == "line"]
    filled = [d for d in lines if d.id in plan.vehicles]
    shifts = [s for d in plan.vehicles for s in day.shifts_by_duty.get(d, [])]
    no_vehicle = Counter(day.routes[day.duties[i].route_id].number for i in plan.unfilled
                         if i in day.duties and day.duties[i].type == "line")
    reserves = sum(1 for i in plan.unfilled if i in day.duties and day.duties[i].type == "reserve")
    no_driver = Counter(_route_label(day, day.duties[day.shifts[i].duty_id])
                        for i in plan.unfilled if i in day.shifts)
    answer = (f"На линии {len(filled)} из {len(lines)} нарядов, водители на "
              f"{sum(s.id in plan.drivers for s in shifts)} из {len(shifts)} смен")
    reasons = []
    if no_vehicle:
        reasons.append(f"Нарядов на линии без автобуса: {sum(no_vehicle.values())}. "
                       + _top(no_vehicle, "маршрут "))
    if reserves:
        reasons.append(f"Не выпущено резервных нарядов: {reserves} (резерв уходит первым)")
    if no_driver:
        reasons.append(f"Смен без водителя: {sum(no_driver.values())}. " + _top(no_driver, ""))
    if not reasons:
        reasons.append("Все наряды и смены закрыты")
    return _answer(f"Сводка на {day.meta['date']}", answer, reasons,
                   {"line_filled": len(filled), "line_total": len(lines),
                    "shifts_filled": sum(s.id in plan.drivers for s in shifts),
                    "shifts_total": len(shifts), "reserve_unfilled": reserves,
                    "no_vehicle_by_route": dict(no_vehicle), "no_driver_by_route": dict(no_driver)})


def _route_label(day: Day, duty) -> str:
    return "резерв" if duty.type == "reserve" else f"маршрут {day.routes[duty.route_id].number}"


def _top(counter: Counter, prefix: str, n: int = 5) -> str:
    items = [f"{prefix}{k} - {v}" for k, v in counter.most_common(n)]
    rest = sum(counter.values()) - sum(v for _, v in counter.most_common(n))
    return ", ".join(items) + (f", прочие - {rest}" if rest else "")
