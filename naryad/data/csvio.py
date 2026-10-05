"""
Выгрузка набора в CSV и загрузка обратно.

Движок читает только JSON. CSV сначала превращается в такой же набор,
проходит ту же проверку, и только потом уходит в движок.

Это строгое чтение: столбцы называются ровно как у нас. Для реестра
перевозчика, где столбцы названы по-русски и часть их нет, есть терпимое
чтение с отчётом - naryad.data.registry. Выгрузка (write_csv) только тут.

Папка набора: по файлу на сущность (parks.csv, routes.csv, ...) плюс
meta.json. Разделитель - точка с запятой, чтобы файл открывался в Excel
без настройки. Списки внутри ячейки - через «|», пустая ячейка - нет значения.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .check import SCHEMA

DELIMITER = ";"
LIST_SEP = "|"


def _cell(value, kind: str) -> str:
    if value is None:
        return ""
    if kind == "list":
        return LIST_SEP.join(value)
    return str(value)


def _value(text: str, kind: str, entity: str, field: str, row: int):
    text = text.strip()
    if text == "":
        return [] if kind == "list" else None
    try:
        if kind == "int":
            return int(text)
        if kind == "float":
            return float(text.replace(",", "."))
    except ValueError:
        raise ValueError(f"{entity}.csv, строка {row}: {field} = «{text}» - не число") from None
    if kind == "list":
        return [part.strip() for part in text.split(LIST_SEP) if part.strip()]
    return text


def write_csv(data: dict, folder) -> None:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for entity, fields in SCHEMA.items():
        with open(folder / f"{entity}.csv", "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=DELIMITER)
            writer.writerow([name for name, _, _ in fields])
            for item in data[entity]:
                writer.writerow([_cell(item.get(name), kind) for name, kind, _ in fields])
    (folder / "meta.json").write_text(
        json.dumps(data.get("meta", {}), ensure_ascii=False, indent=1), encoding="utf-8")


def read_csv(folder) -> dict:
    folder = Path(folder)
    data: dict = {}
    meta = folder / "meta.json"
    data["meta"] = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
    for entity, fields in SCHEMA.items():
        path = folder / f"{entity}.csv"
        if not path.exists():
            raise ValueError(f"нет файла {path.name}")
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, delimiter=DELIMITER)
            missing = [name for name, _, _ in fields if name not in (reader.fieldnames or [])]
            if missing:
                raise ValueError(f"{path.name}: нет столбцов {', '.join(missing)}")
            data[entity] = [
                {name: _value(row[name] or "", kind, entity, name, n)
                 for name, kind, _ in fields}
                for n, row in enumerate(reader, start=2)
            ]
    return data
