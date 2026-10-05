"""Загрузка реестров перевозчика (Б4): CSV, Excel, отчёт о понятом. Запуск: python -m unittest"""

import io
import json
import unittest
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from naryad.data import registry
from naryad.data.registry import build, load, load_folder, match_headers, normal, read_csv_text

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_CSV = ROOT / "data" / "samples" / "park7_weekday_csv"
SAMPLE_JSON = ROOT / "data" / "samples" / "park7_weekday.json"
META = {"date": "2026-10-05", "day_type": "weekday", "moment": "morning"}

# Небольшой реестр в том виде, в каком его присылает перевозчик: русские
# заголовки, слова вместо кодов, нет части наших столбцов.
RUSSIAN = {
    "Парки": "Код парка;Наименование;Состояние;Выпуск будни;Выпуск выходные\n"
             "П7;Автобусный парк №7;работает;2;1\n",
    "Маршруты": "Код;Парк;Номер маршрута;Допустимые классы;Важность;Время оборота\n"
                "М3;П7;3;большой;высокая;160\n",
    "Наряды": "Наряд;Парк;Тип;Маршрут;Класс автобуса;Тип дня;Выход;Заезд;Смен\n"
              "Н1;П7;линейный;М3;большой;будни;4:50;13:20;1\n"
              "Н2;П7;линейный;М3;большой;будни;6:00;14:00;1\n",
    "Смены": "Код смены;Номер наряда;Смена;Начало;Окончание\n"
             "С1;Н1;1;4:50;13:20\nС2;Н2;1;6:00;14:00\n",
    "Автобусы": "Гаражный номер;Класс;Топливо;Парк;Тех. состояние\n"
                "7001;большой;газ;П7;исправен\n7002;большой;газ;П7;в ремонте\n",
    "Водители": "Табельный номер;ФИО;Парк;Допуск;График;Медосмотр\n"
                "070001;Макаров Д. В.;П7;большой;работает;прошел\n"
                "070002;Иванов И. И.;П7;большой|средний;выходной;\n",
}


def letters(index: int) -> str:
    out, index = "", index + 1
    while index:
        index, rest = divmod(index - 1, 26)
        out = chr(65 + rest) + out
    return out


def workbook(sheets: dict, shared: bool = False) -> bytes:
    """Минимальная книга .xlsx: только для тестов, движку писать Excel не нужно."""
    strings: list = []
    buffer = io.BytesIO()
    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as book:
        names = list(sheets)
        book.writestr("_rels/.rels",
                      f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                      f'<Relationship Id="rId1" Type="{rel}/officeDocument" Target="xl/workbook.xml"/>'
                      f'</Relationships>')
        book.writestr("xl/workbook.xml",
                      f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>'
                      + "".join(f'<sheet name="{escape(n)}" sheetId="{i}" r:id="rId{i}"/>'
                                for i, n in enumerate(names, 1)) + "</sheets></workbook>")
        book.writestr("xl/_rels/workbook.xml.rels",
                      '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                      + "".join(f'<Relationship Id="rId{i}" Type="{rel}/worksheet" '
                                f'Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(names) + 1))
                      + "</Relationships>")
        for number, name in enumerate(names, 1):
            rows = []
            for r, row in enumerate(sheets[name], 1):
                cells = []
                for c, value in enumerate(row):
                    place = f"{letters(c)}{r}"
                    if value is None or value == "":
                        continue
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        cells.append(f'<c r="{place}"><v>{value}</v></c>')
                    elif shared:
                        if value not in strings:
                            strings.append(value)
                        cells.append(f'<c r="{place}" t="s"><v>{strings.index(value)}</v></c>')
                    else:
                        cells.append(f'<c r="{place}" t="inlineStr"><is><t>{escape(str(value))}</t>'
                                     f'</is></c>')
                rows.append(f'<row r="{r}">' + "".join(cells) + "</row>")
            book.writestr(f"xl/worksheets/sheet{number}.xml",
                          f'<worksheet xmlns="{main}"><sheetData>' + "".join(rows)
                          + "</sheetData></worksheet>")
        if shared:
            book.writestr("xl/sharedStrings.xml",
                          f'<sst xmlns="{main}">'
                          + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>")
    return buffer.getvalue()


def table(rows: str) -> dict:
    """Текст CSV -> то, что ждёт build: {сущность: (название, строки)}."""
    return {name: (name, read_csv_text(text)) for name, text in rows.items()}


class TestSynonyms(unittest.TestCase):

    def test_normal_ignores_case_punctuation_and_yo(self):
        for text, want in (("Гаражный №", "гаражный"), ("Тех. состояние", "тех состояние"),
                           ("ВЫПУСК  В БУДНИ", "выпуск в будни"), ("Учёт", "учет"),
                           ("extra_big", "extra big"), (None, "")):
            self.assertEqual(normal(text), want, text)

    def test_every_field_of_the_schema_has_synonyms(self):
        """Поле, для которого нет синонимов, из реестра не прочитать никогда."""
        from naryad.data.check import SCHEMA
        for entity, fields in SCHEMA.items():
            described = set(registry.COLUMNS[entity])
            self.assertEqual({name for name, _, _ in fields}, described,
                             f"{entity}: список полей и словарь синонимов разошлись")

    def test_engine_fields_are_really_used_by_the_engine(self):
        """Обязательным считается только то, без чего движок не решит."""
        from naryad.data.check import SCHEMA
        for entity, fields in SCHEMA.items():
            names = {name for name, _, _ in fields}
            self.assertTrue(set(registry.ENGINE_FIELDS[entity]) <= names, entity)
            for name in names - set(registry.ENGINE_FIELDS[entity]):
                self.assertTrue((entity, name) in registry.DEFAULTS,
                                f"{entity}.{name}: не обязательное и без значения по умолчанию")


class TestCsv(unittest.TestCase):

    def test_sample_folder_round_trips_to_the_same_data(self):
        """Наш же набор, выгруженный в CSV, читается обратно без потерь."""
        data, report = load_folder(SAMPLE_CSV)
        self.assertEqual(report["errors"], [])
        source = json.loads(SAMPLE_JSON.read_text(encoding="utf-8"))
        for entity in ("parks", "routes", "duties", "shifts", "vehicles", "drivers"):
            self.assertEqual(data[entity], source[entity], entity)

    def test_delimiters_and_bom(self):
        for text in ("id;name\nP07;Парк\n", "id,name\nP07,Парк\n", "id\tname\nP07\tПарк\n",
                     "﻿id;name\nP07;Парк\n"):
            self.assertEqual(read_csv_text(text), [["id", "name"], ["P07", "Парк"]], text)

    def test_russian_registry_becomes_our_codes(self):
        data, report = load(files=RUSSIAN, meta=META)
        self.assertEqual(report["errors"], [])
        self.assertEqual(data["routes"][0]["priority"], 1)
        self.assertEqual(data["routes"][0]["allowed_classes"], ["big"])
        self.assertEqual(data["duties"][0]["type"], "line")
        self.assertEqual(data["duties"][0]["day_type"], "weekday")
        self.assertEqual((data["duties"][0]["start"], data["duties"][0]["end"]), ("04:50", "13:20"))
        self.assertEqual([v["condition"] for v in data["vehicles"]], ["ok", "repair"])
        self.assertEqual(data["drivers"][0]["medical"], "passed")
        self.assertEqual(data["drivers"][1]["classes"], ["big", "medium"])
        self.assertEqual(data["parks"][0]["state"], "working")

    def test_missing_code_column_is_built_from_the_natural_number(self):
        data, report = load(files=RUSSIAN, meta=META)
        self.assertEqual([v["id"] for v in data["vehicles"]], ["7001", "7002"])
        self.assertEqual([d["id"] for d in data["drivers"]], ["070001", "070002"])
        self.assertTrue(any("код собран из столбца" in w for w in report["warnings"]))

    def test_counts_absent_in_the_registry_are_taken_from_the_data(self):
        """Списочное количество не выдумывается нулём, иначе проверка упадёт зря."""
        data, report = load(files=RUSSIAN, meta=META)
        self.assertEqual(data["parks"][0]["list_count"], 2)
        self.assertEqual(data["routes"][0]["duties_weekday"], 2)
        self.assertEqual(report["errors"], [])

    def test_unknown_column_is_skipped_with_a_note(self):
        files = dict(RUSSIAN)
        files["Автобусы"] = files["Автобусы"].replace("Гаражный номер;", "Гаражный номер;Примечание;")
        files["Автобусы"] = files["Автобусы"].replace("7001;", "7001;сдан в аренду;").replace("7002;", "7002;;")
        data, report = load(files=files, meta=META)
        line = next(x for x in report["entities"] if x["entity"] == "vehicles")
        self.assertEqual(line["unknown_columns"], ["Примечание"])
        self.assertEqual(report["errors"], [])

    def test_unknown_table_is_skipped_with_a_warning(self):
        data, report = load(files=dict(RUSSIAN, **{"Путевые листы": "a;b\n1;2\n"}), meta=META)
        self.assertEqual(report["unknown_tables"], ["Путевые листы"])
        self.assertTrue(any("Путевые листы" in w for w in report["warnings"]))
        self.assertEqual(report["errors"], [])

    def test_missing_engine_column_is_an_error_that_names_it(self):
        files = dict(RUSSIAN)
        files["Автобусы"] = files["Автобусы"].replace(";Тех. состояние", "")
        files["Автобусы"] = "\n".join(line.rsplit(";", 1)[0] for line in files["Автобусы"].splitlines()) + "\n"
        data, report = load(files=files, meta=META)
        self.assertTrue(any("condition" in e for e in report["errors"]), report["errors"])
        line = next(x for x in report["entities"] if x["entity"] == "vehicles")
        self.assertIn("condition", line["missing"])

    def test_missing_soft_column_is_only_a_warning(self):
        """Без закрепления автобуса за маршрутом движок работает, просто хуже."""
        data, report = load(files=RUSSIAN, meta=META)
        self.assertEqual(report["errors"], [])
        self.assertIsNone(data["vehicles"][0]["home_route_id"])
        self.assertTrue(any("свой автобус на свой маршрут" in w for w in report["warnings"]))

    def test_ambiguous_medical_value_is_not_guessed(self):
        """«Не прошёл» - это недопуск или ещё не проходил? Не угадываем."""
        files = dict(RUSSIAN)
        files["Водители"] = files["Водители"].replace("прошел", "не прошёл")
        data, report = load(files=files, meta=META)
        self.assertTrue(any("не прошёл" in e for e in report["errors"]), report["errors"])
        self.assertEqual(report["unknown_values"][0]["field"], "medical")

    def test_no_medical_column_on_a_morning_day_is_an_error(self):
        """Без отметки о медосмотре утренний план был бы пустым и непонятно почему."""
        files = dict(RUSSIAN)
        files["Водители"] = files["Водители"].replace(";Медосмотр", "").replace(";прошел", "").replace(";\n", "\n")
        data, report = load(files=files, meta=META)
        self.assertTrue(any("медосмотр" in e.lower() for e in report["errors"]), report["errors"])
        data, report = load(files=files, meta=dict(META, moment="plan"))
        self.assertEqual(report["errors"], [])
        self.assertTrue(any("медосмотр" in w.lower() for w in report["warnings"]))

    def test_empty_cell_in_a_required_column_names_the_row(self):
        files = dict(RUSSIAN)
        files["Автобусы"] = files["Автобусы"].replace("7002;большой", "7002;")
        data, report = load(files=files, meta=META)
        self.assertTrue(any("строка 3" in e and "Класс" in e for e in report["errors"]), report["errors"])

    def test_empty_and_absent_tables(self):
        self.assertTrue(any("нет таблицы" in e for e in load(files={"Парки": "id;name\nP07;Парк\n"},
                                                             meta=META)[1]["errors"]))
        report = load(files=dict(RUSSIAN, **{"Автобусы": ""}), meta=META)[1]
        self.assertTrue(any("пустая" in e for e in report["errors"]), report["errors"])
        self.assertEqual(load(meta=META)[1]["errors"], ["не передано ни одного файла реестра"])

    def test_times_without_leading_zero_and_after_midnight(self):
        found, _ = match_headers("duties", ["Выход", "Заезд"])
        self.assertEqual(sorted(found), ["end", "start"])
        for raw, want in (("4:50", "04:50"), ("04:50", "04:50"), ("25:20", "25:20"),
                          ("0", "00:00"), ("0.2013888888", "04:50"), ("1.4", "33:36")):
            self.assertEqual(registry._time(raw), want, raw)
        for raw in ("4.50", "утром", "4:70", "9", "2", "3.0", "99:59", "36:00", "1.5"):
            self.assertIsNone(registry._time(raw), raw)

    def test_a_number_of_days_is_not_a_time(self):
        """«2» в столбце заезда - ошибка реестра, а не наряд до 48:00."""
        files = dict(RUSSIAN)
        files["Наряды"] = files["Наряды"].replace("6:00;14:00;1", "6:00;2;1")
        files["Смены"] = files["Смены"].replace("С2;Н2;1;6:00;14:00", "С2;Н2;1;6:00;2")
        data, report = load(files=files, meta=META)
        self.assertIsNone(data["duties"][1]["end"])
        self.assertTrue(any("Заезд" in e and "«2»" in e and "35:59" in e for e in report["errors"]), report["errors"])

    def test_header_is_the_first_non_empty_row(self):
        """Пустая строка перед заголовком не делает заголовком пустоту, номера строк - как в файле."""
        files = dict(RUSSIAN)
        files["Автобусы"] = "\n" + files["Автобусы"]
        data, report = load(files=files, meta=META)
        self.assertEqual(report["errors"], [])
        self.assertEqual([v["id"] for v in data["vehicles"]], ["7001", "7002"])
        files["Автобусы"] = files["Автобусы"].replace("7002;большой", "7002;")
        report = load(files=files, meta=META)[1]
        self.assertTrue(any("строка 4" in e and "Класс" in e for e in report["errors"]), report["errors"])

    def test_unknown_value_names_the_column_and_the_rows(self):
        files = dict(RUSSIAN)
        files["Автобусы"] = files["Автобусы"].replace("в ремонте", "на ходу?")
        data, report = load(files=files, meta=META)
        self.assertEqual(len(report["errors"]), 1)
        self.assertIn("Автобусы, столбец «Тех. состояние», строка 3: значение «на ходу?» не понято",
                      report["errors"][0])
        self.assertEqual(report["unknown_values"][0],
                         {"entity": "vehicles", "field": "condition", "value": "на ходу?", "rows": 1,
                          "title": "Тех. состояние", "first_rows": [3]})
        self.assertEqual(registry._rows_text([3, 7, 12, 20, 21]), "строки 3, 7, 12 и ещё 2")

    def test_blank_repair_days_for_a_broken_bus_is_filled_not_refused(self):
        files = dict(RUSSIAN)
        files["Автобусы"] = ("Гаражный номер;Класс;Топливо;Парк;Тех. состояние;Срок ремонта\n"
                            "7001;большой;газ;П7;исправен;\n7002;большой;газ;П7;в ремонте;\n")
        data, report = load(files=files, meta=META)
        self.assertEqual(report["errors"], [])
        self.assertEqual([v["repair_days_left"] for v in data["vehicles"]], [None, 1])
        self.assertTrue(any("не заполнен" in w and "1 день" in w for w in report["warnings"]), report["warnings"])

    def test_list_of_separators_only_is_an_empty_cell(self):
        files = dict(RUSSIAN)
        files["Водители"] = files["Водители"].replace("большой|средний", "|")
        data, report = load(files=files, meta=META)
        self.assertIsNone(data["drivers"][1]["classes"])
        self.assertTrue(any("строка 3" in e and "Допуск" in e and "пусто" in e for e in report["errors"]),
                        report["errors"])

    def test_duplicate_table_and_same_file_name_are_reported(self):
        data, report = load(files=dict(RUSSIAN, vehicles="id;board_number\nX;1\n"), meta=META)
        self.assertEqual(report["unknown_tables"], [])
        self.assertTrue(any("повторяет уже прочитанную «Автобусы»" in w for w in report["warnings"]),
                        report["warnings"])
        self.assertEqual(report["errors"], [])
        files = {**RUSSIAN, "Автобусы.csv": "Гаражный номер;Класс;Топливо;Парк;Тех. состояние\n"
                                            "9999;большой;газ;П7;исправен\n"}
        data, report = load(files=files, meta=META)
        self.assertEqual([v["id"] for v in data["vehicles"]], ["9999"])
        self.assertTrue(any("перекрыл" in w and "Автобусы.csv" in w for w in report["warnings"]),
                        report["warnings"])

    def test_overflow_and_nan_are_unknown_values(self):
        for bad in ("1e999", "inf", "nan"):
            files = dict(RUSSIAN)
            files["Маршруты"] = files["Маршруты"].replace(";160", f";{bad}")
            data, report = load(files=files, meta=META)
            self.assertTrue(any(bad in e and "Время оборота" in e and "строка 2" in e
                                for e in report["errors"]), (bad, report["errors"]))

    def test_unclosed_quote_in_a_big_file_is_a_value_error(self):
        text = ("Гаражный номер;Класс\n\"7002;большой\n" + "7003;большой\n" * 20000)
        with self.assertRaises(ValueError) as caught:
            read_csv_text(text, "Автобусы")
        self.assertIn("Автобусы", str(caught.exception))
        self.assertIn("кавычка", str(caught.exception))


class TestExcel(unittest.TestCase):

    SHEETS = {
        "Парки": [["Код парка", "Наименование", "Состояние", "Выпуск будни", "Выпуск выходные"],
                  ["П7", "Автобусный парк №7", "работает", 2, 1]],
        "Маршруты": [["Код", "Парк", "Номер маршрута", "Допустимые классы", "Важность",
                      "Время оборота"], ["М3", "П7", "3", "большой", "высокая", 160]],
        "Наряды": [["Наряд", "Парк", "Тип", "Маршрут", "Класс автобуса", "Тип дня", "Выход",
                    "Заезд", "Смен"],
                   ["Н1", "П7", "линейный", "М3", "большой", "будни", 0.2013888888, 0.5555555, 1],
                   ["Н2", "П7", "линейный", "М3", "большой", "будни", "6:00", "14:00", 1]],
        "Смены": [["Код смены", "Номер наряда", "Смена", "Начало", "Окончание"],
                  ["С1", "Н1", 1, 0.2013888888, 0.5555555], ["С2", "Н2", 1, "6:00", "14:00"]],
        "Автобусы": [["Гаражный номер", "Класс", "Топливо", "Парк", "Тех. состояние"],
                     [7001, "большой", "газ", "П7", "исправен"],
                     [7002, "большой", "газ", "П7", "исправен"]],
        "Водители": [["Табельный номер", "ФИО", "Парк", "Допуск", "График", "Медосмотр"],
                     ["070001", "Макаров Д. В.", "П7", "большой", "работает", "прошел"],
                     ["070002", "Иванов И. И.", "П7", "большой", "работает", "прошел"]],
    }

    def test_inline_and_shared_strings_read_the_same(self):
        first = load(workbook=workbook(self.SHEETS), meta=META)[0]
        second = load(workbook=workbook(self.SHEETS, shared=True), meta=META)[0]
        self.assertEqual(first, second)

    def test_sheets_values_and_times(self):
        data, report = load(workbook=workbook(self.SHEETS), meta=META)
        self.assertEqual(report["errors"], [])
        self.assertEqual([line["source"] for line in report["entities"]],
                         ["Парки", "Маршруты", "Наряды", "Смены", "Автобусы", "Водители"])
        self.assertEqual((data["duties"][0]["start"], data["duties"][0]["end"]), ("04:50", "13:20"))
        self.assertEqual(data["duties"][1]["start"], "06:00")
        self.assertEqual(data["vehicles"][0]["board_number"], "7001", "число не должно стать 7001.0")
        self.assertEqual(data["shifts"][0]["order"], 1)

    def test_csv_and_excel_give_the_same_day(self):
        from_excel = load(workbook=workbook(self.SHEETS), meta=META)[0]
        from_csv = load(files=RUSSIAN, meta=META)[0]
        self.assertEqual(from_excel["duties"], from_csv["duties"])
        self.assertEqual([v["id"] for v in from_excel["vehicles"]],
                         [v["id"] for v in from_csv["vehicles"]])

    def test_broken_files_say_what_is_wrong(self):
        with self.assertRaises(ValueError) as bad:
            load(workbook="PK\x03\x04 это не книга".encode("utf-8"))
        self.assertIn("zip", str(bad.exception))
        empty = io.BytesIO()
        with zipfile.ZipFile(empty, "w") as archive:
            archive.writestr("hello.txt", "привет")
        with self.assertRaises(ValueError) as old:
            load(workbook=empty.getvalue())
        self.assertIn(".xlsx", str(old.exception))
        garbage = io.BytesIO()
        with zipfile.ZipFile(garbage, "w") as archive:
            archive.writestr("xl/workbook.xml", "<<< не xml")
        with self.assertRaises(ValueError) as spoiled:
            load(workbook=garbage.getvalue())
        self.assertIn("повреждена", str(spoiled.exception))
        self.assertIn("xl/workbook.xml", str(spoiled.exception))
        sheets = dict(self.SHEETS)   # бесконечность в числовой ячейке - непонятое значение, а не падение
        sheets["Маршруты"] = [self.SHEETS["Маршруты"][0], self.SHEETS["Маршруты"][1][:5] + [float("inf")]]
        report = load(workbook=workbook(sheets), meta=META)[1]
        self.assertTrue(any("inf" in e and "строка 2" in e and "Время оборота" in e for e in report["errors"]),
                        report["errors"])

    def test_row_numbers_follow_the_sheet(self):
        """Excel не пишет пустые строки в XML: ошибка называет строку листа, а не порядковый номер."""
        main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

        def cell(column, row, value):
            return f'<c r="{column}{row}" t="inlineStr"><is><t>{value}</t></is></c>'

        header = ["Гаражный номер", "Класс", "Топливо", "Парк", "Тех. состояние"]
        rows = ('<row r="1">' + "".join(cell(c, 1, v) for c, v in zip("ABCDE", header)) + "</row>"
                '<row r="2">' + "".join(cell(c, 2, v) for c, v in zip("ABCDE", ["7001", "большой", "газ", "П7", "исправен"])) + "</row>"
                '<row r="4">' + "".join(cell(c, 4, v) for c, v in zip("ABDE", ["7002", "большой", "П7", "исправен"])) + "</row>")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as book:
            book.writestr("xl/workbook.xml", f'<workbook xmlns="{main}"><sheets><sheet name="Автобусы" sheetId="1"/></sheets></workbook>')
            book.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{main}"><sheetData>{rows}</sheetData></worksheet>')
        report = load(workbook=buffer.getvalue(), meta=META)[1]
        self.assertTrue(any("строка 4" in e and "Топливо" in e for e in report["errors"]), report["errors"])
        self.assertFalse(any("строка 3" in e for e in report["errors"]), report["errors"])

    def test_formatted_empty_first_row_is_not_the_header(self):
        """Оформленная пустая первая строка листа (ячейки без значений) - не заголовок."""
        main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

        def cell(column, row, value):
            return f'<c r="{column}{row}" t="inlineStr"><is><t>{value}</t></is></c>'

        header = ["Гаражный номер", "Класс", "Топливо", "Парк", "Тех. состояние"]
        rows = ('<row r="1"><c r="A1" s="1"/><c r="B1" s="1"/></row>'
                '<row r="2">' + "".join(cell(c, 2, v) for c, v in zip("ABCDE", header)) + "</row>"
                '<row r="3">' + "".join(cell(c, 3, v) for c, v in zip("ABCDE", ["7001", "большой", "газ", "П7", "исправен"])) + "</row>"
                '<row r="4">' + "".join(cell(c, 4, v) for c, v in zip("ABDE", ["7002", "большой", "П7", "исправен"])) + "</row>")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as book:
            book.writestr("xl/workbook.xml", f'<workbook xmlns="{main}"><sheets><sheet name="Автобусы" sheetId="1"/></sheets></workbook>')
            book.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{main}"><sheetData>{rows}</sheetData></worksheet>')
        report = load(workbook=buffer.getvalue(), meta=META)[1]
        line = next(x for x in report["entities"] if x["entity"] == "vehicles")
        self.assertEqual((len(line["matched"]), line["rows"], line["missing"]), (5, 2, []))
        self.assertFalse(any("нет столбцов" in e for e in report["errors"]), report["errors"])
        self.assertTrue(any("строка 4" in e and "Топливо" in e for e in report["errors"]), report["errors"])

    def test_real_world_excel_traits(self):
        """Куски настоящих книг: форматированный текст, листы не по порядку, столбцы дальше Z."""
        main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as book:
            book.writestr("xl/workbook.xml",
                          f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>'
                          f'<sheet name="Первый" sheetId="1" r:id="rId2"/>'
                          f'<sheet name="Второй" sheetId="2" r:id="rId1"/></sheets></workbook>')
            book.writestr("xl/_rels/workbook.xml.rels",
                          '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                          f'<Relationship Id="rId1" Type="{rel}/worksheet" Target="/xl/worksheets/sheet1.xml"/>'
                          f'<Relationship Id="rId2" Type="{rel}/worksheet" Target="worksheets/sheet2.xml"/>'
                          '</Relationships>')
            book.writestr("xl/sharedStrings.xml",
                          f'<sst xmlns="{main}"><si><r><rPr><b/></rPr><t>Гаражный</t></r>'
                          '<r><t xml:space="preserve"> номер</t></r></si><si><t>Класс</t></si></sst>')
            book.writestr("xl/worksheets/sheet1.xml",
                          f'<worksheet xmlns="{main}"><sheetData><row r="1">'
                          '<c r="A1" t="inlineStr"><is><t>x</t></is></c></row></sheetData></worksheet>')
            book.writestr("xl/worksheets/sheet2.xml",
                          f'<worksheet xmlns="{main}"><sheetData>'
                          '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>'
                          '<c r="AA1"><v>7001.0</v></c><c r="AB1" t="b"><v>1</v></c>'
                          '<c r="AC1" t="str"><v>формула</v></c></row></sheetData></worksheet>')
        sheets = registry.read_xlsx(buffer.getvalue())
        self.assertEqual(list(sheets), ["Первый", "Второй"], "порядок листов - как в книге, а не по rId")
        self.assertEqual(sheets["Второй"], [["x"]])
        first = sheets["Первый"][0]
        self.assertEqual(first[:2], ["Гаражный номер", "Класс"], "куски форматированного текста склеены")
        self.assertEqual(len(first), 29)
        self.assertEqual(first[26:], ["7001", "1", "формула"])

    def test_gaps_between_cells_do_not_shift_columns(self):
        sheets = dict(self.SHEETS)
        sheets["Автобусы"] = [["Гаражный номер", "Класс", "Топливо", "Парк", "Тех. состояние"],
                              [7001, "большой", "", "П7", "исправен"],
                              [7002, "большой", "газ", "П7", "исправен"]]
        data, report = load(workbook=workbook(sheets), meta=META)
        self.assertTrue(any("строка 2" in e and "Топливо" in e for e in report["errors"]),
                        report["errors"])


class TestSheetLimits(unittest.TestCase):
    """Крошечный файл не должен раздуваться в памяти из-за номера строки или столбца."""

    MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

    def rows(self, body: str) -> list:
        from xml.etree import ElementTree
        root = ElementTree.fromstring(f'<worksheet xmlns="{self.MAIN}"><sheetData>{body}</sheetData></worksheet>')
        return registry._sheet_rows(root, [])

    def test_far_row_number_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            self.rows('<row r="3000000"><c r="A3000000" t="inlineStr"><is><t>x</t></is></c></row>')
        self.assertIn("3000000", str(caught.exception))
        self.assertIn(str(registry.MAX_SHEET_ROWS), str(caught.exception))

    def test_far_column_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            self.rows('<row r="1"><c r="ZZZZZ1" t="inlineStr"><is><t>x</t></is></c></row>')
        self.assertIn("ZZZZZ1", str(caught.exception))

    def test_ordinary_gaps_still_read(self):
        rows = self.rows('<row r="1"><c r="A1" t="inlineStr"><is><t>a</t></is></c></row>'
                         '<row r="4"><c r="C4" t="inlineStr"><is><t>b</t></is></c></row>')
        self.assertEqual(rows, [["a"], [], [], ["", "", "b"]])


class TestCommandLine(unittest.TestCase):

    def test_folder_reads_and_writes_json(self):
        import contextlib
        import tempfile
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "day.json"
            with contextlib.redirect_stdout(out):
                code = registry.main([str(SAMPLE_CSV), "--date", "2026-10-05", "--out", str(target)])
            self.assertEqual(code, 0, out.getvalue())
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["meta"]["date"],
                             "2026-10-05")
        self.assertIn("движку его отдавать можно", out.getvalue())

    def test_missing_folder_and_bad_registry(self):
        import contextlib
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(registry.main(["нет-такой-папки"]), 1)
        self.assertIn("не прочитан", err.getvalue())


if __name__ == "__main__":
    unittest.main()
