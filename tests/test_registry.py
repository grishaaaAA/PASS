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
                          ("0", "00:00"), ("0.2013888888", "04:50")):
            self.assertEqual(registry._time(raw), want, raw)
        for raw in ("4.50", "утром", "4:70", "9"):
            self.assertIsNone(registry._time(raw), raw)


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

    def test_gaps_between_cells_do_not_shift_columns(self):
        sheets = dict(self.SHEETS)
        sheets["Автобусы"] = [["Гаражный номер", "Класс", "Топливо", "Парк", "Тех. состояние"],
                              [7001, "большой", "", "П7", "исправен"],
                              [7002, "большой", "газ", "П7", "исправен"]]
        data, report = load(workbook=workbook(sheets), meta=META)
        self.assertTrue(any("строка 2" in e and "Топливо" in e for e in report["errors"]),
                        report["errors"])


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
