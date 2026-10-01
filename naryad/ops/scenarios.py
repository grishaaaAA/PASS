"""
Пять сценариев дня для демо и проверки (А5).

Запуск: python -m naryad.ops.scenarios [файл дня]

Каждый сценарий начинается с утреннего плана (А3 + А4), подаёт события
и применяет лучший вариант. Печатает, что предложила система, сколько
времени ушло на ответ и чего удалось избежать по сравнению с «не
заменять».
"""

from __future__ import annotations

import argparse
import random
import time
from pathlib import Path

from naryad.core.model import Day
from naryad.solve.drivers import solve_drivers
from naryad.solve.vehicles import solve_vehicles

from .replan import Accident, Breakdown, NoShow, OpsState, apply, check_state, options_for

DEFAULT_DAY = Path(__file__).resolve().parents[2] / "data" / "samples" / "park7_weekday.json"


def _line_duty(state: OpsState, t: int, priority: int, skip: int = 0):
    day = state.day
    duties = [d for d in sorted(day.duties.values(), key=lambda d: d.id)
              if d.type == "line" and day.routes[d.route_id].priority == priority
              and d.start < t < d.end - 60 and state.vehicle_at(d.id, t)]
    return duties[skip]


def build(day: Day) -> dict:
    """Сценарий -> список событий. События строятся по утреннему плану дня."""
    state = OpsState.from_plan(day, solve_drivers(day, solve_vehicles(day)))
    morning = 8 * 60 + 40
    duty = _line_duty(state, morning, 1)
    evening = 17 * 60 + 30
    crash = _line_duty(state, evening, 1, skip=3)
    early = next(s for s in sorted(day.shifts.values(), key=lambda s: (s.start, s.id))
                 if state.driver_at(s.id, s.start) and s.start >= 5 * 60)
    frost = 6 * 60 + 30
    rng = random.Random(1)
    running = sorted({state.vehicle_at(d.id, frost) for d in day.duties.values()
                      if d.type == "line" and d.start <= frost < d.end} - {None})
    return {
        "Сход утром на важном маршруте": [Breakdown(state.vehicle_at(duty.id, morning), morning)],
        "Сход на 20 минут (ремонт на линии)": [Breakdown(state.vehicle_at(duty.id, morning), morning, 20)],
        "ДТП вечером": [Accident(state.vehicle_at(crash.id, evening), evening)],
        "Недопуск водителя на медосмотре": [NoShow(state.driver_at(early.id, early.start),
                                                   early.start - 20)],
        "Мороз: 15 автобусов не вышли к 06:30": [Breakdown(v, frost) for v in rng.sample(running, 15)],
    }


def run(day: Day, events: list) -> dict:
    state = OpsState.from_plan(day, solve_drivers(day, solve_vehicles(day)))
    before = {k: [(s.start, s.end, s.who) for s in v] for k, v in state.vehicles.items()}
    seconds, avoided, lines = 0.0, 0, []
    for event in events:
        start = time.perf_counter()
        options = options_for(state, event, limit=None)
        seconds = max(seconds, time.perf_counter() - start)
        if not options:
            continue
        best = options[0]
        nothing = next(o for o in options if o.kind == "none")
        avoided += max(0, nothing.lost_minutes - best.lost_minutes)
        lines.append((event, options[:3]))
        state = apply(state, event, best)
        problems = check_state(state)
        if problems:
            raise AssertionError(problems[0])
    changed = sum(1 for k, v in state.vehicles.items()
                  if [(s.start, s.end, s.who) for s in v] != before.get(k))
    return {"state": state, "options": lines, "max_seconds": seconds,
            "avoided_minutes": avoided, "changed_duties": changed, "duties": len(before)}


def _hm(t: int) -> str:
    return f"{t // 60:02d}:{t % 60:02d}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("day", nargs="?", default=str(DEFAULT_DAY))
    args = parser.parse_args(argv)
    day = Day.load(args.day)
    for name, events in build(day).items():
        result = run(day, events)
        print(f"\n=== {name} ===")
        for event, options in result["options"][:3]:
            print(f"{_hm(event.at)} {event.title}")
            for i, option in enumerate(options, 1):
                print(f"   {i}. {option.title} (цена {option.cost:.1f}) - {option.note}")
        if len(result["options"]) > 3:
            print(f"   ... и ещё {len(result['options']) - 3} событий")
        print(f"Ответ системы: не дольше {result['max_seconds']:.2f} с на событие. "
              f"Изменено нарядов: {result['changed_duties']} из {result['duties']}. "
              f"Сохранено работы на линии против «не заменять»: "
              f"{result['avoided_minutes'] // 60} ч {result['avoided_minutes'] % 60:02d} мин")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
