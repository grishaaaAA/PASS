"""
Загрузка реестров перевозчика (Б4): CSV и Excel в набор данных движка.

Реестр перевозчика не совпадает с нашим форматом и не обязан: столбцы
названы по-русски и в другом порядке, состояния записаны словами
(«исправен», «в ремонте»), часть столбцов вообще отсутствует. Этот модуль
переводит такой файл в набор данных по docs/CONTRACT.md и вместе с ним
отдаёт отчёт: какой столбец чем понят, что не понято, что заполнено по
умолчанию. Отчёт важнее самого набора - по нему видно, где реестр и
движок расходятся, и что спросить у перевозчика.

Три правила, одно на весь модуль:

1. Поле, от которого зависит решение движка, выдумывать нельзя. Нет
   столбца - ошибка с понятным текстом. Остальное (адрес, госномер,
   модель, ФИО) заполняется значением по умолчанию, и это пишется в отчёт.
2. Значение, которое можно понять двояко, не угадывается. «Не прошёл» у
   медосмотра - это недопуск или просто ещё не проходил? Разница в том,
   выпустим ли мы человека на линию, поэтому такое значение уходит в отчёт
   как непонятое, а не толкуется на свой вкус.
3. Ошибка всегда называет место: сущность, строку, столбец и само значение.

Без сторонних библиотек: CSV читает csv из стандартной поставки, xlsx -
это zip с XML, его читают zipfile и xml.etree.

Запуск отдельно:
    python -m naryad.data.registry data/samples/park7_weekday_csv --date 2026-10-05
"""

from __future__ import annotations

import argparse
import base64
import csv
import io
import json
import math
import re
import sys
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree

from .check import ENUMS, SCHEMA, check
from .presets import CLASS_LABELS

MAX_MESSAGES = 50          # длиннее список ошибок человеку не нужен
MAX_SHEET_ROWS = 200_000   # строк на листе; у реестра города их тысячи, не сотни тысяч
MAX_SHEET_COLUMNS = 1_024  # столбцов на листе; у реестра их десятки
MAX_HOURS = 36             # время наряда: после полуночи часы идут дальше 24, но не через сутки с лишним
TIME_FIELDS = {("duties", "start"), ("duties", "end"), ("shifts", "start"), ("shifts", "end")}
LIST_SEPARATORS = re.compile(r"[|,;/]")

# Поля, без которых движок не примет решение: их не выдумываем.
ENGINE_FIELDS = {
    "parks": ("id", "name", "state", "release_weekday", "release_weekend"),
    "routes": ("id", "park_id", "number", "allowed_classes", "priority", "turnaround_min"),
    "duties": ("id", "park_id", "type", "route_id", "vehicle_class", "day_type",
               "start", "end", "shift_count"),
    "shifts": ("id", "duty_id", "order", "start", "end"),
    "vehicles": ("id", "board_number", "class", "fuel", "park_id", "condition", "home_route_id"),
    "drivers": ("id", "tab_number", "park_id", "classes", "home_vehicle_id", "schedule", "medical"),
}

# Поля, без которых движок решит, но хуже: закрепление своего автобуса за
# маршрутом и своего водителя за автобусом. Их отсутствие - предупреждение.
SOFT_FIELDS = {("vehicles", "home_route_id"), ("drivers", "home_vehicle_id"),
               ("drivers", "medical")}

# Если столбца с кодом нет, код собирается из естественного номера записи.
NATURAL_KEY = {"vehicles": "board_number", "drivers": "tab_number"}

SOFT_NOTES = {
    ("vehicles", "home_route_id"): "закрепление автобусов за маршрутами не передано: движок "
                                   "не сможет ставить свой автобус на свой маршрут",
    ("drivers", "home_vehicle_id"): "закрепление водителей за автобусами не передано: движок "
                                    "не сможет ставить своего водителя на свой автобус",
    ("drivers", "medical"): "отметки о медосмотре нет",
}

# Чем заполняем то, что движку не нужно, если столбца в реестре нет.
DEFAULTS = {
    ("parks", "address"): None, ("parks", "lat"): None, ("parks", "lon"): None,
    ("parks", "list_count"): 0, ("parks", "tech_readiness"): 1.0,
    ("parks", "reserve_weekday"): 0, ("parks", "reserve_weekend"): 0,
    ("routes", "length_km"): 0.0, ("routes", "length_source"): "реестр перевозчика",
    ("routes", "duties_weekday"): 0, ("routes", "duties_weekend"): 0,
    ("shifts", "start_place"): "park",
    ("vehicles", "plate"): "", ("vehicles", "model"): "", ("vehicles", "repair_days_left"): None,
    ("drivers", "full_name"): "",
    ("vehicles", "home_route_id"): None, ("drivers", "home_vehicle_id"): None,
    ("drivers", "medical"): None,
}

# Как может называться файл или лист с этой сущностью.
SHEETS = {
    "parks": ("parks", "парки", "парк", "автобусные парки", "автопарки"),
    "routes": ("routes", "маршруты", "маршрут", "реестр маршрутов"),
    "duties": ("duties", "наряды", "наряд", "выпуск", "реестр нарядов", "наряд на выпуск"),
    "shifts": ("shifts", "смены", "смена", "рабочие смены"),
    "vehicles": ("vehicles", "автобусы", "автобус", "подвижной состав", "тс",
                 "транспортные средства", "реестр тс"),
    "drivers": ("drivers", "водители", "водитель", "личный состав", "реестр водителей"),
}

# Как может называться столбец. Наше поле -> что стоит в реестре.
COLUMNS = {
    "parks": {
        "id": ("id", "код", "код парка", "номер парка", "park id"),
        "name": ("name", "название", "наименование", "название парка", "наименование парка"),
        "address": ("address", "адрес"),
        "lat": ("lat", "широта"),
        "lon": ("lon", "долгота"),
        "state": ("state", "состояние", "состояние парка", "статус", "режим работы"),
        "list_count": ("list count", "списочное количество", "списочная численность", "по списку"),
        "tech_readiness": ("tech readiness", "ктг", "коэффициент технической готовности",
                           "техническая готовность"),
        "release_weekday": ("release weekday", "выпуск будни", "выпуск в будни",
                            "плановый выпуск будни", "выпуск будний день"),
        "release_weekend": ("release weekend", "выпуск выходные", "выпуск в выходные",
                            "плановый выпуск выходные", "выпуск выходной день"),
        "reserve_weekday": ("reserve weekday", "резерв будни", "резерв в будни"),
        "reserve_weekend": ("reserve weekend", "резерв выходные", "резерв в выходные"),
    },
    "routes": {
        "id": ("id", "код", "код маршрута"),
        "park_id": ("park id", "парк", "код парка", "парк приписки"),
        "number": ("number", "номер", "номер маршрута", "маршрут"),
        "allowed_classes": ("allowed classes", "классы", "допустимые классы", "класс автобуса",
                            "допустимые классы автобусов", "класс"),
        "priority": ("priority", "важность", "приоритет", "категория маршрута"),
        "length_km": ("length km", "длина", "длина км", "протяженность", "протяженность км"),
        "length_source": ("length source", "источник длины"),
        "turnaround_min": ("turnaround min", "время оборота", "оборот", "оборот мин",
                           "время оборотного рейса"),
        "duties_weekday": ("duties weekday", "нарядов будни", "наряды будни"),
        "duties_weekend": ("duties weekend", "нарядов выходные", "наряды выходные"),
    },
    "duties": {
        "id": ("id", "код", "наряд", "номер наряда", "код наряда"),
        "park_id": ("park id", "парк", "код парка"),
        "type": ("type", "тип", "тип наряда", "вид наряда"),
        "route_id": ("route id", "маршрут", "код маршрута"),
        "vehicle_class": ("vehicle class", "класс", "класс автобуса"),
        "day_type": ("day type", "тип дня", "день"),
        "start": ("start", "начало", "время начала", "выход", "время выхода"),
        "end": ("end", "конец", "окончание", "время окончания", "заезд", "время заезда"),
        "shift_count": ("shift count", "смен", "количество смен", "число смен"),
    },
    "shifts": {
        "id": ("id", "код", "код смены"),
        "duty_id": ("duty id", "наряд", "код наряда", "номер наряда"),
        "order": ("order", "смена", "номер смены", "порядок", "смена по счету"),
        "start": ("start", "начало", "время начала"),
        "end": ("end", "конец", "окончание", "время окончания"),
        "start_place": ("start place", "место начала", "откуда", "начало смены"),
    },
    "vehicles": {
        "id": ("id", "код", "код автобуса", "идентификатор"),
        "board_number": ("board number", "гаражный номер", "гаражный", "бортовой номер", "борт"),
        "plate": ("plate", "госномер", "гос номер", "регистрационный номер", "номер тс"),
        "model": ("model", "модель", "марка", "марка модель"),
        "class": ("class", "класс", "класс автобуса", "вместимость"),
        "fuel": ("fuel", "топливо", "вид топлива", "тип топлива"),
        "park_id": ("park id", "парк", "код парка", "парк приписки"),
        "condition": ("condition", "состояние", "техническое состояние", "тех состояние",
                      "техсостояние", "статус", "состояние тс"),
        "repair_days_left": ("repair days left", "дней ремонта", "осталось дней ремонта",
                             "срок ремонта"),
        "home_route_id": ("home route id", "закрепленный маршрут", "маршрут закрепления",
                          "маршрут"),
    },
    "drivers": {
        "id": ("id", "код", "код водителя"),
        "tab_number": ("tab number", "табельный номер", "табельный", "табель"),
        "full_name": ("full name", "фио", "ф и о", "фамилия имя отчество", "водитель"),
        "park_id": ("park id", "парк", "код парка", "парк приписки"),
        "classes": ("classes", "классы", "допуск", "допуски", "допуск к классам", "категории"),
        "home_vehicle_id": ("home vehicle id", "закрепленный автобус", "закрепление",
                            "свой автобус"),
        "schedule": ("schedule", "график", "график работы", "график на день"),
        "medical": ("medical", "медосмотр", "предрейсовый медосмотр", "медицинский осмотр",
                    "предрейсовый осмотр"),
    },
}

# Значения словами. Двусмысленное сюда не кладём: лучше честный вопрос в
# отчёте, чем тихая догадка. Пример: «не прошёл» у медосмотра может быть и
# недопуском, и «ещё не проходил», а от этого зависит выпуск на линию.
VALUES = {
    ("parks", "state"): {"working": ("работает", "рабочее", "в работе", "норма", "штатный"),
                         "emergency": ("чп", "чрезвычайная ситуация", "аварийный режим"),
                         "down": ("остановлен", "не работает", "простой", "закрыт")},
    ("routes", "priority"): {1: ("высокая", "высокий", "первая", "social", "социальный"),
                             2: ("средняя", "средний", "вторая"),
                             3: ("низкая", "низкий", "третья")},
    ("duties", "type"): {"line": ("линейный", "линия", "маршрутный", "на линию"),
                         "reserve": ("резерв", "резервный", "в резерве")},
    ("duties", "day_type"): {"weekday": ("будни", "будний", "будний день", "рабочий день", "рабочий"),
                             "weekend": ("выходные", "выходной", "выходной день", "праздничный")},
    ("shifts", "start_place"): {"park": ("парк", "из парка", "гараж", "от парка"),
                                "line": ("линия", "на линии", "на маршруте", "пересменка")},
    ("vehicles", "fuel"): {"gas": ("газ", "газовый", "кпг", "спг", "метан"),
                           "diesel": ("дизель", "дизельный", "дт"),
                           "electric": ("электро", "электрический", "электробус")},
    ("vehicles", "condition"): {"ok": ("исправен", "исправный", "в строю", "годен", "рабочее"),
                                "repair": ("ремонт", "в ремонте", "неисправен", "неисправный"),
                                "maintenance": ("то", "техобслуживание", "на то",
                                                "техническое обслуживание"),
                                "accident": ("дтп", "после дтп", "авария")},
    ("drivers", "schedule"): {"work": ("работает", "рабочий", "рабочий день", "на линии", "выход"),
                              "day_off": ("выходной", "отгул", "нерабочий"),
                              "sick": ("больничный", "болеет", "нетрудоспособность"),
                              "vacation": ("отпуск", "в отпуске")},
    ("drivers", "medical"): {"passed": ("прошел", "пройден", "допущен", "годен"),
                             "failed": ("не допущен", "отстранен", "недопуск"),
                             "pending": ("ожидает", "назначен", "не проходил")},
}
# Класс автобуса называется одинаково в четырёх местах, словарь один.
CLASS_WORDS = {
    "medium": ("средний", "средней вместимости", "св", "м"),
    "big": ("большой", "большой вместимости", "бв", "б"),
    "extra_big": ("особо большой", "особо большой вместимости", "обв", "об"),
}
assert set(CLASS_WORDS) == set(CLASS_LABELS), "классы в словаре и в наборе данных разошлись"
for _entity, _field in (("routes", "allowed_classes"), ("duties", "vehicle_class"),
                        ("vehicles", "class"), ("drivers", "classes")):
    VALUES[(_entity, _field)] = CLASS_WORDS


def normal(text) -> str:
    """Название столбца или значение к сравнимому виду: регистр, ё, знаки."""
    low = str(text or "").replace("ё", "е").replace("Ё", "Е").lower()
    return " ".join(re.sub(r"[^0-9a-zа-я]+", " ", low).split())


def _index(table: dict) -> dict:
    """Синонимы -> наше имя. Повтор синонима внутри сущности - ошибка кода."""
    out: dict = {}
    for name, synonyms in table.items():
        for synonym in (name,) + tuple(synonyms):
            key = normal(synonym)
            if key in out and out[key] != name:
                raise ValueError(f"синоним «{synonym}» занят полем {out[key]}, нельзя дать {name}")
            out[key] = name
    return out


COLUMN_INDEX = {entity: _index(fields) for entity, fields in COLUMNS.items()}
SHEET_INDEX = _index({entity: names for entity, names in SHEETS.items()})
VALUE_INDEX = {key: {normal(word): code for code, words in table.items()
                     for word in (tuple(words) + ((code,) if isinstance(code, str) else ()))}
               for key, table in VALUES.items()}


# --- чтение таблиц ------------------------------------------------------------

def _sniff(line: str) -> str:
    """Разделитель CSV. Реестры приходят и с точкой с запятой, и с запятой."""
    counts = {sep: line.count(sep) for sep in (";", "\t", ",")}
    best = max(counts, key=lambda sep: counts[sep])
    return best if counts[best] else ";"


def read_csv_text(text: str, name: str = "CSV") -> list:
    """Текст CSV -> строки таблицы. Пустые строки в конце отбрасываются.

    name - как назвать файл в ошибке. Незакрытая кавычка в большом файле
    делает весь остаток одним полем, и csv отказывается его читать: это
    ошибка реестра, а не сервиса, поэтому ValueError с понятным текстом.
    """
    text = text.lstrip("﻿")
    first = next((line for line in text.splitlines() if line.strip()), "")
    try:
        rows = [[cell.strip() for cell in row]
                for row in csv.reader(io.StringIO(text), delimiter=_sniff(first))]
    except csv.Error as error:
        raise ValueError(f"файл «{name}» не разбирается как CSV: незакрытая кавычка или "
                         f"слишком длинное поле ({error})") from None
    return _trim(rows)


def _trim(rows: list) -> list:
    while rows and not any(cell for cell in rows[-1]):
        rows.pop()
    return rows


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _text(node) -> str:
    return "".join(part.text or "" for part in node.iter() if _local(part.tag) == "t")


def _column(reference: str, fallback: int) -> int:
    """«C12» -> 2. Без буквенной части берётся место по порядку."""
    letters = "".join(c for c in reference if c.isalpha()).upper()
    if not letters:
        return fallback
    number = 0
    for letter in letters:
        number = number * 26 + (ord(letter) - 64)
    return number - 1


def _number_text(raw: str) -> str:
    """Excel хранит числа как 350 или 350.0: приводим к виду без хвоста."""
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return raw
    if not math.isfinite(value):   # INF и NaN - не числа, пусть их не поймёт разбор значения
        return raw
    return str(int(value)) if value == int(value) else raw


def _part(archive: zipfile.ZipFile, name: str):
    """Часть книги как XML. Повреждённая или защищённая книга - ValueError с местом."""
    try:
        return ElementTree.fromstring(archive.read(name))
    except RuntimeError as error:   # zipfile так сообщает о шифровании
        if "encrypt" in str(error).lower() or "password" in str(error).lower():
            raise ValueError("книга Excel защищена паролем, снимите защиту и загрузите снова") from None
        raise ValueError(f"книга Excel повреждена: {name} не читается ({error})") from None
    except (KeyError, OSError, zipfile.BadZipFile, zlib.error, ElementTree.ParseError) as error:
        raise ValueError(f"книга Excel повреждена: {name} не читается как XML ({error})") from None


def read_xlsx(blob: bytes) -> dict:
    """Книга Excel -> {название листа: строки}. Только стандартная поставка."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
    except (zipfile.BadZipFile, OSError):
        raise ValueError("файл не читается как книга Excel (.xlsx): это не zip-архив") from None
    with archive:
        names = set(archive.namelist())
        if "xl/workbook.xml" not in names:
            raise ValueError("файл не похож на книгу Excel: внутри нет xl/workbook.xml. "
                             "Старый формат .xls не поддерживается, пересохраните как .xlsx")
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = _part(archive, "xl/sharedStrings.xml")
            shared = [_text(node) for node in root if _local(node.tag) == "si"]
        targets = {}
        if "xl/_rels/workbook.xml.rels" in names:
            root = _part(archive, "xl/_rels/workbook.xml.rels")
            for node in root:
                target = node.get("Target", "")
                path = target[1:] if target.startswith("/") else "xl/" + target
                targets[node.get("Id")] = path.replace("xl/xl/", "xl/")
        sheets, order = {}, 0
        book = _part(archive, "xl/workbook.xml")
        for node in book.iter():
            if _local(node.tag) != "sheet":
                continue
            order += 1
            rid = next((v for k, v in node.attrib.items() if _local(k) == "id"), None)
            path = targets.get(rid) or f"xl/worksheets/sheet{order}.xml"
            if path in names:
                sheets[node.get("name") or f"Лист{order}"] = _sheet_rows(_part(archive, path), shared)
        return sheets


def _sheet_rows(root, shared: list) -> list:
    """Строки листа. Номер строки - как в Excel: пустые строки, которых в XML нет, остаются пустыми."""
    rows = []
    for row in root.iter():
        if _local(row.tag) != "row":
            continue
        number = row.get("r") or ""
        if number.isdigit() and int(number) > MAX_SHEET_ROWS:
            raise ValueError(f"лист содержит строку с номером {number}, а предел {MAX_SHEET_ROWS}: "
                             f"это не похоже на реестр")
        if number.isdigit() and int(number) > len(rows) + 1:
            rows.extend([] for _ in range(int(number) - 1 - len(rows)))
        cells: list = []
        for position, cell in enumerate(c for c in row if _local(c.tag) == "c"):
            where = _column(cell.get("r") or "", position)
            if where >= MAX_SHEET_COLUMNS:
                raise ValueError(f"лист содержит ячейку {cell.get('r')} дальше столбца номер "
                                 f"{MAX_SHEET_COLUMNS}: это не похоже на реестр")
            cells.extend([""] * (where + 1 - len(cells)))
            kind = cell.get("t")
            if kind == "s":
                raw = next((part.text or "" for part in cell if _local(part.tag) == "v"), "")
                cells[where] = shared[int(raw)] if raw.isdigit() and int(raw) < len(shared) else ""
            elif kind == "inlineStr":
                cells[where] = _text(cell)
            else:
                raw = next((part.text or "" for part in cell if _local(part.tag) == "v"), "")
                cells[where] = raw if kind in ("str", "b") else _number_text(raw)
        rows.append([cell.strip() for cell in cells])
    return _trim(rows)


# --- перевод таблиц в набор данных --------------------------------------------

def match_tables(raw: dict) -> tuple[dict, list, list]:
    """{название листа или файла: строки} -> ({сущность: (название, строки)}, непонятые, повторы).

    Повтор - вторая таблица той же сущности: она понята, но пропущена,
    и это отдельное предупреждение, а не «не понято, что это».
    """
    tables, unknown, duplicates = {}, [], []
    for name, rows in raw.items():
        entity = SHEET_INDEX.get(normal(name))
        if entity is None:
            unknown.append(name)
        elif entity in tables:
            duplicates.append((name, tables[entity][0]))
        else:
            tables[entity] = (name, rows)
    return tables, unknown, duplicates


def match_headers(entity: str, header: list) -> tuple[dict, list]:
    """Заголовки столбцов -> {наше поле: (номер столбца, как названо)}."""
    index, found, unknown = COLUMN_INDEX[entity], {}, []
    for position, title in enumerate(header):
        if not str(title).strip():
            continue
        name = index.get(normal(title))
        if name is None or name in found:
            unknown.append(str(title).strip())
        else:
            found[name] = (position, str(title).strip())
    return found, unknown


def _time(raw: str):
    """«4:50» -> «04:50»; доля суток из Excel -> то же. Иначе None.

    Потолок один для обоих видов - MAX_HOURS: наряд кончается после
    полуночи (25:20), но не через сутки с лишним. Ячейка «2», «3.0» или
    «99:59» - не время наряда, а ошибка в реестре, и её надо показать, а
    не молча принять как 48:00.
    """
    found = re.fullmatch(r"(\d{1,2}):(\d{1,2})(?::\d{1,2})?", raw.strip())
    if found:
        hours, minutes = int(found.group(1)), int(found.group(2))
        return f"{hours:02d}:{minutes:02d}" if minutes < 60 and hours < MAX_HOURS else None
    try:
        share = float(raw.replace(",", "."))
    except ValueError:
        return None
    total = round(share * 24 * 60)   # Excel держит время как долю суток, 25:20 это 1.06
    if not 0 <= total < MAX_HOURS * 60:
        return None
    return f"{total // 60:02d}:{total % 60:02d}"


def _plain(entity: str, field: str, kind: str, raw: str):
    """Значение, уже записанное в нашем виде. ValueError - не читается."""
    if (entity, field) in TIME_FIELDS:
        value = _time(raw)
        if value is None:
            raise ValueError(raw)
        return value
    if kind == "int":
        return int(float(raw.replace(",", ".")))
    if kind == "float":
        return float(raw.replace(",", "."))
    return raw


def _single(entity: str, field: str, kind: str, raw: str):
    """Одно значение в наш вид. ValueError - значение не понято.

    Сначала словарь слов («в ремонте» -> repair), потом значение в нашем
    виде: в реестре, выгруженном из нашего же формата, стоят готовые коды
    и цифры, и их тоже надо принимать.
    """
    words = VALUE_INDEX.get((entity, field))
    if words is None:
        return _plain(entity, field, kind, raw)
    code = words.get(normal(raw))
    if code is not None:
        return code
    value = _plain(entity, field, kind, raw)
    allowed = ENUMS.get((entity, field))
    if allowed is not None and value not in allowed:
        raise ValueError(raw)
    return value


def _cell(entity: str, field: str, kind: str, raw: str, unmapped: dict, where: tuple):
    """Значение ячейки в наш вид. Непонятое копится в unmapped, тут None.

    where - (таблица, столбец, номер строки): по ним ошибка называет место.
    OverflowError - «1e999» в целом поле, это такое же непонятое значение.
    """
    parts = [p.strip() for p in LIST_SEPARATORS.split(raw) if p.strip()] if kind == "list" else [raw]
    out = []
    for part in parts:
        try:
            out.append(_single(entity, field, kind, part))
        except (ValueError, TypeError, OverflowError):
            seen = unmapped.setdefault((entity, field, part), {"source": where[0], "title": where[1], "rows": []})
            seen["rows"].append(where[2])
            return None
    return out if kind == "list" else out[0]


def _rows_text(numbers: list) -> str:
    """«строка 3», «строки 3, 7», «строки 3, 7, 12 и ещё 4»."""
    if len(numbers) == 1:
        return f"строка {numbers[0]}"
    shown = ", ".join(str(n) for n in numbers[:3])
    return f"строки {shown}" + (f" и ещё {len(numbers) - 3}" if len(numbers) > 3 else "")


def _allowed(entity: str, field: str) -> str:
    """Что можно написать в этом столбце - для текста ошибки."""
    words = VALUES.get((entity, field))
    if words:
        return ", ".join(f"{code}" + (f" ({w[0]})" if w else "") for code, w in words.items())
    if (entity, field) in TIME_FIELDS:
        return f"время в виде ЧЧ:ММ, часы после полуночи идут дальше 24, но не больше {MAX_HOURS - 1}:59"
    kinds = {"int": "целое число", "float": "число", "list": "список через | или запятую"}
    return kinds.get(dict((f, k) for f, k, _ in SCHEMA[entity])[field], "текст")


def build(tables: dict, meta: dict | None = None) -> tuple[dict, dict]:
    """Таблицы реестра -> (набор данных, отчёт о загрузке)."""
    data: dict = {"meta": {"format_version": "0.2", "source": "реестр перевозчика", **(meta or {})}}
    report: dict = {"entities": [], "errors": [], "warnings": [], "unknown_values": []}
    unmapped: dict = {}

    for entity, fields in SCHEMA.items():
        data[entity] = []
        if entity not in tables:
            report["errors"].append(
                f"нет таблицы «{entity}»: ожидался лист или файл с названием "
                f"{' или '.join(repr(n) for n in SHEETS[entity][:3])}")
            continue
        source, rows = tables[entity]
        line = {"entity": entity, "source": source, "rows": 0, "matched": {},
                "defaulted": [], "derived": [], "unknown_columns": [], "missing": []}
        report["entities"].append(line)
        # заголовок - первая непустая строка: перед ним бывает пустая или оформленная строка
        head = next((i for i, row in enumerate(rows) if any(str(cell).strip() for cell in row)), None)
        if head is None:
            report["errors"].append(f"{source}: таблица пустая, нет даже заголовка")
            continue
        found, unknown = match_headers(entity, rows[head])
        line["unknown_columns"] = unknown
        line["matched"] = {name: title for name, (_, title) in sorted(found.items())}
        key = NATURAL_KEY.get(entity)
        if "id" not in found and key in found:
            line["derived"].append("id")
            report["warnings"].append(
                f"{source}: нет столбца с кодом, код собран из столбца "
                f"«{found[key][1]}». Если у перевозчика свой код - назовите столбец id")
        for field, kind, nullable in fields:
            if field in found or field in line["derived"]:
                continue
            if (entity, field) in SOFT_FIELDS:
                line["defaulted"].append(field)
                note = SOFT_NOTES[(entity, field)]
                if (entity, field) == ("drivers", "medical") and \
                        (data["meta"].get("moment") or "plan") == "morning":
                    report["errors"].append(
                        f"{source}: {note}, а день заявлен на утро (moment = morning). "
                        "Утром на линию выпускается только водитель с пройденным медосмотром, "
                        "так что без этого столбца план будет пустым. Добавьте столбец "
                        "«Медосмотр» или стройте план на завтра (moment = plan)")
                else:
                    report["warnings"].append(f"{source}: {note}")
            elif field in ENGINE_FIELDS[entity]:
                line["missing"].append(field)
            else:
                line["defaulted"].append(field)
        if line["missing"]:
            report["errors"].append(
                f"{source}: нет столбцов, без которых движок не решит: "
                f"{', '.join(line['missing'])}. Понятые столбцы: "
                f"{', '.join(line['matched'].values()) or 'ни одного'}")
            continue

        for number, row in enumerate(rows[head + 1:], start=head + 2):  # number - строка файла
            if not any(str(cell).strip() for cell in row):
                continue
            item = {}
            for field, kind, nullable in fields:
                if field == "id" and field in line["derived"]:
                    natural = found[key][0]
                    item["id"] = str(row[natural]).strip() if natural < len(row) else ""
                    continue
                if field not in found:
                    item[field] = DEFAULTS[(entity, field)]
                    continue
                position, title = found[field]
                raw = str(row[position]).strip() if position < len(row) else ""
                if kind == "list" and not LIST_SEPARATORS.sub("", raw).strip():
                    raw = ""   # «|» или «,» без единого слова - пустая ячейка, а не пустой список
                if not raw:
                    item[field] = None
                    if not nullable:
                        report["errors"].append(
                            f"{source}, строка {number}, столбец «{title}»: пусто, "
                            f"а без этого значения нельзя")
                    continue
                item[field] = _cell(entity, field, kind, raw, unmapped, (source, title, number))
            data[entity].append(item)
            line["rows"] += 1

    _fill_counts(data, report)

    for (entity, field, value), seen in sorted(unmapped.items(), key=lambda kv: -len(kv[1]["rows"])):
        report["unknown_values"].append({"entity": entity, "field": field, "value": value,
                                         "rows": len(seen["rows"]), "title": seen["title"],
                                         "first_rows": seen["rows"][:3]})
        report["errors"].append(
            f"{seen['source']}, столбец «{seen['title']}», {_rows_text(seen['rows'])}: "
            f"значение «{value}» не понято. Допустимо: {_allowed(entity, field)}")
    return data, report


def _fill_counts(data: dict, report: dict) -> None:
    """Поля, которых не было в реестре, но которые должны быть согласованы.

    Списочное количество автобусов парка и число нарядов маршрута проверка
    сверяет с фактом. Поставить туда ноль значит выдумать расхождение,
    которого в реестре нет, и завалить загрузку ошибкой на ровном месте.
    Поэтому, когда столбца не было, берём то, что реально лежит в данных.

    Срок ремонта проверка требует у каждого неисправного автобуса. В
    реестре его обычно нет: написано «в ремонте», а на сколько - нет.
    Движок это поле не использует вовсе (оно нужно только прогнозу на
    несколько дней), поэтому ставим один день и пишем об этом в отчёт.
    """
    missing = {(line["entity"], field) for line in report["entities"]
               for field in line["defaulted"]}
    day_type = data["meta"].get("day_type")
    for park in data["parks"]:
        own = [d for d in data["duties"] if d.get("park_id") == park["id"]]
        if ("parks", "list_count") in missing:
            park["list_count"] = sum(v.get("park_id") == park["id"] for v in data["vehicles"])
        reserve = f"reserve_{day_type}"
        if ("parks", reserve) in missing:
            park[reserve] = sum(d.get("type") == "reserve" for d in own)
    for route in data["routes"]:
        for suffix in ("weekday", "weekend"):
            field = f"duties_{suffix}"
            if ("routes", field) not in missing:
                continue
            route[field] = (sum(d.get("route_id") == route["id"] for d in data["duties"])
                            if suffix == day_type else 0)
    broken = [v for v in data["vehicles"]
              if v.get("condition") not in (None, "ok") and v.get("repair_days_left") is None]
    for vehicle in broken:
        vehicle["repair_days_left"] = 1
    if broken:
        report["warnings"].append(
            (f"срока ремонта в реестре нет, неисправным автобусам поставлен 1 день (таких {len(broken)})"
             if ("vehicles", "repair_days_left") in missing else
             f"срок ремонта не заполнен у неисправных автобусов (таких {len(broken)}), им поставлен 1 день")
            + ". Движок это поле не использует, оно нужно только прогнозу на несколько дней")


# --- точки входа ---------------------------------------------------------------

def load(files: dict | None = None, workbook: bytes | None = None,
         meta: dict | None = None) -> tuple[dict, dict]:
    """Реестр -> (набор данных, отчёт).

    files - {название файла или сущности: текст CSV}, workbook - книга
    Excel целиком. Можно вместе: лист книги и отдельный файл дополняют
    друг друга. Общая проверка данных запускается только когда реестр
    прочитан без ошибок: иначе её список накрыл бы то, что надо чинить
    первым.
    """
    raw: dict = {}
    notes: list = []
    if workbook is not None:
        raw.update(read_xlsx(workbook))
    for name, text in (files or {}).items():
        stem = Path(str(name)).stem
        if stem in raw:
            notes.append(f"файл «{name}» перекрыл таблицу «{stem}», загруженную раньше: "
                         f"одинаковое название без расширения")
        raw[stem] = read_csv_text(text, str(name))
    if not raw:
        return {"meta": dict(meta or {})}, {
            "entities": [], "errors": ["не передано ни одного файла реестра"],
            "warnings": [], "unknown_values": [], "unknown_tables": []}

    tables, unknown_tables, duplicates = match_tables(raw)
    data, report = build(tables, meta)
    report["unknown_tables"] = unknown_tables
    report["warnings"].extend(notes)
    if unknown_tables:
        report["warnings"].append(
            f"не понято, что это за таблицы, они пропущены: {', '.join(unknown_tables)}")
    for name, first in duplicates:
        report["warnings"].append(f"таблица «{name}» повторяет уже прочитанную «{first}», пропущена")
    if not report["errors"]:
        result = check(data)
        report["errors"].extend(result["errors"])
        report["warnings"].extend(result["warnings"])
    report["errors"] = report["errors"][:MAX_MESSAGES]
    report["warnings"] = report["warnings"][:MAX_MESSAGES]
    return data, report


def load_folder(folder, meta: dict | None = None) -> tuple[dict, dict]:
    """Папка с CSV (и meta.json, если он есть) -> (набор данных, отчёт)."""
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f"нет папки {folder}")
    files = {path.stem: path.read_text(encoding="utf-8-sig")
             for path in sorted(folder.glob("*.csv"))}
    stored = folder / "meta.json"
    both = json.loads(stored.read_text(encoding="utf-8")) if stored.exists() else {}
    both.update(meta or {})
    return load(files=files, meta=both)


def describe(report: dict) -> str:
    """Отчёт словами, для командной строки и для письма."""
    lines = []
    for line in report["entities"]:
        bits = [f"{line['entity']}: прочитано строк {line['rows']}, "
                f"столбцов понято {len(line['matched'])}"]
        if line["defaulted"]:
            bits.append(f"    заполнено по умолчанию (движку не нужно): {', '.join(line['defaulted'])}")
        if line["unknown_columns"]:
            bits.append(f"    столбцы не понятны, пропущены: {', '.join(line['unknown_columns'])}")
        if line["missing"]:
            bits.append(f"    НЕТ ОБЯЗАТЕЛЬНЫХ СТОЛБЦОВ: {', '.join(line['missing'])}")
        lines.extend(bits)
    for word, key in (("Ошибки", "errors"), ("Предупреждения", "warnings")):
        items = report.get(key) or []
        if items:
            lines.append(f"{word} ({len(items)}):")
            lines.extend(f"  - {text}" for text in items[:20])
    if not report.get("errors"):
        lines.append("Реестр прочитан, движку его отдавать можно.")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Загрузка реестра перевозчика (CSV или Excel)")
    parser.add_argument("path", help="папка с CSV или файл .xlsx")
    parser.add_argument("--date", help="дата дня ГГГГ-ММ-ДД")
    parser.add_argument("--day-type", choices=("weekday", "weekend"), help="тип дня")
    parser.add_argument("--moment", choices=("plan", "morning"), help="момент: план или утро")
    parser.add_argument("--out", help="куда записать набор данных в JSON")
    args = parser.parse_args(argv)
    meta = {k: v for k, v in (("date", args.date), ("day_type", args.day_type),
                              ("moment", args.moment)) if v}
    path = Path(args.path)
    try:
        if path.is_dir():
            data, report = load_folder(path, meta)
        else:
            data, report = load(workbook=path.read_bytes(), meta=meta)
    except (ValueError, OSError) as error:
        print(f"Реестр не прочитан: {error}", file=sys.stderr)
        return 1
    print(describe(report))
    if report["errors"]:
        return 1
    if args.out:
        Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"Набор данных записан: {args.out}")
    return 0


def encode(blob: bytes) -> str:
    """Книга Excel для передачи в API: base64 без переносов."""
    return base64.b64encode(blob).decode("ascii")


if __name__ == "__main__":
    raise SystemExit(main())
