"""Коды набора данных словами: класс автобуса, состояние, график, медосмотр, тип дня.

Тексты нарушений, проверки данных и объяснений видит диспетчер, а наши коды
(big, repair, sick) ему ничего не говорят. Словарь один на весь движок. Модуль
ничего не импортирует, поэтому его берут и naryad.core, и naryad.data, и объяснения.
"""

CLASS_NAMES = {"medium": "средний", "big": "большой", "extra_big": "особо большой"}
CONDITION_NAMES = {"ok": "исправен", "repair": "в ремонте", "maintenance": "на ТО", "accident": "после ДТП"}
SCHEDULE_NAMES = {"work": "работает", "day_off": "выходной по графику", "sick": "на больничном",
                  "vacation": "в отпуске"}
MEDICAL_NAMES = {"passed": "медосмотр пройден", "failed": "не прошёл медосмотр",
                 "pending": "медосмотр ещё не проходил", None: "нет отметки о медосмотре"}
DAY_TYPE_NAMES = {"weekday": "будни", "weekend": "выходные"}


def word(names: dict, code) -> str:
    """Слово для кода. Незнакомый код - как есть: лучше код, чем пропавшее сообщение."""
    return names.get(code, str(code))


def class_name(code) -> str:
    """Класс в кавычках, как в объяснениях: «большой»."""
    return f"«{word(CLASS_NAMES, code)}»"


def class_names(codes) -> str:
    return ", ".join(class_name(code) for code in codes)
