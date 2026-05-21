"""Сервис платежей — заглушка до подключения эквайринга.

ВРЕМЕННАЯ РЕАЛИЗАЦИЯ. Клиент пока не оформил эквайринг (ЮKassa), поэтому
запросы на оплату ведут на статичную страницу-заглушку
``/app/payment_stub.html``. Записи в БД (таблица ``payments``) **не**
создаются — мы вернёмся к этому, когда подключим SDK.

После получения эквайринга:
1. Дополнить файл ``yookassa_client.py``.
2. Добавить ``create_real_payment_for_tariff``, ``process_webhook_*``.
3. Записывать в таблицу ``payments``.
4. Заменить вызовы ``create_stub_payment_url`` на реальные.

См. roadmap_billing_tariffs.md, раздел «Отложено».
"""

from __future__ import annotations

import logging
from urllib.parse import urlencode

from src.db.models import TariffPlan

logger = logging.getLogger(__name__)


# Тарифы, по которым можно «оплатить» (даже заглушкой).
_PAYABLE_TARIFFS = {
    TariffPlan.BASIC.value,
    TariffPlan.PRO.value,
}


class PaymentNotConfiguredError(Exception):
    """Запрос на оплату для тарифа, которого нет в списке платных."""


def is_billing_configured() -> bool:
    """Готов ли биллинг к реальной работе.

    Пока всегда False — это сигнал API и UI: показывать stub,
    а не реальный платёжный flow.
    """
    return False


def create_stub_payment_url(
    *,
    tariff: str,
    tenant_id: int | None = None,
    base_url: str = "/app/payment_stub.html",
) -> str:
    """Формирует URL stub-страницы оплаты.

    Параметры запроса в URL:
    - ``tariff=basic|pro`` — какой тариф «оплачивается»
    - ``tenant_id=...`` — для возможности связаться (опционально)

    Использование: API эндпоинт ``POST /api/billing/create-payment``
    возвращает этот URL как ``confirmation_url``. Mini App открывает
    его через ``tg.openLink`` или ``window.location``.
    """
    tariff_norm = tariff.strip().lower() if tariff else ""
    if tariff_norm not in _PAYABLE_TARIFFS:
        raise PaymentNotConfiguredError(
            f"Tariff '{tariff}' is not payable (allowed: {sorted(_PAYABLE_TARIFFS)})"
        )

    params = {"tariff": tariff_norm}
    if tenant_id is not None:
        params["tenant_id"] = str(tenant_id)

    logger.info(
        "Stub payment URL issued: tariff=%s tenant_id=%s",
        tariff_norm, tenant_id,
    )
    return f"{base_url}?{urlencode(params)}"
