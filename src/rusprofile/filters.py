"""Формирование URL расширенного поиска Rusprofile.

Реальные имена URL-параметров подтверждены сабмитом формы
``/search-advanced`` (см. ``scripts/diag_filters_final.py`` → лог
``logs/diag_filters_final.log``). Собранный живьём URL выглядит так::

    /search-advanced?query=&state-1=on&46.9=on&okved_strict=on&97,77=on&
      MICRO=MICRO&SMALL=SMALL&12165,12300=on&has_phones=on&has_emails=on&
      has_sites=on&finance_has_actual_year_data=on&finance_revenue_from=…&
      not_defendant=on

Правила, не очевидные из кода сайта:
* Статусы: каждый отдельным параметром ``state-1=on``… ``state-5=on``.
* ОКВЭД: ``<код>=on`` (и ``okved_strict=on`` при непустом наборе кодов).
* Регион: ``<код>=on`` — для Москвы и ряда краёв это *составной* код
  (старая классификация + новая), например ``97,77=on``.
* Правовая форма (ОКОПФ): ``<код>=on`` — для ООО тоже составной
  ``12165,12300=on``.
* МСП: ``<value>=<value>`` — например ``MICRO=MICRO``.
* has_phones/has_emails/has_sites/not_defendant/finance_has_actual_year_data:
  значение ``on`` (не ``1``).
"""

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode

from src.config import RUSPROFILE_BASE_URL


# ---- справочные константы -------------------------------------------------

# Коды статусов компании — суффиксы параметров ``state-N``.
STATUS_ACTIVE = "1"
STATUS_REORG = "2"
STATUS_LIQUIDATING = "3"
STATUS_BANKRUPT = "4"
STATUS_LIQUIDATED = "5"

STATUS_LABELS = {
    STATUS_ACTIVE: "Действующая",
    STATUS_REORG: "В процессе реорганизации",
    STATUS_LIQUIDATING: "В процессе ликвидации",
    STATUS_BANKRUPT: "В процессе банкротства",
    STATUS_LIQUIDATED: "Ликвидированная",
}

# Размер бизнеса — Реестр МСП. name и value равны коду.
MSP_MICRO = "MICRO"
MSP_SMALL = "SMALL"
MSP_MEDIUM = "MEDIUM"
MSP_NO = "NO"

MSP_LABELS = {
    MSP_MICRO: "Микропредприятие",
    MSP_SMALL: "Малое",
    MSP_MEDIUM: "Среднее",
    MSP_NO: "Нет в реестре МСП",
}

# Правовые формы (коды подтверждены по id="okopf-<код>" в форме).
LEGAL_FORM_OOO = "12165,12300"  # ООО — составной код (старая + новая классификация)
LEGAL_FORM_AO = "12200"          # АО — Акционерные общества
LEGAL_FORM_PAO = "12247"         # ПАО — Публичные АО
LEGAL_FORM_NAO = "12267"         # НАО — Непубличные АО
LEGAL_FORM_IP = "50102"          # ИП — Индивидуальные предприниматели

LEGAL_FORM_LABELS = {
    LEGAL_FORM_OOO: "ООО",
    LEGAL_FORM_AO: "АО",
    LEGAL_FORM_PAO: "ПАО",
    LEGAL_FORM_NAO: "НАО",
    LEGAL_FORM_IP: "ИП",
}


# ---- регионы --------------------------------------------------------------
# Коды регионов Rusprofile — чаще всего совпадают с ОКАТО (первые две цифры),
# но для ряда субъектов используется *составной* код «старая,новая»
# (ОКТМО объединил территории). Эти случаи см. в `logs/diag_filters_final.log`.

REGIONS = {
    "01": "Республика Адыгея",
    "02": "Республика Башкортостан",
    "03": "Республика Бурятия",
    "04": "Республика Алтай",
    "05": "Республика Дагестан",
    "06": "Республика Ингушетия",
    "07": "Кабардино-Балкарская Республика",
    "08": "Республика Калмыкия",
    "09": "Карачаево-Черкесская Республика",
    "10": "Республика Карелия",
    "11": "Республика Коми",
    "12": "Республика Марий Эл",
    "13": "Республика Мордовия",
    "14": "Республика Саха (Якутия)",
    "15": "Республика Северная Осетия",
    "16": "Республика Татарстан",
    "17": "Республика Тыва",
    "18": "Удмуртская Республика",
    "19": "Республика Хакасия",
    "20": "Чеченская Республика",
    "21": "Чувашская Республика",
    "22": "Алтайский край",
    "23": "Краснодарский край",
    "24": "Красноярский край",
    "25": "Приморский край",
    "26": "Ставропольский край",
    "27": "Хабаровский край",
    "28": "Амурская область",
    "29": "Архангельская область",
    "30": "Астраханская область",
    "31": "Белгородская область",
    "32": "Брянская область",
    "33": "Владимирская область",
    "34": "Волгоградская область",
    "35": "Вологодская область",
    "36": "Воронежская область",
    "37": "Ивановская область",
    "38": "Иркутская область",
    "39": "Калининградская область",
    "40": "Калужская область",
    "41,82": "Камчатский край",       # составной: 41 + 82 (бывш. Корякский АО)
    "42": "Кемеровская область",
    "43": "Кировская область",
    "44": "Костромская область",
    "45": "Курганская область",
    "46": "Курская область",
    "47": "Ленинградская область",
    "48": "Липецкая область",
    "49": "Магаданская область",
    "50": "Московская область",
    "51": "Мурманская область",
    "52": "Нижегородская область",
    "53": "Новгородская область",
    "54": "Новосибирская область",
    "55": "Омская область",
    "56": "Оренбургская область",
    "57": "Орловская область",
    "58": "Пензенская область",
    "81,59": "Пермский край",         # составной: 81 (бывш. Коми-Пермяцкий АО) + 59
    "60": "Псковская область",
    "61": "Ростовская область",
    "62": "Рязанская область",
    "63": "Самарская область",
    "64": "Саратовская область",
    "65": "Сахалинская область",
    "66": "Свердловская область",
    "67": "Смоленская область",
    "68": "Тамбовская область",
    "69": "Тверская область",
    "70": "Томская область",
    "71": "Тульская область",
    "72": "Тюменская область",
    "73": "Ульяновская область",
    "74": "Челябинская область",
    "75,80": "Забайкальский край",    # составной: 75 + 80 (бывш. Агинский Бурятский АО)
    "76": "Ярославская область",
    "97,77": "Москва",                # составной: 97 (ТиНАО) + 77
    "78": "Санкт-Петербург",
    "79": "Еврейская автономная область",
    "83": "Ненецкий автономный округ",
    "86": "Ханты-Мансийский АО",
    "87": "Чукотский автономный округ",
    "89": "Ямало-Ненецкий АО",
    "91": "Республика Крым",
    "92": "Севастополь",
    "99": "Байконур",
}


# ---- фильтры --------------------------------------------------------------

@dataclass
class SearchFilters:
    """Параметры расширенного поиска Rusprofile.

    Имена скалярных атрибутов намеренно совпадают с URL-параметрами,
    чтобы в ``build_search_url`` не было переименований. Виджеты
    (status/okved/region/msp/okopf) имеют собственную логику маппинга.
    """

    # --- Название ----------------------------------------------------------
    query: Optional[str] = None

    # --- Статус ------------------------------------------------------------
    # Список кодов STATUS_* — каждый уходит в URL отдельным ``state-N=on``.
    status: list[str] = field(default_factory=list)

    # --- ОКВЭД -------------------------------------------------------------
    # Список кодов (``46.9``, ``01.11``). Каждый уходит как ``<код>=on``.
    # При непустом списке автоматически добавляется ``okved_strict=on``,
    # что ограничивает поиск основными видами деятельности.
    okved: list[str] = field(default_factory=list)
    # Можно принудительно отключить strict (по умолчанию — True при наличии ОКВЭД).
    okved_strict: Optional[bool] = None

    # --- Дата регистрации (dd.mm.YYYY или YYYY-MM-DD) --------------------
    date_begin: Optional[str] = None
    date_end: Optional[str] = None

    # --- Регион ------------------------------------------------------------
    # Список кодов (включая составные ``97,77``). Уходят как ``<код>=on``.
    region: list[str] = field(default_factory=list)

    # --- Уставный капитал, руб. -------------------------------------------
    capital_from: Optional[int] = None
    capital_to: Optional[int] = None

    # --- Среднесписочная численность -------------------------------------
    sshr_from: Optional[int] = None
    sshr_to: Optional[int] = None

    # --- Реестр МСП --------------------------------------------------------
    # MICRO/SMALL/MEDIUM/NO. Уходит как ``<value>=<value>`` (а не ``=on``).
    msp: list[str] = field(default_factory=list)

    # --- Правовая форма ----------------------------------------------------
    # Коды LEGAL_FORM_* (включая составной ``12165,12300``).
    okopf: list[str] = field(default_factory=list)

    # --- Контакты ---------------------------------------------------------
    has_phones: bool = False
    has_emails: bool = False
    has_sites: bool = False

    # --- Бухотчётность ----------------------------------------------------
    finance_has_actual_year_data: bool = False

    # --- Выручка, руб. -----------------------------------------------------
    finance_revenue_from: Optional[int] = None
    finance_revenue_to: Optional[int] = None

    # --- Прибыль, руб. ----------------------------------------------------
    finance_profit_from: Optional[int] = None
    finance_profit_to: Optional[int] = None

    # --- Стоимость компании, руб. -----------------------------------------
    finance_value_from: Optional[int] = None
    finance_value_to: Optional[int] = None

    # --- Госзакупки (кол-во контрактов) -----------------------------------
    gz_supplier_cnt_from: Optional[int] = None
    gz_supplier_cnt_to: Optional[int] = None

    # --- Госзакупки (сумма контрактов, руб.) ------------------------------
    gz_all_sum_from: Optional[int] = None
    gz_all_sum_to: Optional[int] = None

    # --- Арбитраж (сумма исков, руб.) -------------------------------------
    arbitr_claim_sum_from: Optional[int] = None
    arbitr_claim_sum_to: Optional[int] = None
    not_defendant: bool = False

    # --- Пагинация --------------------------------------------------------
    page: int = 1


_SCALAR_FIELDS = (
    "query",
    "date_begin",
    "date_end",
    "capital_from",
    "capital_to",
    "sshr_from",
    "sshr_to",
    "finance_revenue_from",
    "finance_revenue_to",
    "finance_profit_from",
    "finance_profit_to",
    "finance_value_from",
    "finance_value_to",
    "gz_supplier_cnt_from",
    "gz_supplier_cnt_to",
    "gz_all_sum_from",
    "gz_all_sum_to",
    "arbitr_claim_sum_from",
    "arbitr_claim_sum_to",
)

# Все булевы флаги уходят со значением ``on`` — это реальный сабмит формы.
_BOOL_FIELDS = (
    "has_phones",
    "has_emails",
    "has_sites",
    "finance_has_actual_year_data",
    "not_defendant",
)


def build_search_url(filters: SearchFilters) -> str:
    """Формирует URL расширенного поиска Rusprofile.

    Порядок параметров несущественен — сервер не чувствителен к нему.
    Пустые значения не отправляем: живая форма их отправляет тоже, но
    это только удлиняет URL и засоряет логи.
    """
    params: list[tuple[str, str]] = []

    if filters.query:
        params.append(("query", filters.query))

    # Статусы — каждый отдельным ключом ``state-N=on``.
    for code in filters.status:
        if code:
            params.append((f"state-{code}", "on"))

    # ОКВЭД — ``<код>=on`` плюс глобальный флаг strict.
    okved_codes = [c for c in filters.okved if c]
    for code in okved_codes:
        params.append((code, "on"))
    if okved_codes:
        # По умолчанию при заданных ОКВЭД включаем strict — так делает сайт.
        strict = True if filters.okved_strict is None else filters.okved_strict
        if strict:
            params.append(("okved_strict", "on"))

    # Регионы — ``<код>=on`` (код может содержать запятую: ``97,77``).
    for code in filters.region:
        if code:
            params.append((code, "on"))

    # Правовая форма — ``<код>=on``.
    for code in filters.okopf:
        if code:
            params.append((code, "on"))

    # МСП — ``<value>=<value>``.
    for code in filters.msp:
        if code:
            params.append((code, code))

    # Скалярные поля (включая query, если уже не добавили — но добавили выше,
    # поэтому исключаем его тут).
    for name in _SCALAR_FIELDS:
        if name == "query":
            continue
        value = getattr(filters, name)
        if value in (None, ""):
            continue
        params.append((name, str(value)))

    # Булевы флаги.
    for name in _BOOL_FIELDS:
        if getattr(filters, name):
            params.append((name, "on"))

    if filters.page > 1:
        params.append(("page", str(filters.page)))

    # Запросы без виджетов и без фильтров — короткий /search; иначе /search-advanced.
    has_widgets_or_filters = any(
        key not in ("query", "page") for key, _ in params
    )
    path = "/search-advanced" if has_widgets_or_filters else "/search"

    if not params:
        return f"{RUSPROFILE_BASE_URL}{path}"

    # ``quote_via`` по умолчанию — ``quote_plus``; запятые в кодах регионов
    # и ОКОПФ URL-кодируются в ``%2C``, что сайт принимает.
    query = urlencode(params, doseq=True)
    return f"{RUSPROFILE_BASE_URL}{path}?{query}"
