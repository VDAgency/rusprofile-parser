"""Модель данных и утилиты извлечения полей карточки Яндекс Карт."""

from dataclasses import dataclass
from datetime import date


@dataclass
class YandexPlace:
    """Данные об организации с Яндекс Карт."""

    name: str = ""
    categories: str = ""       # "Стоматология, Медицинский центр"
    region: str = ""           # запрошенный регион (как ввёл пользователь)
    address: str = ""
    phone: str = ""
    email: str = ""
    site: str = ""
    rating: str = ""           # "4.5"
    reviews_count: str = ""    # "128" (только число)
    hours: str = ""            # "Пн-Пт 10:00-20:00"
    coordinates: str = ""      # "55.7558,37.6173" (lat,lon)
    yandex_url: str = ""       # прямая ссылка на карточку
    parse_date: str = ""       # "21.04.2026"

    # ─── Этап 2 v3: дополнительные сигналы для скоринга ───────────────
    last_review_date: date | None = None
    operating_status: str | None = None  # working / temporarily_closed / permanently_closed
    hours_filled: bool = False
    coordinates_filled: bool = False

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
            self.email,
            self.site,
            self.rating,
            self.reviews_count,
            self.hours,
            self.coordinates,
            self.yandex_url,
            self.parse_date,
        ]

    # ─── Хелперы для скоринга ────────────────────────────────────────
    def yandex_signals_dict(self) -> dict:
        """Свёртка всех yandex_*-полей одним словарём — для записи
        в `Company.raw_json` или для прямого присваивания в qualify_service.

        Преобразует строковый rating/reviews_count в числа, иначе None.
        """
        def _safe_float(v: str) -> float | None:
            try:
                return float(v.replace(",", ".")) if v else None
            except ValueError:
                return None

        def _safe_int(v: str) -> int | None:
            try:
                return int(v) if v else None
            except ValueError:
                return None

        return {
            "yandex_url": self.yandex_url or None,
            "yandex_rating": _safe_float(self.rating),
            "yandex_reviews_count": _safe_int(self.reviews_count),
            "yandex_last_review_date": self.last_review_date,
            "yandex_hours_filled": self.hours_filled,
            "yandex_coordinates_filled": self.coordinates_filled,
            "yandex_operating_status": self.operating_status,
        }
