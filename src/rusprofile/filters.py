"""Формирование URL с фильтрами для расширенного поиска Rusprofile."""

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlencode

from src.config import RUSPROFILE_BASE_URL


@dataclass
class SearchFilters:
    """Параметры фильтрации для поиска компаний на Rusprofile."""

    # Регион (код региона, например "77" для Москвы)
    region: Optional[str] = None
    # ОКВЭД (код, например "46.90")
    okved: Optional[str] = None
    # Выручка
    revenue_from: Optional[int] = None  # в рублях
    revenue_to: Optional[int] = None
    # Прибыль
    profit_from: Optional[int] = None
    profit_to: Optional[int] = None
    # Тип организации: "ooo", "ao", "ip"
    org_type: Optional[str] = None
    # Статус: "active", "liquidating", "liquidated"
    status: str = "active"
    # Наличие контактов
    has_phone: bool = False
    has_site: bool = False
    has_email: bool = False
    # Размер бизнеса: "micro", "small", "medium", "large"
    business_size: Optional[str] = None
    # Текстовый поиск
    query: Optional[str] = None
    # Страница
    page: int = 1


# Коды регионов РФ
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
    "41": "Камчатский край",
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
    "59": "Пермский край",
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
    "75": "Забайкальский край",
    "76": "Ярославская область",
    "77": "Москва",
    "78": "Санкт-Петербург",
    "79": "Еврейская автономная область",
    "83": "Ненецкий автономный округ",
    "86": "Ханты-Мансийский АО",
    "87": "Чукотский автономный округ",
    "89": "Ямало-Ненецкий АО",
    "91": "Республика Крым",
    "92": "Севастополь",
}


def build_search_url(filters: SearchFilters) -> str:
    """Формирует URL расширенного поиска Rusprofile по фильтрам."""
    params = {}

    if filters.query:
        params["query"] = filters.query
    if filters.region:
        params["region"] = filters.region
    if filters.okved:
        params["okved"] = filters.okved
    if filters.revenue_from:
        params["revenue_from"] = str(filters.revenue_from)
    if filters.revenue_to:
        params["revenue_to"] = str(filters.revenue_to)
    if filters.profit_from:
        params["profit_from"] = str(filters.profit_from)
    if filters.profit_to:
        params["profit_to"] = str(filters.profit_to)
    if filters.org_type:
        params["org_type"] = filters.org_type
    if filters.status:
        params["status"] = filters.status
    if filters.has_phone:
        params["has_phone"] = "1"
    if filters.has_site:
        params["has_site"] = "1"
    if filters.has_email:
        params["has_email"] = "1"
    if filters.business_size:
        params["business_size"] = filters.business_size
    if filters.page > 1:
        params["page"] = str(filters.page)

    url = f"{RUSPROFILE_BASE_URL}/search?{urlencode(params)}"
    return url
