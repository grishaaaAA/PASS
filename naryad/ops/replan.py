"""
Оперативный пересчёт внутри дня (А5): сход, ДТП, неявка водителя.

Утренний план говорит, кто на каком наряде весь день. Внутри дня этого
мало: в 08:40 автобус сошёл, с 09:10 на наряде резервный. Поэтому день
описывается отрезками: «наряд D, с 05:25 до 08:40 - автобус 7023,
с 09:10 - автобус 7105». Так же для водителей на сменах.

На событие система строит варианты замены, считает цену каждого и
отдаёт до трёх лучших разных видов. Диспетчер выбирает, вариант
применяется к состоянию дня.

Цена варианта - те же единицы, что в А3: потерянные часы на линии с
весом важности маршрута и штрафом за рост интервала. Плюс небольшая
цена за израсходованный резерв: он нужен на следующий сход.

Допущения (уточнить у перевозчика; пока координат парков и конечных
нет, время подачи - постоянное):
- подача автобуса из парка на маршрут - SUPPLY_MIN минут;
- перегон автобуса с одного маршрута на другой - TRANSFER_MIN минут;
- водитель сошедшего автобуса остаётся с ним; на замену едет другой
  водитель; на пересменке водители наряда принимают новый автобус по плану.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from naryad.core.invariants import load_labor, shift_limit
from naryad.core.model import Day, Plan
from naryad.solve.drivers import History, day_base, rest_status
from naryad.solve.vehicles import GROWTH_POWER, PRIORITY_WEIGHT

SUPPLY_MIN = 30
TRANSFER_MIN = 20
RESERVE_COST_PER_HOUR = 0.2  # цена часа израсходованного резерва
END = 10 ** 6                # «до конца дня»


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
    history: History = field(default_factory=History)  # прошлые дни водителей
    log: list = field(default_factory=list)

    @classmethod
    def from_plan(cls, day: Day, plan: Plan, history: History | None = None) -> "OpsState":
        state = cls(day=day, history=copy.deepcopy(history) if history else History())
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

    def driver_worked(self, driver_id: str) -> list:
        return [s for segs in self.drivers.values() for s in segs if s.who == driver_id]

    def shift_at(self, duty_id: str, t: int):
        return next((s for s in self.day.shifts_by_duty.get(duty_id, []) if s.start <= t < s.end), None)


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

def _route_cost(state: OpsState, duty_id: str, minutes_: int, t: int) -> float:
    """Цена потери minutes_ минут работы наряда: важность x часы x рост интервала."""
    duty = state.day.duties[duty_id]
    if duty.type == "reserve" or minutes_ <= 0:
        return RESERVE_COST_PER_HOUR * max(0, minutes_) / 60
    route_duties = [d for d in state.day.duties.values() if d.route_id == duty.route_id
                    and d.day_type == state.day.day_type]
    n = len(route_duties)
    running = sum(1 for d in route_duties if d.start <= t < d.end and state.vehicle_at(d.id, t))
    active = max(1, sum(1 for d in route_duties if d.start <= t < d.end))
    missing = active - running
    growth = (active / max(1, active - missing - 1)) ** GROWTH_POWER if n > 1 else 4.0
    weight = PRIORITY_WEIGHT[state.day.routes[duty.route_id].priority]
    return weight * minutes_ / 60 * growth


# --- кого можно поставить ----------------------------------------------------

def free_drivers(state: OpsState, duty, start: int, end: int, labor: dict) -> list:
    """Водители, которые законно могут работать на наряде с start до end.

    Не работали сегодня, работают по графику, медосмотр не провален, не
    выбыли, допуск к классу, отдых после вчерашней смены по нормам,
    смена не длиннее дневной нормы.
    """
    if end - start > shift_limit(labor):
        return []
    worked = {s.who for segs in state.drivers.values() for s in segs}
    base = day_base(state.day)
    cls = state.day.duties[duty.id].vehicle_class
    out = []
    for d in state.day.drivers.values():
        if (d.id in worked or d.id in state.down_drivers or d.schedule != "work"
                or d.medical == "failed" or d.park_id != duty.park_id or cls not in d.classes):
            continue
        status = rest_status(state.history.get(d.id), base + start, labor)
        if status is not None:
            out.append((status == "reduced", d.tab_number, d.id))
    return [d for *_, d in sorted(out)]


def _classes(state: OpsState, duty) -> tuple:
    if duty.type == "line":
        return state.day.routes[duty.route_id].allowed_classes
    return (duty.vehicle_class,)


def _replacement_options(state: OpsState, duty_id: str, t: int, labor: dict,
                         back: int | None = None) -> list:
    """Варианты вернуть автобус на наряд duty_id, который потерял его в момент t."""
    day = state.day
    duty = day.duties[duty_id]
    if duty.end <= t:
        return []
    shift = state.shift_at(duty_id, t)
    shift_end = shift.end if shift else duty.end
    gap_end = duty.end if back is None else min(duty.end, t + back)
    options = [Option("none", "Не заменять", _route_cost(state, duty_id, gap_end - t, t),
                      gap_end - t, [],
                      "маршрут работает без этого автобуса" +
                      ("" if back is None else f", автобус вернётся через {back} мин"))]
    classes = _classes(state, duty)
    arrive = t + SUPPLY_MIN

    # 1. Резервный наряд: автобус и водитель резерва едут на линию.
    for reserve in day.duties.values():
        if reserve.type != "reserve" or reserve.park_id != duty.park_id or not reserve.start <= t < reserve.end:
            continue
        rv = state.vehicle_at(reserve.id, t)
        if rv is None or rv in state.down_vehicles or day.vehicles[rv].cls not in classes:
            continue
        r_shift = state.shift_at(reserve.id, t)
        rd = state.driver_at(r_shift.id, t) if r_shift else None
        if rd is not None and rd not in state.down_drivers \
                and shift_end - r_shift.start <= shift_limit(labor):
            driver_change = [("driver_end", r_shift.id, t),
                             ("driver", shift.id if shift else None, Segment(arrive, shift_end, rd))]
            who = f"водитель резерва {day.drivers[rd].tab_number}"
        else:
            free = free_drivers(state, duty, arrive, shift_end, labor)
            if not free:
                continue
            driver_change = [("driver", shift.id if shift else None, Segment(arrive, shift_end, free[0]))]
            who = f"свободный водитель {day.drivers[free[0]].tab_number}"
        cost = (_route_cost(state, duty_id, SUPPLY_MIN, t)
                + RESERVE_COST_PER_HOUR * (reserve.end - t) / 60)
        options.append(Option(
            "reserve", f"Резерв: автобус {day.vehicles[rv].board_number}", cost, SUPPLY_MIN,
            [("vehicle_end", reserve.id, t), ("vehicle", duty_id, Segment(arrive, duty.end, rv))]
            + driver_change,
            f"подача из парка ~{SUPPLY_MIN} мин, {who}; резервный наряд {reserve.id} пустеет"))
        break  # резервы равноценны: достаточно первого подходящего

    # 2. Исправный автобус без наряда + свободный водитель.
    idle = [v for v in sorted(day.vehicles.values(), key=lambda v: v.board_number)
            if v.condition == "ok" and v.park_id == duty.park_id and v.cls in classes
            and v.id not in state.down_vehicles and not state.vehicle_busy_after(v.id, t)]
    free = free_drivers(state, duty, arrive, shift_end, labor)
    if idle and free:
        v = idle[0]
        options.append(Option(
            "idle", f"Автобус из парка {v.board_number} + водитель {day.drivers[free[0]].tab_number}",
            _route_cost(state, duty_id, SUPPLY_MIN, t), SUPPLY_MIN,
            [("vehicle", duty_id, Segment(arrive, duty.end, v.id)),
             ("driver", shift.id if shift else None, Segment(arrive, shift_end, free[0]))],
            f"подача из парка ~{SUPPLY_MIN} мин, резерв не тратится"))

    # 3. Снять автобус с другого маршрута (где его потеря дешевле).
    best = None
    for donor in day.duties.values():
        if (donor.type != "line" or donor.id == duty_id or donor.park_id != duty.park_id
                or donor.route_id == duty.route_id or not donor.start <= t < donor.end):
            continue
        dv = state.vehicle_at(donor.id, t)
        if dv is None or dv in state.down_vehicles or day.vehicles[dv].cls not in classes:
            continue
        d_shift = state.shift_at(donor.id, t)
        dd = state.driver_at(d_shift.id, t) if d_shift else None
        if dd is None or shift is None:
            continue
        lost_donor = donor.end - t
        uncovered = max(0, shift_end - d_shift.end)  # водитель донора закончит раньше смены
        cost = (_route_cost(state, duty_id, TRANSFER_MIN + uncovered, t)
                + _route_cost(state, donor.id, lost_donor, t))
        if best is None or cost < best.cost:
            route = day.routes[donor.route_id]
            best = Option(
                "donor", f"Снять автобус {day.vehicles[dv].board_number} с маршрута {route.number}",
                cost, TRANSFER_MIN + uncovered + lost_donor,
                [("vehicle_end", donor.id, t), ("driver_end", d_shift.id, t),
                 ("vehicle", duty_id, Segment(t + TRANSFER_MIN, duty.end, dv)),
                 ("driver", shift.id, Segment(t + TRANSFER_MIN, min(shift_end, d_shift.end), dd))],
                f"перегон ~{TRANSFER_MIN} мин; маршрут {route.number} (важность {route.priority}) "
                f"теряет {lost_donor // 60} ч {lost_donor % 60:02d} мин")
    if best is not None and best.cost < options[0].cost:
        options.append(best)

    return sorted(options, key=lambda o: (o.cost, o.kind))


# --- обработка событий ----------------------------------------------------------

def options_for(state: OpsState, event, labor: dict | None = None, limit: int | None = 3) -> list:
    """Варианты реакции на событие, от лучшего к худшему (по умолчанию до трёх)."""
    labor = labor or load_labor()
    return _all_options(state, event, labor)[:limit]


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
    arrive = max(from_t, event.at + SUPPLY_MIN) if event.at > shift.start else from_t
    options = [Option("none", "Не заменять", _route_cost(trial, duty.id, shift.end - from_t, from_t),
                      shift.end - from_t, [], "автобус стоит без водителя до пересменки")]
    free = free_drivers(trial, duty, arrive, shift.end, labor)
    if free:
        lost = arrive - from_t
        options.append(Option("free_driver", f"Водитель {day.drivers[free[0]].tab_number}",
                              _route_cost(trial, duty.id, lost, from_t), lost,
                              [("driver", shift_id, Segment(arrive, shift.end, free[0]))],
                              "свободный водитель, отдых после вчерашней смены соблюдён"))
    for reserve in day.duties.values():
        if reserve.type != "reserve" or reserve.park_id != duty.park_id:
            continue
        r_shift = trial.shift_at(reserve.id, arrive)
        rd = trial.driver_at(r_shift.id, arrive) if r_shift else None
        if rd is None or rd in trial.down_drivers or duty.vehicle_class not in day.drivers[rd].classes:
            continue
        if max(shift.end, r_shift.end) - min(arrive, r_shift.start) > shift_limit(labor):
            continue
        lost = arrive - from_t
        options.append(Option(
            "reserve_driver", f"Водитель резерва {day.drivers[rd].tab_number}",
            _route_cost(trial, duty.id, lost, from_t) + RESERVE_COST_PER_HOUR * (r_shift.end - arrive) / 60,
            lost, [("driver_end", r_shift.id, arrive), ("driver", shift_id, Segment(arrive, shift.end, rd))],
            f"резервный наряд {reserve.id} остаётся без водителя"))
        break
    return sorted(options, key=lambda o: (o.cost, o.kind))


def _cut(state: OpsState, event) -> OpsState:
    """Копия состояния, где выбывшие с момента события убраны с отрезков."""
    out = copy.deepcopy(state)
    if isinstance(event, (Breakdown, Accident)):
        out.down_vehicles[event.vehicle_id] = event.at
        duty_id = out.duty_of_vehicle(event.vehicle_id, event.at)
        _end(out.vehicles, duty_id, event.at, event.vehicle_id)
        if isinstance(event, Breakdown) and event.duration is not None:
            out.down_vehicles.pop(event.vehicle_id)
            out.vehicles[duty_id].append(Segment(event.at + event.duration,
                                                 out.day.duties[duty_id].end, event.vehicle_id))
        shift = out.shift_at(duty_id, event.at)
        if shift is not None:
            driver = out.driver_at(shift.id, event.at)
            if isinstance(event, Accident) and driver:
                out.down_drivers[driver] = event.at
            if driver and not (isinstance(event, Breakdown) and event.duration is not None):
                _end(out.drivers, shift.id, event.at, driver)
    elif isinstance(event, NoShow):
        out.down_drivers[event.driver_id] = event.at
        for shift_id in list(out.drivers):
            _end(out.drivers, shift_id, event.at, event.driver_id)
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
    """Нарушения в состоянии дня. Пустой список - состояние допустимо."""
    labor = labor or load_labor()
    day, out = state.day, []
    busy = {}
    for table, kind in ((state.vehicles, "автобус"), (state.drivers, "водитель")):
        for key, segments in table.items():
            for s in segments:
                busy.setdefault((kind, s.who), []).append((s.start, s.end, key))
    for (kind, who), items in busy.items():
        items.sort()
        for a, b in zip(items, items[1:]):
            if b[0] < a[1]:
                out.append(f"{kind} {who} одновременно на {a[2]} и {b[2]}")
        down = (state.down_vehicles if kind == "автобус" else state.down_drivers).get(who)
        if down is not None and any(end > down for _, end, _ in items):
            out.append(f"{kind} {who} выбыл в {_hm(down)}, но стоит после этого")
        if kind == "автобус" and day.vehicles[who].condition != "ok":
            out.append(f"автобус {who} неисправен с утра")
        if kind == "водитель":
            start, end = min(i[0] for i in items), max(i[1] for i in items)
            if end - start > shift_limit(labor):
                out.append(f"водитель {who}: рабочий день {end - start} мин больше нормы")
            if rest_status(state.history.get(who), day_base(day) + start, labor) is None:
                out.append(f"водитель {who}: не отдохнул после прошлой смены")
    return out
