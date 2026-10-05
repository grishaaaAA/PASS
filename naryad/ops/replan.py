"""
Оперативный пересчёт внутри дня (А5): сход, ДТП, неявка водителя.

Утренний план говорит, кто на каком наряде весь день. Внутри дня этого
мало: в 08:40 автобус сошёл, с 09:10 на наряде резервный. Поэтому день
описывается отрезками: «наряд D, с 05:25 до 08:40 - автобус 7023,
с 09:10 - автобус 7105». Так же для водителей на сменах.

На событие система строит варианты замены, считает цену каждого и
отдаёт до трёх лучших разных видов. Диспетчер выбирает, вариант
применяется к состоянию дня.

Цена варианта - в тех же единицах, что в А3: часы простоя на линии с
весом важности маршрута и штрафом за рост интервала. Рост интервала
считается в каждый момент простоя по нарядам маршрута: сколько должно
работать по плану, делённое на сколько работает, в кубе. Если маршрут
встал совсем, считаем, что осталась четверть автобуса: это дороже любой
частичной потери. Формула одна для маршрута, где сошёл автобус, для
маршрута-донора и для неявки водителя. Плюс небольшая цена за
израсходованный резерв: он нужен на следующий сход.

Правило отрезков (одно на весь модуль): новый отрезок никогда не ставится
поверх существующего. Окно замены ограничивается началом ближайшего
следующего отрезка наряда и смены, свобода автобуса и водителя проверяется
на всём отрезке по всем нарядам, а выбывший ресурс снимается со всех
нарядов и смен сразу. Допуск водителя проверяется по классу того автобуса,
который он реально поведёт: на маршрут бывает допущено несколько классов.

Допущения (уточнить у перевозчика; пока координат парков и конечных
нет, время подачи - постоянное):
- подача автобуса из парка на маршрут и обратно - SUPPLY_MIN минут;
- перегон автобуса с одного маршрута на другой - TRANSFER_MIN минут;
- водитель сошедшего автобуса остаётся с ним; на замену едет другой
  водитель; на пересменке водители наряда принимают новый автобус по плану;
- если сход на время, замена работает только до возвращения своего
  автобуса: потом резерв возвращается в резерв, донор - на свой маршрут;
- штраф за полную остановку маршрута - допущение, сверить с договором
  (штрафы за невыполненные рейсы);
- замена ищется только в своём парке. Переброска между парками - решение
  утренней нарядки на весь день (А7, naryad/solve/city.py), внутри дня её
  не пересматриваем: автобус чужого парка на наряде без записи в transfers
  проверка считает нарушением. Межпарковую замену внутри дня добавим, если
  перевозчик скажет, что так делают.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from naryad.core.invariants import (Violation, load_labor, may_depart, shift_limit,
                                    work_minutes)
from naryad.core.model import Day, Plan
from naryad.solve.drivers import History, day_base, remember, rest_status
from naryad.solve.vehicles import GROWTH_POWER, PRIORITY_WEIGHT, STOP_SHARE

SUPPLY_MIN = 30
TRANSFER_MIN = 20
RESERVE_COST_PER_HOUR = 0.2  # цена часа израсходованного резерва


@dataclass
class Segment:
    start: int
    end: int
    who: str  # автобус или водитель


@dataclass
class OpsState:
    """Состояние дня: кто где с какого по какое время."""

    day: Day
    vehicles: dict = field(default_factory=dict)   # наряд -> [Segment]
    drivers: dict = field(default_factory=dict)    # смена -> [Segment]
    down_vehicles: dict = field(default_factory=dict)  # автобус -> с какого времени
    down_drivers: dict = field(default_factory=dict)   # водитель -> с какого времени
    repairs: dict = field(default_factory=dict)        # автобус -> [(с, до)]: в ремонте после схода на время
    history: History = field(default_factory=History)  # прошлые дни водителей
    transfers: dict = field(default_factory=dict)      # автобус -> в какой парк переброшен на день
    log: list = field(default_factory=list)

    @classmethod
    def from_plan(cls, day: Day, plan: Plan, history: History | None = None) -> "OpsState":
        state = cls(day=day, history=copy.deepcopy(history) if history else History(),
                    transfers=dict(plan.transfers))
        for duty_id, vehicle_id in plan.vehicles.items():
            duty = day.duties[duty_id]
            state.vehicles[duty_id] = [Segment(duty.start, duty.end, vehicle_id)]
        for shift_id, driver_id in plan.drivers.items():
            shift = day.shifts[shift_id]
            state.drivers[shift_id] = [Segment(shift.start, shift.end, driver_id)]
        return state

    def vehicle_at(self, duty_id: str, t: int) -> str | None:
        return next((s.who for s in self.vehicles.get(duty_id, []) if s.start <= t < s.end), None)

    def driver_at(self, shift_id: str, t: int) -> str | None:
        return next((s.who for s in self.drivers.get(shift_id, []) if s.start <= t < s.end), None)

    def duty_of_vehicle(self, vehicle_id: str, t: int) -> str | None:
        for duty_id, segments in self.vehicles.items():
            if any(s.who == vehicle_id and s.start <= t < s.end for s in segments):
                return duty_id
        return None

    def vehicle_busy_after(self, vehicle_id: str, t: int) -> bool:
        return any(s.who == vehicle_id and s.end > t for segs in self.vehicles.values() for s in segs)

    def in_repair(self, vehicle_id: str, start: int, end: int) -> tuple | None:
        """Окно ремонта автобуса, которое пересекает [start, end), или None."""
        return next(((a, b) for a, b in self.repairs.get(vehicle_id, [])
                     if a < end and start < b), None)

    def driver_worked(self, driver_id: str) -> list:
        return [s for segs in self.drivers.values() for s in segs if s.who == driver_id]

    def shift_at(self, duty_id: str, t: int):
        return next((s for s in self.day.shifts_by_duty.get(duty_id, []) if s.start <= t < s.end), None)

    def running(self, duty_id: str) -> list:
        """Когда наряд реально работает: на нём есть и автобус, и водитель смены."""
        buses = [(s.start, s.end) for s in self.vehicles.get(duty_id, [])]
        shifts = self.day.shifts_by_duty.get(duty_id, [])
        if not shifts:
            return buses
        people = [(max(s.start, sh.start), min(s.end, sh.end))
                  for sh in shifts for s in self.drivers.get(sh.id, [])]
        return [(max(a, c), min(b, e)) for a, b in buses for c, e in people if max(a, c) < min(b, e)]


# --- события ---------------------------------------------------------------

@dataclass(frozen=True)
class Breakdown:
    """Сход автобуса с линии. duration - через сколько минут вернётся (None - до конца дня)."""
    vehicle_id: str
    at: int
    duration: int | None = None
    title: str = "Сход с линии"


@dataclass(frozen=True)
class Accident:
    """ДТП: автобус до конца дня, водитель тоже выбывает."""
    vehicle_id: str
    at: int
    title: str = "ДТП"


@dataclass(frozen=True)
class NoShow:
    """Водитель не вышел или не допущен медосмотром."""
    driver_id: str
    at: int
    title: str = "Неявка водителя"


@dataclass
class Option:
    kind: str
    title: str
    cost: float
    lost_minutes: int
    changes: list           # [(вид, ключ, отрезок)], применяются по порядку
    note: str = ""


# --- цена потерь -------------------------------------------------------------

class Losses:
    """Цена простоя нарядов в одном состоянии дня.

    По каждому маршруту один раз строится лента событий: когда наряд
    начинается и кончается по плану, когда реально работает. Цена
    простоя - сумма по кускам ленты: вес важности x часы x рост
    интервала, где рост = (нарядов по плану / работает) ** GROWTH_POWER.
    """

    def __init__(self, state: OpsState):
        self.state = state
        self.timeline = {}  # маршрут -> [(время, +-по плану, +-работает)]

    def _route(self, route_id: str) -> list:
        if route_id not in self.timeline:
            day, out = self.state.day, []
            for d in day.duties.values():
                if d.route_id == route_id and d.day_type == day.day_type:
                    out += [(d.start, 1, 0), (d.end, -1, 0)]
                    for a, b in self.state.running(d.id):
                        out += [(a, 0, 1), (b, 0, -1)]
            self.timeline[route_id] = out
        return self.timeline[route_id]

    def cost(self, duty_id: str, start: int, end: int, remove: bool = False) -> float:
        """Цена простоя наряда с start до end.

        remove=True - наряд ещё работает, а его автобус собираются снять
        (донор): считаем так, будто уже сняли.
        """
        day = self.state.day
        duty = day.duties[duty_id]
        start, end = max(start, duty.start), min(end, duty.end)
        if end <= start:
            return 0.0
        if duty.type == "reserve":
            return RESERVE_COST_PER_HOUR * (end - start) / 60
        points = list(self._route(duty.route_id))
        if remove:
            for a, b in self.state.running(duty_id):
                points += [(a, 0, -1), (b, 0, 1)]
        weight = PRIORITY_WEIGHT[day.routes[duty.route_id].priority]
        total, planned, running, prev = 0.0, 0, 0, start
        for t, d_planned, d_running in sorted(points):
            lo, hi = max(prev, start), min(t, end)
            if lo < hi and planned:
                total += weight * (hi - lo) / 60 * (planned / max(running, STOP_SHARE)) ** GROWTH_POWER
            planned, running, prev = planned + d_planned, running + d_running, t
        return total


# --- кого можно поставить ----------------------------------------------------

def _free_until(table: dict, key: str, start: int, end: int) -> int:
    """Докуда на наряде (смене) key нет отрезка, начиная со start и не дальше end.

    Так новый отрезок не наезжает на тот, что уже стоит: например на свой
    автобус, который вернётся после схода на время.
    """
    for segment in table.get(key, []):
        if start < segment.start < end:
            end = segment.start
        elif segment.start <= start < segment.end:
            return start
    return end


def _busy_until(state: OpsState, kind: str, who: str, start: int, end: int,
                skip: tuple = ()) -> int:
    """Докуда ресурс свободен начиная со start и не дальше end.

    Занятость считается по всем нарядам и сменам, кроме skip: оттуда
    выбранный вариант ресурс снимает сам. Возвращает start, если ресурс
    занят уже в момент start - значит ставить его нельзя.
    """
    table = state.vehicles if kind == "vehicle" else state.drivers
    for key, segments in table.items():
        if key in skip:
            continue
        for segment in segments:
            if segment.who != who:
                continue
            if segment.start <= start < segment.end:
                return start
            if start < segment.start < end:
                end = segment.start
    return end


def free_drivers(state: OpsState, duty, start: int, end: int, labor: dict,
                 cls: str | None = None, classes=None) -> list:
    """Водители, которые законно могут работать на наряде с start до end.

    Не работали сегодня, работают по графику, медосмотр не провален, не
    выбыли, допуск к классу, отдых после вчерашней смены по нормам,
    смена не длиннее дневной нормы.

    cls - класс автобуса, который водитель реально поведёт. Он может
    отличаться от класса наряда: на маршрут допущено несколько классов.
    classes - несколько классов сразу, когда в окне на наряде стоят разные
    автобусы: нужен допуск к каждому. Если не передано ни то, ни другое -
    класс наряда.
    """
    if work_minutes(end - start, labor) > shift_limit(labor):
        return []
    worked = {s.who for segs in state.drivers.values() for s in segs}
    base = day_base(state.day)
    need = tuple(classes) if classes else (cls or state.day.duties[duty.id].vehicle_class,)
    out = []
    for d in state.day.drivers.values():
        if (d.id in worked or d.id in state.down_drivers
                or not may_depart(d, state.day.meta.get("moment"))
                or d.park_id != duty.park_id or any(c not in d.classes for c in need)):
            continue
        status = rest_status(state.history.peek(d.id), base + start, labor)
        if status is not None:
            out.append((status == "reduced", d.tab_number, d.id))
    return [d for *_, d in sorted(out)]


def _classes(state: OpsState, duty) -> tuple:
    if duty.type == "line":
        return state.day.routes[duty.route_id].allowed_classes
    return (duty.vehicle_class,)


def _day_fits(state: OpsState, driver_id: str, start: int, end: int, labor: dict) -> bool:
    """Рабочий день водителя вместе с новым отрезком не длиннее дневной нормы."""
    worked = state.driver_worked(driver_id)
    first = min([s.start for s in worked] + [start])
    last = max([s.end for s in worked] + [end])
    return work_minutes(last - first, labor) <= shift_limit(labor)


def _replacement_options(state: OpsState, duty_id: str, t: int, labor: dict,
                         back: int | None = None) -> list:
    """Варианты вернуть автобус на наряд duty_id, который потерял его в момент t.

    back - через сколько минут вернётся свой автобус (None - не вернётся).
    Замена нужна только до его возвращения. Водитель замены работает до
    конца текущей смены, дальше наряд по плану принимает следующая смена.
    """
    day = state.day
    duty = day.duties[duty_id]
    if duty.end <= t:
        return []
    losses = Losses(state)
    shift = state.shift_at(duty_id, t)
    shift_id = shift.id if shift else None
    gap_end = duty.end if back is None else min(duty.end, t + back)
    gap_end = _free_until(state.vehicles, duty_id, t, gap_end)
    drive_end = min(shift.end, gap_end) if shift else gap_end
    if shift_id:
        drive_end = _free_until(state.drivers, shift_id, t, drive_end)
    options = [Option("none", "Не заменять", losses.cost(duty_id, t, gap_end), gap_end - t, [],
                      "маршрут работает без этого автобуса" +
                      ("" if back is None else f", автобус вернётся через {back} мин"))]
    classes = _classes(state, duty)
    arrive = t + SUPPLY_MIN

    # 1. Резервный наряд: автобус и водитель резерва едут на линию.
    for reserve in day.duties.values() if arrive < gap_end else ():
        if reserve.type != "reserve" or reserve.park_id != duty.park_id or not reserve.start <= t < reserve.end:
            continue
        rv = state.vehicle_at(reserve.id, t)
        if rv is None or rv in state.down_vehicles or day.vehicles[rv].cls not in classes:
            continue
        if _busy_until(state, "vehicle", rv, arrive, gap_end, (reserve.id,)) < gap_end:
            continue  # резервный автобус уже обещан другому наряду
        rv_cls = day.vehicles[rv].cls
        home = gap_end + SUPPLY_MIN  # когда автобус вернётся в резерв
        changes = [("vehicle_end", reserve.id, t), ("vehicle", duty_id, Segment(arrive, gap_end, rv)),
                   ("vehicle", reserve.id, Segment(home, reserve.end, rv))]
        r_shift = state.shift_at(reserve.id, t)
        rd = state.driver_at(r_shift.id, t) if r_shift else None
        if (rd is not None and rd not in state.down_drivers
                and rv_cls in day.drivers[rd].classes
                and _day_fits(state, rd, arrive, drive_end, labor)
                and _busy_until(state, "driver", rd, arrive, drive_end, (r_shift.id,)) >= drive_end):
            back_to_reserve = _busy_until(state, "driver", rd, drive_end + SUPPLY_MIN,
                                          r_shift.end, (r_shift.id,))
            changes += [("driver_end", r_shift.id, t), ("driver", shift_id, Segment(arrive, drive_end, rd)),
                        ("driver", r_shift.id, Segment(drive_end + SUPPLY_MIN, back_to_reserve, rd))]
            who = f"водитель резерва {day.drivers[rd].tab_number}"
        else:
            free = free_drivers(state, duty, arrive, drive_end, labor, rv_cls)
            if not free:
                continue
            changes.append(("driver", shift_id, Segment(arrive, drive_end, free[0])))
            who = f"свободный водитель {day.drivers[free[0]].tab_number}"
        cost = losses.cost(duty_id, t, arrive) + RESERVE_COST_PER_HOUR * (min(home, reserve.end) - t) / 60
        options.append(Option(
            "reserve", f"Резерв: автобус {day.vehicles[rv].board_number}", cost, SUPPLY_MIN, changes,
            f"подача из парка ~{SUPPLY_MIN} мин, {who}; " +
            (f"резервный наряд {reserve.id} пустеет" if home >= reserve.end
             else f"автобус вернётся в резерв к {_hm(home)}")))
        break  # резервы равноценны: достаточно первого подходящего

    # 2. Исправный автобус без наряда + свободный водитель.
    idle = [v for v in sorted(day.vehicles.values(), key=lambda v: v.board_number)
            if v.condition == "ok" and v.cls in classes
            and state.transfers.get(v.id, v.park_id) == duty.park_id  # отданный в другой парк - там и работает
            and v.id not in state.down_vehicles and not state.vehicle_busy_after(v.id, t)]
    v = idle[0] if idle else None
    free = (free_drivers(state, duty, arrive, drive_end, labor, v.cls)
            if v is not None and arrive < gap_end else [])
    if free:
        options.append(Option(
            "idle", f"Автобус из парка {v.board_number} + водитель {day.drivers[free[0]].tab_number}",
            losses.cost(duty_id, t, arrive), SUPPLY_MIN,
            [("vehicle", duty_id, Segment(arrive, gap_end, v.id)),
             ("driver", shift_id, Segment(arrive, drive_end, free[0]))],
            f"подача из парка ~{SUPPLY_MIN} мин, резерв не тратится"))

    # 3. Снять автобус с другого маршрута (где его потеря дешевле).
    move_in = t + TRANSFER_MIN
    best = None
    for donor in day.duties.values() if shift and move_in < gap_end else ():
        if (donor.type != "line" or donor.id == duty_id or donor.park_id != duty.park_id
                or donor.route_id == duty.route_id or not donor.start <= t < donor.end):
            continue
        dv = state.vehicle_at(donor.id, t)
        if dv is None or dv in state.down_vehicles or day.vehicles[dv].cls not in classes:
            continue
        d_shift = state.shift_at(donor.id, t)
        dd = state.driver_at(d_shift.id, t) if d_shift else None
        if dd is None or day.vehicles[dv].cls not in day.drivers[dd].classes:
            continue
        if _busy_until(state, "vehicle", dv, move_in, gap_end, (donor.id,)) < gap_end:
            continue  # автобус донора уже обещан другому наряду
        d_end = min(drive_end, d_shift.end)            # водитель донора - до конца своей смены
        d_end = min(d_end, _busy_until(state, "driver", dd, move_in, d_end, (d_shift.id,)))
        home = min(donor.end, gap_end + TRANSFER_MIN)  # когда автобус вернётся на свой маршрут
        home = max(home, move_in)
        if d_end <= move_in:
            continue
        cost = (losses.cost(duty_id, t, move_in) + losses.cost(duty_id, d_end, drive_end)
                + losses.cost(donor.id, t, home, remove=True))
        if best is None or cost < best.cost:
            route = day.routes[donor.route_id]
            lost_donor = home - t
            best = Option(
                "donor", f"Снять автобус {day.vehicles[dv].board_number} с маршрута {route.number}",
                cost, (move_in - t) + (drive_end - d_end) + lost_donor,
                [("vehicle_end", donor.id, t), ("driver_end", d_shift.id, t),
                 ("vehicle", duty_id, Segment(move_in, gap_end, dv)),
                 ("driver", shift.id, Segment(move_in, d_end, dd)),
                 ("vehicle", donor.id, Segment(home, donor.end, dv)),
                 ("driver", d_shift.id, Segment(home, d_shift.end, dd))],
                f"перегон ~{TRANSFER_MIN} мин; маршрут {route.number} (важность {route.priority}) "
                f"теряет {lost_donor // 60} ч {lost_donor % 60:02d} мин" +
                ("" if home >= donor.end else f", автобус вернётся на него к {_hm(home)}"))
    if best is not None and best.cost < options[0].cost:
        options.append(best)

    return sorted(options, key=lambda o: (o.cost, o.kind))


# --- обработка событий ----------------------------------------------------------

def options_for(state: OpsState, event, labor: dict | None = None, limit: int | None = 3) -> list:
    """Варианты реакции на событие, от лучшего к худшему (по умолчанию до трёх).

    Вариант «не заменять» остаётся в ответе всегда, даже если он дороже
    остальных и не попал в лучшие: диспетчеру нужна точка отсчёта, с чем
    сравнивать, и docs/CONTRACT.md обещает его в ответе.
    """
    labor = labor or load_labor()
    everything = _all_options(state, event, labor)
    if limit is None:
        return everything
    best = everything[:limit]
    if not any(o.kind == "none" for o in best):
        nothing = next((o for o in everything if o.kind == "none"), None)
        if nothing is not None:
            best = best + [nothing]
    return best


def _all_options(state: OpsState, event, labor: dict) -> list:
    if isinstance(event, (Breakdown, Accident)):
        duty_id = state.duty_of_vehicle(event.vehicle_id, event.at)
        if duty_id is None:
            return []
        trial = _cut(state, event)
        back = event.duration if isinstance(event, Breakdown) else None
        return _replacement_options(trial, duty_id, event.at, labor, back)
    if isinstance(event, NoShow):
        return _driver_options(state, event, labor)
    raise TypeError(f"Неизвестное событие {event!r}")


def _driver_options(state: OpsState, event: NoShow, labor: dict) -> list:
    day = state.day
    shift_id = next((sid for sid, segs in state.drivers.items()
                     if any(s.who == event.driver_id and s.end > event.at for s in segs)), None)
    if shift_id is None:
        return []
    shift = day.shifts[shift_id]
    duty = day.duties[shift.duty_id]
    from_t = max(shift.start, event.at)
    trial = _cut(state, event)
    losses = Losses(trial)
    arrive = max(from_t, event.at + SUPPLY_MIN) if event.at > shift.start else from_t
    until = _free_until(trial.drivers, shift_id, event.at, shift.end)
    bus = trial.vehicle_at(duty.id, arrive)
    cls = day.vehicles[bus].cls if bus else None
    options = [Option("none", "Не заменять", losses.cost(duty.id, from_t, shift.end),
                      shift.end - from_t, [], "автобус стоит без водителя до пересменки")]
    free = free_drivers(trial, duty, arrive, until, labor, cls) if arrive < until else []
    if free:
        options.append(Option("free_driver", f"Водитель {day.drivers[free[0]].tab_number}",
                              losses.cost(duty.id, from_t, arrive), arrive - from_t,
                              [("driver", shift_id, Segment(arrive, until, free[0]))],
                              "свободный водитель, отдых после вчерашней смены соблюдён"))
    for reserve in day.duties.values():
        if reserve.type != "reserve" or reserve.park_id != duty.park_id:
            continue
        r_shift = trial.shift_at(reserve.id, arrive)
        rd = trial.driver_at(r_shift.id, arrive) if r_shift else None
        if rd is None or rd in trial.down_drivers or (cls or duty.vehicle_class) not in day.drivers[rd].classes:
            continue
        if not _day_fits(trial, rd, arrive, until, labor) or arrive >= until:
            continue
        if _busy_until(trial, "driver", rd, arrive, until, (r_shift.id,)) < until:
            continue  # резервный водитель уже обещан другому наряду
        options.append(Option(
            "reserve_driver", f"Водитель резерва {day.drivers[rd].tab_number}",
            losses.cost(duty.id, from_t, arrive) + RESERVE_COST_PER_HOUR * (r_shift.end - arrive) / 60,
            arrive - from_t,
            [("driver_end", r_shift.id, arrive), ("driver", shift_id, Segment(arrive, until, rd))],
            f"резервный наряд {reserve.id} остаётся без водителя"))
        break
    return sorted(options, key=lambda o: (o.cost, o.kind))


def _cut(state: OpsState, event) -> OpsState:
    """Копия состояния, где выбывшие с момента события убраны с отрезков.

    Сход на время: автобус и его водитель возвращаются на наряд через
    duration минут, если наряд к тому времени не кончился. Иначе автобус
    выбывает до конца дня.
    """
    out = copy.deepcopy(state)
    if isinstance(event, (Breakdown, Accident)):
        duty_id = out.duty_of_vehicle(event.vehicle_id, event.at)
        if duty_id is None:
            out.down_vehicles[event.vehicle_id] = event.at
            return out
        duty = out.day.duties[duty_id]
        shift = out.shift_at(duty_id, event.at)
        driver = out.driver_at(shift.id, event.at) if shift else None
        _end(out.vehicles, duty_id, event.at, event.vehicle_id)
        if driver:
            _end(out.drivers, shift.id, event.at, driver)
        back = event.at + event.duration if isinstance(event, Breakdown) and event.duration is not None else None
        if back is not None and back < duty.end:
            # автобус стоит с event.at до back: снимаем его занятость в этом окне везде
            # и запоминаем окно, чтобы ни движок, ни диспетчер не поставили его туда
            out.repairs.setdefault(event.vehicle_id, []).append((event.at, back))
            _hold(out.vehicles, event.vehicle_id, event.at, back)
            end = min(duty.end, _busy_until(out, "vehicle", event.vehicle_id, back, duty.end, (duty_id,)))
            end = _free_until(out.vehicles, duty_id, back - 1, end)
            if end > back:
                out.vehicles[duty_id].append(Segment(back, end, event.vehicle_id))
            if driver and back < shift.end:
                _hold(out.drivers, driver, event.at, back)
                d_end = min(shift.end, _busy_until(out, "driver", driver, back, shift.end, (shift.id,)))
                d_end = _free_until(out.drivers, shift.id, back - 1, d_end)
                if d_end > back:
                    out.drivers[shift.id].append(Segment(back, d_end, driver))
        else:
            out.down_vehicles[event.vehicle_id] = event.at
            _hold(out.vehicles, event.vehicle_id, event.at, None)  # выбыл: снять со всех нарядов
        if isinstance(event, Accident) and driver:
            out.down_drivers[driver] = event.at
            _hold(out.drivers, driver, event.at, None)
    elif isinstance(event, NoShow):
        out.down_drivers[event.driver_id] = event.at
        for shift_id in list(out.drivers):
            _end(out.drivers, shift_id, event.at, event.driver_id)
    return out


def _hold(table: dict, who: str, start: int, end: int | None) -> None:
    """Убрать занятость ресурса who в окне [start, end) на всех нарядах и сменах.

    end=None - до конца дня (ресурс выбыл). Отрезок, который начался до
    start, обрезается по start; часть после end остаётся как отдельный
    отрезок: автобус может вернуться к своему следующему наряду.
    """
    for key, segments in table.items():
        kept = []
        for segment in segments:
            if segment.who != who:
                kept.append(segment)
                continue
            if segment.start < start:
                kept.append(Segment(segment.start, min(segment.end, start), segment.who))
            if end is not None and segment.end > end:
                kept.append(Segment(max(segment.start, end), segment.end, segment.who))
        table[key] = [s for s in kept if s.end > s.start]


# --- ручная правка плана диспетчером -------------------------------------------

EDIT_TYPES = ("set_vehicle", "clear_vehicle", "set_driver", "clear_driver")


@dataclass(frozen=True)
class Edit:
    """Ручная правка: поставить или снять ресурс на наряде (смене) в окне времени.

    key - наряд для автобуса, смена для водителя. who - кого ставим, при
    снятии None. Окно задаёт диспетчер; по умолчанию это всё время наряда
    или смены.
    """

    type: str
    key: str
    who: str | None
    start: int
    end: int
    title: str = "Правка диспетчера"


def _clear_window(table: dict, key: str, start: int, end: int) -> None:
    """Убрать с наряда (смены) key всё, что стоит в окне [start, end).

    Отрезок, который начался раньше окна, обрезается по его началу;
    хвост после окна остаётся отдельным отрезком. Так правка на середину
    наряда не стирает то, что стоит до и после неё.
    """
    kept = []
    for segment in table.get(key, []):
        if segment.start < start:
            kept.append(Segment(segment.start, min(segment.end, start), segment.who))
        if segment.end > end:
            kept.append(Segment(max(segment.start, end), segment.end, segment.who))
    table[key] = [s for s in kept if s.end > s.start]


def manual_edit(state: OpsState, edit: Edit) -> OpsState:
    """Новое состояние дня после ручной правки.

    Законность здесь не проверяется: это дело check_state. Так диспетчер
    видит полный список того, что он нарушает, и решает сам, а движок не
    отказывает молча.
    """
    out = copy.deepcopy(state)
    table = out.vehicles if edit.type.endswith("vehicle") else out.drivers
    _clear_window(table, edit.key, edit.start, edit.end)
    if edit.type.startswith("set") and edit.who and edit.end > edit.start:
        table.setdefault(edit.key, []).append(Segment(edit.start, edit.end, edit.who))
        table[edit.key].sort(key=lambda s: s.start)
    out.log.append(f"{_hm(edit.start)} {edit.title}")
    return out


def free_vehicles(state: OpsState, duty, start: int, end: int) -> list:
    """Автобусы, которые законно можно поставить на наряд с start до end.

    Исправны, не выбыли, не в ремонте после схода на время, подходящего
    класса, из парка наряда (или переброшены в него утром), свободны на
    всём окне, и к их классу допущены водители, которые уже стоят на
    сменах наряда в этом окне (зеркало правила driver_permit в
    check_state). Пара к free_drivers.
    """
    allowed = _classes(state, duty)
    permits = [set(state.day.drivers[s.who].classes)
               for shift in state.day.shifts_by_duty.get(duty.id, [])
               for s in state.drivers.get(shift.id, []) if s.start < end and start < s.end]
    out = []
    for vehicle in state.day.vehicles.values():
        if (vehicle.condition != "ok" or vehicle.id in state.down_vehicles
                or vehicle.cls not in allowed or any(vehicle.cls not in p for p in permits)):
            continue
        if state.transfers.get(vehicle.id, vehicle.park_id) != duty.park_id:
            continue
        if state.in_repair(vehicle.id, start, end) is not None:
            continue
        if _busy_until(state, "vehicle", vehicle.id, start, end) < end:
            continue
        out.append(vehicle.id)
    return sorted(out)


def history_after(state: OpsState, labor: dict | None = None) -> History:
    """Память водителей по фактически отработанным отрезкам дня.

    Утренний план помнит то, что назначил движок, а внутри дня людей
    меняют: кто-то не вышел, кого-то сняли с резерва, кто-то вышел вместо
    него. Плану на следующий день нужна фактическая картина, иначе отдых
    посчитается не по той смене. Рабочий день водителя берётся целиком,
    от начала первого отрезка до конца последнего.
    """
    labor = labor or load_labor()
    out = copy.deepcopy(state.history)
    base, month = day_base(state.day), state.day.meta["date"][:7]
    worked: dict = {}
    for segments in state.drivers.values():
        for segment in segments:
            start, end = worked.get(segment.who, (segment.start, segment.end))
            worked[segment.who] = (min(start, segment.start), max(end, segment.end))
    for driver_id, (start, end) in sorted(worked.items(), key=lambda item: (item[1][0], item[0])):
        remember(out.get(driver_id), base + start, base + end, month, labor)
    return out


def _end(table: dict, key: str, t: int, who: str | None = None) -> None:
    """Обрезать отрезки ключа по моменту t (будущие - убрать)."""
    kept = []
    for s in table.get(key, []):
        if who is not None and s.who != who:
            kept.append(s)
        elif s.start < t:
            kept.append(Segment(s.start, min(s.end, t), s.who))
    table[key] = kept


def apply(state: OpsState, event, option: Option) -> OpsState:
    """Новое состояние дня после события и выбранного варианта."""
    out = _cut(state, event)
    for kind, key, value in option.changes:
        if key is None:
            continue
        if kind == "vehicle_end":
            _end(out.vehicles, key, value)
        elif kind == "driver_end":
            _end(out.drivers, key, value)
        elif kind == "vehicle":
            if value.who and value.end > value.start:
                out.vehicles.setdefault(key, []).append(value)
        elif kind == "driver":
            if value.end > value.start:
                out.drivers.setdefault(key, []).append(value)
    out.log.append(f"{_hm(event.at)} {event.title}: {option.title}")
    return out


def _hm(t: int) -> str:
    return f"{t // 60:02d}:{t % 60:02d}"


# --- проверка состояния ---------------------------------------------------------

def check_state(state: OpsState, labor: dict | None = None) -> list:
    """Нарушения в состоянии дня. Пустой список - состояние допустимо.

    Возвращает такие же объекты Violation (код, текст, id), как утренняя
    проверка naryad.core.invariants.check_plan, и там, где правило то же,
    использует тот же код: интерфейс и API разбирают оба списка одинаково.
    Своё правило: автобус не стоит на наряде в окне ремонта после схода на
    время (in_repair) - до возвращения он в парке, а не на линии.

    Четырёх правил утренней проверки здесь нет, и это осознанно:
    - лимит выпуска парка и состояние парка внутри дня проверять нечему:
      варианты замены только переставляют автобусы между нарядами, которые
      уже выпущены утром;
    - «одна смена на водителя в день» внутри дня не действует: водитель
      резерва законно появляется и на своём резервном наряде, и на линии,
      это один рабочий день, разбитый на две записи. Его длину проверяет
      правило driver_overtime;
    - «водитель только на наряде с автобусом» запретило бы законный вариант
      «не заменять», когда автобус сошёл, а водитель ждёт пересменки.
    """
    labor = labor or load_labor()
    day, out = state.day, []

    def bad(code: str, text: str, *ids) -> None:
        out.append(Violation(code, text, tuple(i for i in ids if i)))

    for table, place, of, bounds in ((state.vehicles, "наряд", "наряда", day.duties),
                                     (state.drivers, "смена", "смены", day.shifts)):
        for key, segments in table.items():
            items = sorted((s.start, s.end, s.who) for s in segments)
            for start, end, who in items:
                if not bounds[key].start <= start < end <= bounds[key].end:
                    bad("bad_segment", f"{place} {key}: отрезок {who} {_hm(start)}-{_hm(end)} "
                                       f"пустой или вне времени {of}", key, who)
            for a, b in zip(items, items[1:]):
                if b[0] < a[1]:
                    bad("segment_overlap", f"{place} {key}: в {_hm(b[0])} сразу {a[2]} и {b[2]}",
                        key, a[2], b[2])

    for duty_id, segments in state.vehicles.items():
        duty = day.duties[duty_id]
        for seg in segments:
            vehicle = day.vehicles[seg.who]
            if vehicle.cls not in _classes(state, duty):
                bad("vehicle_class", f"наряд {duty_id}: автобус {seg.who} класса {vehicle.cls} "
                                     f"не подходит", duty_id, seg.who)
            today = state.transfers.get(vehicle.id, vehicle.park_id)  # где автобус сегодня
            if today != duty.park_id:
                where = (f"из парка {vehicle.park_id} без переброски" if today == vehicle.park_id
                         else f"сегодня переброшен в парк {today}")
                bad("vehicle_park", f"наряд {duty_id}: автобус {seg.who} {where}", duty_id, seg.who)
            window = state.in_repair(seg.who, seg.start, seg.end)
            if window is not None:
                bad("in_repair", f"наряд {duty_id}: автобус {seg.who} в ремонте с {_hm(window[0])} "
                                 f"до {_hm(window[1])}, но стоит на наряде в это время", duty_id, seg.who)

    for shift_id, segments in state.drivers.items():
        duty = day.duties[day.shifts[shift_id].duty_id]
        for seg in segments:
            driver = day.drivers[seg.who]
            if driver.park_id != duty.park_id:
                bad("driver_park", f"смена {shift_id}: водитель {seg.who} из парка "
                                   f"{driver.park_id}", shift_id, seg.who)
            if driver.schedule != "work":
                bad("driver_not_working", f"смена {shift_id}: водитель {seg.who} не работает сегодня "
                                          f"({driver.schedule})", shift_id, seg.who)
            if driver.schedule == "work" and not may_depart(driver, day.meta.get("moment")):
                bad("driver_medical", f"смена {shift_id}: водитель {seg.who} не допущен "
                                      f"по медосмотру ({driver.medical})", shift_id, seg.who)
            for bus in state.vehicles.get(duty.id, []):
                if bus.start < seg.end and seg.start < bus.end and day.vehicles[bus.who].cls not in driver.classes:
                    bad("driver_permit", f"смена {shift_id}: у водителя {seg.who} нет допуска "
                                         f"к автобусу {bus.who}", shift_id, seg.who, bus.who)

    busy = {}
    for table, kind in ((state.vehicles, "автобус"), (state.drivers, "водитель")):
        for key, segments in table.items():
            for s in segments:
                busy.setdefault((kind, s.who), []).append((s.start, s.end, key))
    for (kind, who), items in busy.items():
        items.sort()
        for a, b in zip(items, items[1:]):
            if b[0] < a[1]:
                bad("vehicle_twice" if kind == "автобус" else "driver_overlap",
                    f"{kind} {who} одновременно на {a[2]} и {b[2]}", who, a[2], b[2])
        down = (state.down_vehicles if kind == "автобус" else state.down_drivers).get(who)
        if down is not None and any(end > down for _, end, _ in items):
            bad("down_but_working", f"{kind} {who} выбыл в {_hm(down)}, но стоит после этого", who)
        if kind == "автобус" and day.vehicles[who].condition != "ok":
            bad("vehicle_broken", f"автобус {who} неисправен с утра", who)
        if kind == "водитель":
            start, end = min(i[0] for i in items), max(i[1] for i in items)
            if work_minutes(end - start, labor) > shift_limit(labor):
                bad("driver_overtime", f"водитель {who}: рабочий день {end - start} мин "
                                       f"больше нормы", who)
            if rest_status(state.history.peek(who), day_base(day) + start, labor) is None:
                bad("daily_rest", f"водитель {who}: не отдохнул после прошлой смены", who)
    return out
