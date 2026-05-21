"""Тесты stub-сервиса оплаты.

Регрессионная страховка: пока эквайринг не подключён,
``is_billing_configured`` должна возвращать False, а
``create_stub_payment_url`` — корректный URL stub-страницы.

После реального подключения ЮKassa эти тесты надо будет переписать.
"""

import pytest

from src.services.payment_service import (
    PaymentNotConfiguredError,
    create_stub_payment_url,
    is_billing_configured,
)


def test_billing_not_configured_yet():
    assert is_billing_configured() is False


def test_stub_url_contains_tariff_basic():
    url = create_stub_payment_url(tariff="basic")
    assert url.startswith("/app/payment_stub.html?")
    assert "tariff=basic" in url


def test_stub_url_contains_tariff_pro():
    url = create_stub_payment_url(tariff="pro")
    assert "tariff=pro" in url


def test_stub_url_includes_tenant_id_when_passed():
    url = create_stub_payment_url(tariff="pro", tenant_id=12345)
    assert "tenant_id=12345" in url


def test_stub_url_omits_tenant_id_when_none():
    url = create_stub_payment_url(tariff="pro", tenant_id=None)
    assert "tenant_id" not in url


def test_stub_url_normalizes_tariff_case():
    url = create_stub_payment_url(tariff="PRO")
    assert "tariff=pro" in url


@pytest.mark.parametrize("bad_tariff", ["trial", "simple", "ai", "unknown", "", "  "])
def test_stub_url_rejects_non_payable_tariffs(bad_tariff):
    with pytest.raises(PaymentNotConfiguredError):
        create_stub_payment_url(tariff=bad_tariff)


def test_stub_url_supports_custom_base():
    url = create_stub_payment_url(
        tariff="basic", base_url="https://parserclients.ru/app/payment_stub.html",
    )
    assert url.startswith("https://parserclients.ru/app/payment_stub.html?")
