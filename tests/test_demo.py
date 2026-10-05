"""Сценарий показа (naryad/demo.py): проходит целиком и печатает главное. Запуск: python -m unittest"""

import contextlib
import io
import unittest

from naryad import demo
from naryad.core.invariants import use_labor_preset


def run(args: list) -> tuple:
    """(код возврата, что напечатано в оба потока)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = demo.main(args)
    return code, out.getvalue() + err.getvalue()


class TestDemo(unittest.TestCase):

    def tearDown(self):
        use_labor_preset(None)  # набор норм ставится на весь процесс

    def test_breakdown_story(self):
        code, text = run([])
        self.assertEqual(code, 0, text)
        for word in ("Автобусный парк №7", "Утренний план", "Варианты замены",
                     "Применяем лучший вариант", "Журнал дня", "08:40"):
            self.assertIn(word, text)
        self.assertIn("Проверка плана: нарушений 0", text)
        self.assertIn("Проверка дня после замены: нарушений 0", text)
        self.assertEqual(text.count("Варианты замены"), 1)

    def test_no_show_step_is_added_before_breakdown(self):
        code, text = run(["--no-show"])
        self.assertEqual(code, 0, text)
        self.assertIn("медосмотр", text)
        self.assertEqual(text.count("Варианты замены"), 2)
        self.assertEqual(text.count("нарушений 0"), 3)  # план и два события
        self.assertLess(text.index("медосмотр"), text.index("сошёл"))

    def test_interval_line_after_the_replacement(self):
        """Ровные интервалы - наш главный довод, он должен быть виден в показе."""
        code, text = run([])
        self.assertEqual(code, 0, text)
        self.assertIn("Интервалы:", text)
        self.assertIn("маршрутов с ростом больше четверти 0", text)
        self.assertIn("без единого автобуса 0", text)
        self.assertLess(text.index("Проверка дня после замены"), text.index("Интервалы:"))

    def test_dispatcher_edit_step(self):
        """Шаг правки показывает подсказку, отказ по нормам и запись в журнал."""
        code, text = run([])
        self.assertEqual(code, 0, text)
        for word in ("Диспетчер правит план сам", "законно можно поставить автобусов",
                     "Правка нарушает нормы и не применена", "одновременно на",
                     "Правка не применена, день не изменился",
                     "Применено, новых нарушений: 0", "правка  Диспетчер поставил автобус"):
            self.assertIn(word, text)
        self.assertLess(text.index("Журнал дня"), text.index("правка  Диспетчер"))
        self.assertLess(text.index("Применяем лучший вариант"), text.index("Диспетчер правит план сам"))

    def test_temporary_breakdown(self):
        code, text = run(["--duration", "20"])
        self.assertEqual(code, 0, text)
        self.assertIn("вернётся через 20 мин", text)

    def test_other_labor_preset(self):
        code, text = run(["--labor", "strict"])
        self.assertEqual(code, 0, text)
        self.assertIn("нормы труда: strict", text)

    def test_clear_errors(self):
        cases = ((["--at", "03:00"], "нет нарядов"),
                 (["--at", "8-40"], "ЧЧ:ММ"),
                 (["--day", "нет-такого-файла.json"], "Нет файла"),
                 (["--labor", "нет"], "неизвестный набор норм"))
        for args, word in cases:
            code, text = run(args)
            self.assertEqual(code, 1, args)
            self.assertIn(word, text, args)


if __name__ == "__main__":
    unittest.main()
