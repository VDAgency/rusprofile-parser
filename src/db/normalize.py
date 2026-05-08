"""Нормализация телефонных номеров.

Для дедупа храним строку из 11 цифр, начинающуюся с `7` (российский
номер). Для вывода в Sheets/Excel конвертируем обратно в формат
`+7 (xxx) xxx-xx-xx`.
"""

import re


_DIGITS_RE = re.compile(r"\d+")


def normalize_phone(phone: str | None) -> str | None:
    """Нормализует телефон в строку из цифр для дедупа.

    Алгоритм:
    1. Оставляем только цифры.
    2. Если 11 цифр и первая `8` — заменяем на `7` (российская локаль).
    3. Если 10 цифр — добавляем `7` в начало (часто оператор пишет
       без кода страны).
    4. Иначе возвращаем как есть, если осталось 11 цифр; короче — None.

    Возвращает None, если на входе мусор/пусто/слишком короткий номер.
    """
    if not phone:
        return None
    digits = "".join(_DIGITS_RE.findall(str(phone)))
    if not digits:
        return None

    if len(digits) == 11 and digits[0] == "8":
        return "7" + digits[1:]
    if len(digits) == 10:
        return "7" + digits
    if len(digits) == 11 and digits[0] == "7":
        return digits

    return None  # для +49... и прочего нерусского формата — не дедупим


def format_phone_for_display(phone_or_normalized: str | None) -> str:
    """Форматирует телефон в `+7 (xxx) xxx-xx-xx` для вывода.

    На вход принимает либо нормализованную форму (11 цифр от `7`),
    либо произвольный формат — пробует нормализовать.
    """
    if not phone_or_normalized:
        return ""
    norm = normalize_phone(phone_or_normalized)
    if not norm or len(norm) != 11:
        return str(phone_or_normalized).strip()
    return f"+{norm[0]} ({norm[1:4]}) {norm[4:7]}-{norm[7:9]}-{norm[9:11]}"
