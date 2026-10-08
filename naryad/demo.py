"""
Показ на один парк: план на день, событие, варианты замены, объяснения.

Запуск:
    python -m naryad.demo                                  # парк №7 из data/samples
    python -m naryad.demo --at 08:40 --no-show             # плюс недопуск водителя утром
    python -m naryad.demo --late 30                        # плюс опоздание водителя на 30 минут
    python -m naryad.demo --day data/samples/park7_weekday.json
    python -m naryad.demo --labor likely                   # на другом наборе норм

Сценарий идёт через API движка (naryad.web.api.Engine) - тот же, на котором
будет работать интерфейс: если показ прошёл, интерфейсу есть на чём работать.
День берётся из файла в data/samples, поэтому показ не зависит ни от
генератора, ни от сети, ни от порядка запуска.

Что видит заказчик по шагам: какой это день, утренний план и его проверка,
событие дня, до трёх вариантов замены с ценой, объяснение лучшего простыми
словами, план после замены и проверка законности, и напоследок ручная правка
плана диспетчером: движок подсказывает, кого можно поставить, не пропускает
нарушение молча и записывает решение человека в журнал.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from naryad.core.invariants import labor_preset_name, use_labor_preset
from naryad.core.model import Day
from naryad.data.presets import CLASS_LABELS
from naryad.web.api import ApiError, Engine, hm, parse_time

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "samples" / "park7_weekday.json"
WIDTH = 78


class DemoError(Exception):
    """Показ не удался: текст уже понятен человеку."""


def head(step: int, title: str) -> None:
    print(f"\nШаг {step}. {title}")
    print("-" * WIDTH)


def answer(payload: dict, indent: str = "  ") -> None:
    """Объяснение движка как есть: одна фраза и пункты под ней."""
    print(f"{indent}{payload['answer']}")
    for reason in payload.get("reasons", []):
        print(f"{indent}- {reason}")


class Demo:
    """Один прогон показа: держит номер дня в API и печатает шаги."""

    def __init__(self, path: Path):
        self.engine = Engine()
        self.raw = self._read(path)
        self.day = Day.from_dict(self.raw)
        self.day_id = ""
        self.step = 0

    @staticmethod
    def _read(path: Path) -> dict:
        import json
        if not path.is_file():
            raise DemoError(f"Нет файла с днём: {path}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise DemoError(f"{path}: неверный JSON ({error})") from None

    def call(self, method: str, path: str, body: dict | None = None,
             query: dict | None = None) -> dict:
        status, payload = self.engine.handle(method, path, query, body)
        if status != 200:
            raise DemoError(f"{method} {path}: {payload.get('error', payload)}")
        return payload

    def next_step(self, title: str) -> None:
        self.step += 1
        head(self.step, title)

    # --- шаги -------------------------------------------------------------------

    def load(self, path: Path) -> None:
        self.next_step("Что за день")
        loaded = self.call("POST", "/api/days", self.raw)
        self.day_id = loaded["day_id"]
        counts = loaded["counts"]
        park = next(iter(self.day.parks.values()))
        print(f"  {park.name}, {loaded['date']}, {'будни' if loaded['day_type'] == 'weekday' else 'выходной'}")
        print(f"  Маршрутов {counts['routes']}, нарядов {counts['duties']}, смен {counts['shifts']}")
        print(f"  Автобусов {counts['vehicles']}, водителей {counts['drivers']}")
        print(f"  Источник: {path}, нормы труда: {labor_preset_name()}")
        for warning in loaded["warnings"][:3]:
            print(f"  Предупреждение: {warning}")

    def plan(self) -> dict:
        self.next_step("Утренний план")
        planned = self.call("POST", f"/api/days/{self.day_id}/plan", {})
        print(f"  Посчитан за {planned['seconds']} с")
        answer(planned["summary"])
        violations = planned["violations"]
        print(f"  Проверка плана: нарушений {len(violations)}"
              + ("" if not violations else f", первое: {violations[0]['text']}"))
        return planned

    def no_show(self) -> None:
        """Недопуск водителя на медосмотре: самая частая утренняя неприятность."""
        self.next_step("Событие: водителя не допустил медосмотр")
        shift = min((s for s in self.day.shifts.values()
                     if self.day.duties[s.duty_id].type == "line" and s.start >= 5 * 60),
                    key=lambda s: (s.start, s.id), default=None)
        if shift is None:
            raise DemoError("в дне нет утренней смены на линии")
        state = self.engine.days[self.day_id].state
        driver_id = state.driver_at(shift.id, shift.start)
        if driver_id is None:
            raise DemoError(f"на смену {shift.id} водитель не поставлен")
        driver = self.day.drivers[driver_id]
        at = max(0, shift.start - 20)
        route = self.day.routes[self.day.duties[shift.duty_id].route_id]
        print(f"  {hm(at)}: водитель {driver.tab_number} не допущен, "
              f"смена {hm(shift.start)}-{hm(shift.end)} на маршруте {route.number}")
        self.event({"type": "no_show", "driver_id": driver_id, "at": hm(at)})

    def late(self, delay: int = 30) -> None:
        """Водитель опаздывает на утреннюю смену: самая частая замена в парке.

        Так, как описал директор парка: водитель должен выехать в 7:30 и
        опаздывает. Его можно ждать, а можно поменять сменами с тем, кто
        выходит чуть позже и уже пришёл.
        """
        self.next_step(f"Событие: водитель опаздывает на {delay} мин")
        state = self.engine.days[self.day_id].state
        shifts = [s for s in self.day.shifts.values()
                  if self.day.duties[s.duty_id].type == "line" and s.start >= 7 * 60 + 20
                  and state.driver_at(s.id, s.start)]
        if not shifts:
            raise DemoError("в дне нет утренней смены на линии с водителем")
        shift = min(shifts, key=lambda s: (s.start, s.id))
        driver = self.day.drivers[state.driver_at(shift.id, shift.start)]
        route = self.day.routes[self.day.duties[shift.duty_id].route_id]
        print(f"  {hm(shift.start)}: водитель {driver.tab_number} должен выехать на маршрут {route.number} "
              f"и опаздывает на {delay} мин")
        self.event({"type": "late", "driver_id": driver.id, "at": hm(shift.start), "delay_min": delay})

    def breakdown(self, at: int, duration: int | None) -> None:
        """Сход автобуса с важного маршрута: то, из-за чего срываются рейсы."""
        self.next_step("Событие: автобус сошёл с линии")
        state = self.engine.days[self.day_id].state
        duties = [d for d in self.day.duties.values()
                  if d.type == "line" and d.day_type == self.day.day_type
                  and d.start <= at < d.end and state.vehicle_at(d.id, at)]
        if not duties:
            raise DemoError(f"в {hm(at)} на линии нет нарядов: возьмите другое время (--at)")
        duty = min(duties, key=lambda d: (self.day.routes[d.route_id].priority, d.id))
        vehicle = self.day.vehicles[state.vehicle_at(duty.id, at)]
        route = self.day.routes[duty.route_id]
        print(f"  {hm(at)}: автобус {vehicle.board_number} "
              f"(класс {CLASS_LABELS.get(vehicle.cls, vehicle.cls)}) сошёл "
              f"с маршрута {route.number} (важность {route.priority}), наряд {duty.id}")
        print(f"  Наряд работает до {hm(duty.end)}"
              + (f", автобус вернётся через {duration} мин" if duration else ", автобус выбыл на день"))
        event = {"type": "breakdown", "vehicle_id": vehicle.id, "at": hm(at)}
        if duration:
            event["duration_min"] = duration
        self.event(event)

    def event(self, event: dict) -> None:
        """Варианты на событие, объяснение лучшего, применение и проверка."""
        before = self.call("GET", f"/api/days/{self.day_id}/state")
        options = self.call("POST", f"/api/days/{self.day_id}/events/options", event)["options"]
        if not options:
            raise DemoError("движок не дал ни одного варианта")
        self.next_step(f"Варианты замены: {len(options)}")
        print(f"  {'№':>2}  {'вид':15} {'что сделать':42} {'цена':>7}  {'простой':>9}")
        for option in options:
            print(f"  {option['index']:>2}  {option['kind']:15} {option['title'][:42]:42} "
                  f"{option['cost']:>7}  {option['lost_minutes']:>5} мин")
        print("\n  Лучший вариант и почему он лучше:")
        answer(options[0]["explanation"], indent="    ")

        self.next_step("Применяем лучший вариант")
        after = self.call("POST", f"/api/days/{self.day_id}/events/apply",
                          {"event": event, "option": 0})
        violations = after["violations"]
        print(f"  Проверка дня после замены: нарушений {len(violations)}"
              + ("" if not violations else f", первое: {violations[0]['text']}"))
        self.changes(before, after)
        self.intervals()

    def intervals(self) -> None:
        """Что стало с интервалами для пассажира: одна строка, зато главная."""
        answer = self.call("GET", f"/api/days/{self.day_id}/intervals")
        numbers = answer["numbers"]
        print(f"  Интервалы: {answer['answer'].lower()}; маршрутов с ростом больше четверти "
              f"{numbers['over_25_percent']} из {numbers['routes_total']}, "
              f"без единого автобуса {numbers['stopped_routes']}")

    def changes(self, before: dict, state: dict) -> None:
        """Что именно поменялось в дне: сравнение с состоянием до события.

        Считать изменённым то, где больше одного отрезка, нельзя: замена
        водителя, который не вышел до начала смены, даёт ровно один отрезок,
        и показ сообщал «изменено 0», хотя замена была.
        """
        was_duties = {d["duty_id"]: d["vehicles"] for d in before["duties"]}
        was_shifts = {s["shift_id"]: s["drivers"] for s in before["shifts"]}
        duties = [d for d in state["duties"] if d["vehicles"] != was_duties.get(d["duty_id"])]
        shifts = [s for s in state["shifts"] if s["drivers"] != was_shifts.get(s["shift_id"])]
        print(f"  Изменено нарядов: {len(duties)}, смен: {len(shifts)}; "
              f"остальные наряды без изменений: {len(state['duties']) - len(duties)}")
        for duty in duties[:3]:
            board = {v: self.day.vehicles[v].board_number for v in
                     (s["vehicle_id"] for s in duty["vehicles"]) if v in self.day.vehicles}
            parts = " | ".join(f"{s['from']}-{s['to']} автобус {board.get(s['vehicle_id'], '?')}"
                               for s in duty["vehicles"])
            print(f"    {duty['duty_id']}: {parts}")
        for shift in shifts[:3]:
            tabs = {d: self.day.drivers[d].tab_number for d in
                    (s["driver_id"] for s in shift["drivers"]) if d in self.day.drivers}
            parts = " | ".join(f"{s['from']}-{s['to']} водитель {tabs.get(s['driver_id'], '?')}"
                               for s in shift["drivers"])
            print(f"    {shift['shift_id']}: {parts}")
        for out in state["down_vehicles"]:
            print(f"    Выбыл автобус {self.day.vehicles[out['vehicle_id']].board_number} "
                  f"с {out['since']}")
        for out in state["down_drivers"]:
            print(f"    Выбыл водитель {self.day.drivers[out['driver_id']].tab_number} "
                  f"с {out['since']}")

    def dispatcher_edit(self) -> None:
        """Диспетчер не согласен с движком и правит план сам.

        Показывает три вещи подряд: движок подсказывает, кого законно можно
        поставить; правку против норм он не пропускает молча; с явным
        подтверждением пропускает и записывает в журнал.
        """
        state = self.call("GET", f"/api/days/{self.day_id}/state")
        # наряды, которых события не касались: один отрезок на весь наряд.
        # Иначе правка затёрла бы замену, только что показанную на шаге выше.
        quiet = [d for d in state["duties"] if len(d["vehicles"]) == 1]
        if len(quiet) < 2:
            return
        duty, donor = quiet[0]["duty_id"], quiet[1]
        self.next_step("Диспетчер правит план сам")

        free = self.call("GET", f"/api/days/{self.day_id}/edits/options", query={"duty_id": duty})
        boards = ", ".join(v["board_number"] for v in free["vehicles"][:5])
        print(f"  На наряд {duty} законно можно поставить автобусов: {free['total']}"
              + (f" ({boards}...)" if boards else ""))

        taken = donor["vehicles"][0]["vehicle_id"]
        refused = self.refuse("POST", f"/api/days/{self.day_id}/edits",
                              {"type": "set_vehicle", "duty_id": duty, "vehicle_id": taken})
        print(f"\n  Диспетчер ставит автобус {self.day.vehicles[taken].board_number}, "
              f"который уже занят на наряде {donor['duty_id']}:")
        print(f"    Движок: {refused['error']}")
        for violation in refused["violations"]:
            print(f"    - {violation['text']}")
        print("    Правка не применена, день не изменился. Движок не молчит и не решает за человека.")

        if not free["vehicles"]:
            return
        good = free["vehicles"][0]
        after = self.call("POST", f"/api/days/{self.day_id}/edits",
                          {"type": "set_vehicle", "duty_id": duty, "vehicle_id": good["vehicle_id"],
                           "reason": "решение диспетчера"})
        print(f"\n  Диспетчер ставит свободный автобус {good['board_number']}:")
        print(f"    Применено, новых нарушений: {len(after['new_violations'])}. "
              "Правка записана в журнал дня.")
        print("    Если бы диспетчер настоял на занятом автобусе, правка прошла бы с force,")
        print("    и в журнале осталось бы, что именно он нарушил и почему.")

    def refuse(self, method: str, path: str, body: dict) -> dict:
        """Запрос, который движок обязан отклонить. Ответ 200 здесь - ошибка показа."""
        status, payload = self.engine.handle(method, path, None, body)
        if status != 409:
            raise DemoError(f"{path}: ждали отказ 409, получили {status}")
        return payload

    def journal(self) -> None:
        self.next_step("Журнал дня")
        for row in self.call("GET", f"/api/days/{self.day_id}/log")["log"]:
            # у события есть время, у ручной правки - окно: журнал общий
            when = row["at"] if row.get("kind", "event") == "event" else "правка"
            mark = " (под ответственность диспетчера)" if row.get("forced") else ""
            print(f"  {when:>6}  {row['title']}{mark}")
        print("\n  Весь разбор занял доли секунды на каждое событие. "
              "Диспетчер видит те же цифры и те же объяснения.")


def run(path: Path, at: int, duration: int | None, with_no_show: bool, late: int | None = None) -> None:
    demo = Demo(path)
    print("=" * WIDTH)
    print("AUTODISP: нарядка автобусов и водителей, показ на одном парке".center(WIDTH))
    print("=" * WIDTH)
    demo.load(path)
    demo.plan()
    if with_no_show:
        demo.no_show()
    if late:
        demo.late(late)
    demo.breakdown(at, duration)
    demo.dispatcher_edit()
    demo.journal()
    print()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--day", default=str(SAMPLE), help="файл с днём (JSON)")
    parser.add_argument("--at", default="08:40", help="время схода, ЧЧ:ММ")
    parser.add_argument("--duration", type=int, default=None,
                        help="через сколько минут автобус вернётся; без него - выбыл на день")
    parser.add_argument("--no-show", action="store_true",
                        help="добавить шаг: водителя не допустил медосмотр утром")
    parser.add_argument("--late", type=int, default=None, metavar="МИН",
                        help="добавить шаг: водитель утренней смены опаздывает на столько минут")
    parser.add_argument("--labor", default=None,
                        help="набор норм из naryad/core/labor_presets.json: current, likely, strict")
    args = parser.parse_args(argv)
    try:
        use_labor_preset(args.labor)
        if args.late is not None and args.late <= 0:
            raise ValueError("--late: на сколько минут опаздывает водитель, больше нуля")
        run(Path(args.day), parse_time(args.at, "--at"), args.duration, args.no_show, args.late)
    except (ApiError, DemoError, ValueError) as error:
        print(f"Показ не удался: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
