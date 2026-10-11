"""
Доменная модель: день и план.

День - то, что пришло из генератора или реестра (формат docs/CONTRACT.md).
План - ответ движка: какой автобус на каком наряде, какой водитель на какой
смене и почему что-то осталось незакрытым.

День читается только после проверки данных Антона (naryad.data.check):
противоречивые данные в движок не пускаем.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from naryad.data.check import check


_LATIN = dict(zip("АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ",
                  ["A", "B", "V", "G", "D", "E", "E", "ZH", "Z", "I", "I", "K", "L", "M", "N", "O", "P",
                   "R", "S", "T", "U", "F", "H", "C", "CH", "SH", "SH", "", "Y", "", "E", "YU", "YA"]))


# Написания, которые транслитерация не сводит: по-русски «ВолгаБас» через «а»,
# «Мерседес-Бенц» целиком. Дополнять по живым реестрам.
MAKE_ALIASES = {"VOLGABAS": "VOLGABUS", "MERSEDES": "MERCEDES", "MERSEDESBENC": "MERCEDES",
                "MERCEDESBENZ": "MERCEDES", "LIAAZ": "LIAZ"}


@lru_cache(maxsize=None)
def normal_make(text) -> str:
    """Марка автобуса к сравнимому виду: «ЛиАЗ», «LIAZ», «лиаз» -> «LIAZ».

    В одной выгрузке марка бывает и по-русски, и латиницей: в допуске
    водителя «НЕФАЗ», в карточке автобуса «NEFAZ 5299». Поэтому сравниваем
    латиницей без пробелов и знаков.
    """
    upper = str(text or "").upper()
    latin = re.sub(r"[^A-Z0-9]", "", "".join(_LATIN.get(ch, ch) for ch in upper))
    return MAKE_ALIASES.get(latin, latin)


def make_of(vehicle: dict) -> str:
    """Марка автобуса: поле make, а если его нет - первое слово модели."""
    make = vehicle.get("make")
    if make:
        return str(make).strip()
    words = str(vehicle.get("model") or "").split()
    return words[0] if words else ""


def minutes(text: str) -> int:
    """'25:10' -> 1510. Часы после полуночи идут дальше 24."""
    hours, mins = text.split(":")
    return int(hours) * 60 + int(mins)


@dataclass(frozen=True)
class Park:
    id: str
    name: str
    state: str
    release_weekday: int
    release_weekend: int

    def release(self, day_type: str) -> int:
        return self.release_weekday if day_type == "weekday" else self.release_weekend


@dataclass(frozen=True)
class Route:
    id: str
    park_id: str
    number: str
    allowed_classes: tuple
    priority: int
    turnaround_min: int = 0  # время полного круга, для расчёта интервала


@dataclass(frozen=True)
class Duty:
    id: str
    park_id: str
    type: str
    route_id: str | None
    vehicle_class: str
    day_type: str
    start: int
    end: int
    shift_count: int


@dataclass(frozen=True)
class Shift:
    id: str
    duty_id: str
    order: int
    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start


@dataclass(frozen=True)
class Vehicle:
    id: str
    board_number: str
    cls: str
    fuel: str
    park_id: str
    condition: str
    home_route_id: str | None
    make: str = ""          # марка как в данных; сравнивать через normal_make


@dataclass(frozen=True)
class Driver:
    id: str
    tab_number: str
    park_id: str
    classes: tuple
    home_vehicle_id: str | None
    schedule: str
    medical: str | None
    makes: tuple = ()       # марки, на которых обкатан, уже через normal_make; пусто - без ограничения
    routes: tuple = ()      # маршруты, на которые допущен; пусто - без ограничения


@dataclass
class Day:
    meta: dict
    parks: dict
    routes: dict
    duties: dict
    shifts: dict
    vehicles: dict
    drivers: dict
    shifts_by_duty: dict = field(default_factory=dict)

    @property
    def day_type(self) -> str:
        return self.meta["day_type"]

    @classmethod
    def from_dict(cls, data: dict) -> "Day":
        result = check(data)
        if result["errors"]:
            raise ValueError("Данные дня противоречивы, движку их отдавать нельзя: "
                             + "; ".join(result["errors"][:5]))
        day = cls(
            meta=data["meta"],
            parks={p["id"]: Park(p["id"], p["name"], p["state"], p["release_weekday"],
                                 p["release_weekend"]) for p in data["parks"]},
            routes={r["id"]: Route(r["id"], r["park_id"], r["number"],
                                   tuple(r["allowed_classes"]), r["priority"],
                                   r["turnaround_min"])
                    for r in data["routes"]},
            duties={d["id"]: Duty(d["id"], d["park_id"], d["type"], d["route_id"],
                                  d["vehicle_class"], d["day_type"], minutes(d["start"]),
                                  minutes(d["end"]), d["shift_count"])
                    for d in data["duties"]},
            shifts={s["id"]: Shift(s["id"], s["duty_id"], s["order"], minutes(s["start"]),
                                   minutes(s["end"])) for s in data["shifts"]},
            vehicles={v["id"]: Vehicle(v["id"], v["board_number"], v["class"], v["fuel"],
                                       v["park_id"], v["condition"], v["home_route_id"], make_of(v))
                      for v in data["vehicles"]},
            drivers={d["id"]: Driver(d["id"], d["tab_number"], d["park_id"],
                                     tuple(d["classes"]), d["home_vehicle_id"],
                                     d["schedule"], d["medical"],
                                     tuple(normal_make(m) for m in d.get("makes") or ()),
                                     tuple(d.get("routes") or ())) for d in data["drivers"]},
        )
        for shift in sorted(day.shifts.values(), key=lambda s: s.order):
            day.shifts_by_duty.setdefault(shift.duty_id, []).append(shift)
        return day

    @classmethod
    def load(cls, path: str | Path) -> "Day":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# Почему наряд или смена остались без автобуса или водителя.
REASONS = {
    "no_vehicle": "нет исправного автобуса нужного класса",
    "no_driver": "нет водителя с допуском, который работает в этот день",
    "release_limit": "выпуск парка исчерпан",
    "lower_priority": "ресурс отдан маршрутам важнее",
    "park_down": "парк не выпускает автобусы",
    "manual": "решение диспетчера",
}


@dataclass
class Plan:
    """Ответ движка. Все ссылки - по id из дня."""

    vehicles: dict = field(default_factory=dict)   # наряд -> автобус
    drivers: dict = field(default_factory=dict)    # смена -> водитель
    unfilled: dict = field(default_factory=dict)   # наряд или смена -> причина
    transfers: dict = field(default_factory=dict)  # автобус -> в какой парк переброшен на день
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "meta": self.meta,
            "vehicle_assignments": [{"duty_id": d, "vehicle_id": v}
                                    for d, v in sorted(self.vehicles.items())],
            "driver_assignments": [{"shift_id": s, "driver_id": d}
                                   for s, d in sorted(self.drivers.items())],
            "unfilled": [{"id": i, "reason": r} for i, r in sorted(self.unfilled.items())],
            "transfers": [{"vehicle_id": v, "to_park": p} for v, p in sorted(self.transfers.items())],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Plan":
        """Повтор одного и того же наряда или смены - ошибка формата, а не нарушение."""
        plan = cls(meta=data.get("meta", {}))
        for name, target, key, value in (
                ("vehicle_assignments", plan.vehicles, "duty_id", "vehicle_id"),
                ("driver_assignments", plan.drivers, "shift_id", "driver_id"),
                ("unfilled", plan.unfilled, "id", "reason"),
                ("transfers", plan.transfers, "vehicle_id", "to_park")):
            for item in data.get(name, []):
                if item[key] in target:
                    raise ValueError(f"{name}: {item[key]} указан дважды")
                target[item[key]] = item[value]
        return plan
