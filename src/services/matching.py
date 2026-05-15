"""Алгоритмы матчинга компаний между источниками.

Используются Stage E (cross-enrichment) и любым модулем, которому нужно
сравнить два названия / телефона / региона. Без I/O — синхронные функции.
"""

from __future__ import annotations

import re

from rapidfuzz import fuzz

# ---------------------------------------------------------------------------
# Названия компаний
# ---------------------------------------------------------------------------

# Префиксы организационно-правовых форм. Их выкидываем перед сравнением:
# «ООО Ромашка» и «Ромашка» — одна компания, а не разные.
_LEGAL_FORM_PATTERNS = [
    r"^\s*ооо\s+",
    r"^\s*оао\s+",
    r"^\s*зао\s+",
    r"^\s*пао\s+",
    r"^\s*нао\s+",
    r"^\s*ао\s+",
    r"^\s*ип\s+",
    r"^\s*ип[\.,]?\s+",
    r"^\s*тоо\s+",
    r"^\s*чоу\s+",
    r"^\s*гбу\s+",
    r"^\s*мбу\s+",
    r"^\s*фгуп\s+",
    r"^\s*некоммерческое\s+партн[её]рство\s+",
    r"^\s*ассоциация\s+",
    r"^\s*фонд\s+",
]

_QUOTES_RE = re.compile(r"[\"'«»„“”']")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_company_name(name: str | None) -> str:
    """Убирает ОПФ-префиксы, кавычки, лишние пробелы. Lowercase.

    >>> normalize_company_name('ООО "Ромашка"')
    'ромашка'
    >>> normalize_company_name('  ОАО   Газпром  ')
    'газпром'
    """
    if not name:
        return ""

    s = name.strip().lower()
    s = _QUOTES_RE.sub(" ", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()

    # Несколько проходов: «ООО Ассоциация Х» → сначала ООО, потом Ассоциация
    changed = True
    while changed:
        changed = False
        for pat in _LEGAL_FORM_PATTERNS:
            new = re.sub(pat, "", s, count=1)
            if new != s:
                s = new
                changed = True
    return _WHITESPACE_RE.sub(" ", s).strip()


def name_similarity(name1: str | None, name2: str | None) -> float:
    """Возвращает 0.0–1.0 по `rapidfuzz.fuzz.ratio` после нормализации.

    Если хотя бы одна сторона пустая — 0.0.
    """
    n1 = normalize_company_name(name1)
    n2 = normalize_company_name(name2)
    if not n1 or not n2:
        return 0.0
    # token_set_ratio устойчивее к перестановкам слов («Ромашка-Сервис»
    # и «Сервис Ромашка»), чем простой ratio.
    return fuzz.token_set_ratio(n1, n2) / 100.0


# ---------------------------------------------------------------------------
# Телефоны → E.164
# ---------------------------------------------------------------------------


def phone_to_e164(phone: str | None, default_region: str = "RU") -> str | None:
    """Приводит произвольный российский телефон к E.164 (`+74951234567`).

    Возвращает None, если разобрать не удалось или номер не похож на
    рабочий. Используется для матчинга через Я.Карты по `+7XXXXXXXXXX`.

    Импорт `phonenumbers` локальный, чтобы модуль грузился быстро в местах,
    где функция не нужна.
    """
    if not phone or not phone.strip():
        return None

    try:
        import phonenumbers
    except ImportError:  # pragma: no cover — зависимость в requirements.txt
        return None

    raw = phone.strip()

    # Если в строке уже есть +, доверяем.
    try:
        if raw.startswith("+"):
            num = phonenumbers.parse(raw, None)
        else:
            num = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException:
        return None

    if not phonenumbers.is_possible_number(num):
        return None
    if not phonenumbers.is_valid_number(num):
        # Допускаем «possible but not valid» — Rusprofile иногда отдаёт
        # городские с хитрым форматом, всё равно матч полезен.
        pass
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


# ---------------------------------------------------------------------------
# Регионы
# ---------------------------------------------------------------------------

# Сокращения / альтернативные написания, которые нужно нормализовать перед
# сравнением. Расширяемый список — добавлять по мере встреченных кейсов.
_REGION_REPLACEMENTS = {
    " обл.": " область",
    " обл ": " область ",
    " респ.": " республика",
    " респ ": " республика ",
    " кр.": " край",
    " кр ": " край ",
    " ао ": " автономный округ ",
    " чукотский ао": " чукотский автономный округ",
    "ё": "е",
}


def _normalize_region(region: str | None) -> str:
    if not region:
        return ""
    s = region.strip().lower()
    s = s.replace(" ", " ")  # неразрывные пробелы
    for src, dst in _REGION_REPLACEMENTS.items():
        s = s.replace(src, dst)
    s = _WHITESPACE_RE.sub(" ", s).strip()
    # «г. москва» / «город москва» / «москва» — приводим к «москва».
    for prefix in ("г. ", "город "):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return s


def regions_match(region1: str | None, region2: str | None) -> bool:
    """Сравнение регионов с учётом альтернативных написаний.

    «Самарская обл.» == «Самарская область» == «самарская область».
    Если хотя бы одна сторона пустая — False (нечем сравнивать).
    """
    r1 = _normalize_region(region1)
    r2 = _normalize_region(region2)
    if not r1 or not r2:
        return False
    if r1 == r2:
        return True
    # Допускаем подстроку: «Самарская область» в строке «Россия, Самарская область».
    if r1 in r2 or r2 in r1:
        return True
    # Высокая схожесть строк (опечатки, разный порядок слов).
    return fuzz.token_set_ratio(r1, r2) >= 90
