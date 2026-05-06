"""Модель данных и утилиты извлечения полей карточки Яндекс Карт."""

from dataclasses import dataclass


@dataclass
class YandexPlace:
    """Данные об организации с Яндекс Карт."""

    name: str = ""
    categories: str = ""       # "Стоматология, Медицинский центр"
    region: str = ""           # запрошенный регион (как ввёл пользователь)
    address: str = ""
    phone: str = ""
    site: str = ""
    rating: str = ""           # "4.5"
    reviews_count: str = ""    # "128" (только число)
    hours: str = ""            # "Пн-Пт 10:00-20:00"
    coordinates: str = ""      # "55.7558,37.6173" (lat,lon)
    yandex_url: str = ""       # прямая ссылка на карточку
    parse_date: str = ""       # "21.04.2026"

    def to_row(self) -> list[str]:
        """Конвертирует в строку для Google Sheets (порядок — YANDEX_SHEET_HEADERS)."""
        # Префикс ' нужен телефону, иначе Google Sheets со значением
        # value_input_option=USER_ENTERED трактует "+7..." как формулу
        # и показывает #ERROR! в ячейке. Апостроф форсирует текст.
        phone = f"'{self.phone}" if self.phone else ""
        return [
            self.name,
            self.categories,
            self.region,
            self.address,
            phone,
            self.site,
            self.rating,
            self.reviews_count,
            self.hours,
            self.coordinates,
            self.yandex_url,
            self.parse_date,
        ]
