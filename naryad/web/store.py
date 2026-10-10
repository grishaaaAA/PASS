"""
Хранение дней на диске: день переживает перезапуск сервера.

Раньше дни жили только в памяти, и перезапуск стирал всё: утренний план,
события, ручные правки диспетчера. Для показа этого хватало, для работы
нет - диспетчер не должен терять день из-за того, что сервер перезапустили.

Что сохраняется и почему именно так. На день два файла:

- `<день>.day.json` - сам день как пришёл, плюс метки. Пишется один раз
  при загрузке и больше не трогается: он большой (полмегабайта на парк)
  и неизменный.
- `<день>.live.json` - то, что меняется: план, состояние дня отрезками,
  память водителей, журнал. Пишется при каждом изменении, он небольшой.

Состояние сохраняется снимком, а не пересчитывается заново из журнала.
Пересчёт был бы компактнее, но после правки движка он дал бы диспетчеру
не тот день, который тот видел и на который опирался. А ручная правка с
force - это сознательное решение человека, и переигрывать его движок не
вправе. Поэтому на диск кладётся то, что было на экране.

Запись без полуфайлов: сначала во временный файл рядом, потом замена
одним движением. Если сервер упадёт на середине, на диске останется
прошлая целая версия, а не обрывок.

Файл, который не читается или написан другой версией формата, при
загрузке пропускается с сообщением: один испорченный день не должен
мешать открыть остальные. Номер пропущенного дня новому дню не выдаётся,
и файл дня никогда не перезаписывается: иначе день, написанный более
новой версией сервиса или другим сервером на той же папке, был бы
уничтожен молча. Папку делить между двумя серверами нельзя.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

from naryad.core.model import Day, Plan
from naryad.ops.replan import OpsState, Segment
from naryad.solve.drivers import DriverState, History

FORMAT = 1
DAY_ID = re.compile(r"^day-\d+$")
DAY_FILE, LIVE_FILE = "{}.day.json", "{}.live.json"
ANY_FILE = re.compile(r"^day-(\d+)\.(day|live)\.json$")


# --- перевод объектов движка в JSON и обратно ---------------------------------

def plan_to_dict(plan: Plan) -> dict:
    return {"vehicles": plan.vehicles, "drivers": plan.drivers, "unfilled": plan.unfilled,
            "transfers": plan.transfers, "meta": plan.meta}


def plan_from_dict(data: dict) -> Plan:
    return Plan(vehicles=dict(data.get("vehicles") or {}), drivers=dict(data.get("drivers") or {}),
                unfilled=dict(data.get("unfilled") or {}), transfers=dict(data.get("transfers") or {}),
                meta=dict(data.get("meta") or {}))


def _segments_to_list(table: dict) -> dict:
    return {key: [[s.start, s.end, s.who] for s in segments]
            for key, segments in table.items() if segments}


def _segments_from_list(data: dict) -> dict:
    return {key: [Segment(int(start), int(end), who) for start, end, who in segments]
            for key, segments in (data or {}).items()}


def history_to_dict(history: History) -> dict:
    return {driver: [state.last_end, state.last_length, state.in_row, state.reduced,
                     state.month, state.month_minutes]
            for driver, state in history.drivers.items()}


def history_from_dict(data: dict) -> History:
    out = History()
    for driver, row in (data or {}).items():
        last_end, last_length, in_row, reduced, month, month_minutes = row
        out.drivers[driver] = DriverState(last_end, last_length, in_row, reduced,
                                          month, month_minutes)
    return out


def state_to_dict(state: OpsState) -> dict:
    return {"vehicles": _segments_to_list(state.vehicles),
            "drivers": _segments_to_list(state.drivers),
            "down_vehicles": state.down_vehicles, "down_drivers": state.down_drivers,
            "repairs": {vehicle: [[a, b] for a, b in windows] for vehicle, windows in state.repairs.items()},
            "transfers": state.transfers, "history": history_to_dict(state.history),
            "late": {driver: [since, comes] for driver, (since, comes) in state.late.items()},
            "log": list(state.log)}


def state_from_dict(data: dict, day: Day) -> OpsState:
    return OpsState(day=day, vehicles=_segments_from_list(data.get("vehicles")),
                    drivers=_segments_from_list(data.get("drivers")),
                    down_vehicles=dict(data.get("down_vehicles") or {}),
                    down_drivers=dict(data.get("down_drivers") or {}),
                    # дни, записанные до появления поля, читаются как без ремонтов
                    repairs={vehicle: [(int(a), int(b)) for a, b in windows]
                             for vehicle, windows in (data.get("repairs") or {}).items()},
                    history=history_from_dict(data.get("history")),
                    transfers=dict(data.get("transfers") or {}),
                    # дни, записанные до появления поля, читаются как без опозданий
                    late={driver: (int(since), int(comes))
                          for driver, (since, comes) in (data.get("late") or {}).items()},
                    log=list(data.get("log") or []))


# --- файлы --------------------------------------------------------------------

class Store:
    """Папка с днями. Без папки (None) ничего не пишется и не читается."""

    def __init__(self, folder=None):
        self.folder = Path(folder).expanduser() if folder else None
        if self.folder:
            try:
                self.folder.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise ValueError(f"Папка дней не создана: {self.folder} - "
                                 f"{'это файл, а не папка' if self.folder.exists() else 'нет прав или нет пути'}"
                                 f" ({error.strerror or error})") from None

    def __bool__(self) -> bool:
        return self.folder is not None

    def _write(self, name: str, payload: dict) -> None:
        """Целиком или никак: временный файл рядом, потом замена."""
        target = self.folder / name
        handle, temporary = tempfile.mkstemp(dir=str(self.folder), prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise

    def save_day(self, day_id: str, raw: dict) -> None:
        """Файл дня пишется один раз. Существующий не перезаписывается: это чужой день."""
        if self.folder:
            target = self.folder / DAY_FILE.format(day_id)
            if target.exists():
                raise FileExistsError(
                    f"{target.name} уже есть в папке {self.folder}: день {day_id} уже записан. "
                    f"Два сервера на одной папке? Папку делить нельзя")
            self._write(target.name, {"format": FORMAT, "day_id": day_id, "raw": raw})

    def save_live(self, day_id: str, live: dict) -> None:
        if self.folder:
            self._write(LIVE_FILE.format(day_id), {"format": FORMAT, "day_id": day_id, **live})

    def highest_number(self) -> int:
        """Наибольший номер дня среди всех файлов папки, включая непрочитанные.

        Новому дню нельзя выдать номер пропущенного дня: его файлы легли бы
        поверх файлов пропущенного, а тот мог быть написан более новой
        версией сервиса.
        """
        numbers = [0]
        for path in self.folder.iterdir() if self.folder else ():
            found = ANY_FILE.match(path.name)
            if found:
                numbers.append(int(found.group(1)))
        return max(numbers)

    def read_all(self) -> list:
        """[(day_id, день, живая часть или None)] по порядку номеров."""
        if not self.folder:
            return []
        out = []
        for path in sorted(self.folder.glob("*.day.json")):
            day_id = path.name[: -len(".day.json")]
            if not DAY_ID.match(day_id):
                self._skip(path, "имя файла не похоже на номер дня")
                continue
            day = self._read(path)
            if day is None:
                continue
            live_path = self.folder / LIVE_FILE.format(day_id)
            live = (self._read(live_path, f"день {day_id} открыт без плана и журнала")
                    if live_path.exists() else None)
            out.append((day_id, day, live))
        return sorted(out, key=lambda item: int(item[0].rsplit("-", 1)[-1]))

    def _read(self, path: Path, outcome: str = "день пропущен"):
        """Файл как объект JSON нашего формата, иначе None и сообщение в журнал сервера.

        outcome - что это значит для дня: файл дня не читается - день пропущен,
        живая часть не читается - день открыт, но без плана и журнала.
        """
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            return self._skip(path, f"файл не читается: {error}", outcome)
        if not isinstance(data, dict):
            return self._skip(path, "внутри не объект JSON", outcome)
        if data.get("format") != FORMAT:
            return self._skip(path, f"формат {data.get('format')}, этот сервис читает {FORMAT}", outcome)
        return data

    @staticmethod
    def _skip(path: Path, why: str, outcome: str = "день пропущен") -> None:
        print(f"{outcome[0].upper()}{outcome[1:]}, {path.name}: {why}", file=sys.stderr)
        return None
