"""Тесты унифицированных уведомлений."""

from unittest.mock import AsyncMock

import pytest

from src.services.notifications import (
    NOTIFICATION_TEMPLATES,
    NotificationKind,
    render,
    send_notification,
)


# ─── render ──────────────────────────────────────────────────────────────


def test_render_trial_granted():
    text = render(
        NotificationKind.TRIAL_GRANTED.value,
        trial_days=7, trial_parses=10,
    )
    assert "7 дней" in text
    assert "10 парсингов" in text


def test_render_payment_success():
    text = render(
        NotificationKind.PAYMENT_SUCCESS.value,
        tariff_name="Pro", expires_at="14.06.2026",
    )
    assert "Pro" in text
    assert "14.06.2026" in text


def test_render_renewal_failed():
    text = render(
        NotificationKind.SUBSCRIPTION_RENEWAL_FAILED.value,
        grace_days=5,
    )
    assert "5 дн." in text


def test_render_raises_for_unknown_kind():
    with pytest.raises(ValueError, match="Unknown"):
        render("not_a_real_kind", x=1)


def test_render_fallback_on_missing_context(caplog):
    """Если в context не хватает ключа — лог warning, шаблон возвращается
    как есть (лучше отправить кривое, чем ничего)."""
    text = render(NotificationKind.TRIAL_GRANTED.value)  # без trial_days/trial_parses
    assert "{trial_days}" in text or text == NOTIFICATION_TEMPLATES[
        NotificationKind.TRIAL_GRANTED.value
    ]


def test_all_kinds_have_templates():
    """Каждый NotificationKind должен иметь шаблон."""
    for kind in NotificationKind:
        assert kind.value in NOTIFICATION_TEMPLATES, (
            f"Missing template for {kind.value}"
        )


# ─── send_notification ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_send_calls_bot_send_message():
    bot = AsyncMock()
    ok = await send_notification(
        bot, telegram_user_id=42,
        kind=NotificationKind.PAYMENT_SUCCESS.value,
        tariff_name="Pro", expires_at="14.06.2026",
    )
    assert ok is True
    bot.send_message.assert_called_once()
    call = bot.send_message.call_args
    assert call.kwargs["chat_id"] == 42
    assert "Pro" in call.kwargs["text"]


@pytest.mark.anyio
async def test_send_returns_false_without_bot():
    ok = await send_notification(
        None, telegram_user_id=42,
        kind=NotificationKind.TRIAL_EXPIRED.value,
    )
    assert ok is False


@pytest.mark.anyio
async def test_send_returns_false_on_unknown_kind():
    bot = AsyncMock()
    ok = await send_notification(bot, 42, "wrong_kind")
    assert ok is False
    bot.send_message.assert_not_called()


@pytest.mark.anyio
async def test_send_handles_bot_exception_gracefully():
    """Telegram может бросить что угодно (блок, удаление чата, rate
    limit) — мы НЕ должны падать."""
    bot = AsyncMock()
    bot.send_message.side_effect = RuntimeError("Forbidden: bot was blocked")
    ok = await send_notification(
        bot, telegram_user_id=42,
        kind=NotificationKind.TRIAL_EXPIRED.value,
    )
    assert ok is False
