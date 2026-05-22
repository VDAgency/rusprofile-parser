"""Тесты валидации Telegram WebApp initData."""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest


TEST_BOT_TOKEN = "1234567890:TEST_TOKEN_FOR_TESTS"


def _build_init_data(user_id: int, username: str = "tester",
                     auth_date: int | None = None,
                     signature: str | None = None) -> str:
    """Симулирует то, что Telegram пришлёт в Mini App.

    Если ``signature`` передан — она включается в HMAC-проверку,
    как это и делает реальный Telegram Web с 2024 года.
    """
    auth_date = auth_date or int(time.time())
    user = {"id": user_id, "username": username, "first_name": "Test"}
    pairs = {
        "auth_date": str(auth_date),
        "query_id": "AAA-test",
        "user": json.dumps(user, separators=(",", ":")),
    }
    if signature is not None:
        pairs["signature"] = signature
    data_check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs.keys()))
    secret = hmac.new(b"WebAppData", TEST_BOT_TOKEN.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    pairs["hash"] = h
    return urlencode(pairs)


@pytest.fixture
def patched_token(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TEST_BOT_TOKEN)
    import importlib
    import src.config
    import src.api.auth
    importlib.reload(src.config)
    importlib.reload(src.api.auth)
    return src.api.auth


def test_valid_init_data_returns_user_id(patched_token):
    init = _build_init_data(user_id=42, username="vasya")
    assert patched_token.get_user_id(init) == 42


def test_valid_init_data_returns_username(patched_token):
    init = _build_init_data(user_id=42, username="vasya")
    assert patched_token.get_username(init) == "vasya"


def test_tampered_init_data_rejected(patched_token):
    init = _build_init_data(user_id=42)
    # Меняем user_id, hash остаётся старый — должно быть отвергнуто
    tampered = init.replace("%22id%22%3A42", "%22id%22%3A99")
    assert patched_token.get_user_id(tampered) is None


def test_missing_hash_rejected(patched_token):
    init = "auth_date=1700000000&user=%7B%22id%22%3A42%7D"
    assert patched_token.get_user_id(init) is None


def test_expired_init_data_rejected(patched_token):
    very_old = int(time.time()) - 24 * 60 * 60 - 100  # >12 ч назад
    init = _build_init_data(user_id=42, auth_date=very_old)
    assert patched_token.get_user_id(init) is None


def test_empty_init_data_rejected(patched_token):
    assert patched_token.get_user_id("") is None
    assert patched_token.get_user_id(None) is None


def test_signature_field_included_in_hmac(patched_token):
    """Telegram с 2024 добавил в initData поле `signature` (Ed25519,
    для third-party валидации). Оно ВКЛЮЧАЕТСЯ в HMAC-расчёт хеша
    наравне с остальными полями — Telegram учитывает её при подсчёте
    `hash`, и мы тоже должны.

    Этот тест регресс-фиксирует баг с Telegram Web: ранее код делал
    `pairs.pop("signature")` перед расчётом, и initData от Web (где
    signature всегда есть) отвергался с hash mismatch.
    """
    # signature участвовала в подсчёте hash — должно пройти.
    init = _build_init_data(
        user_id=42, username="vasya",
        signature="Ed25519FakeSignature_payload-here",
    )
    assert patched_token.get_user_id(init) == 42


def test_signature_appended_after_hash_is_rejected(patched_token):
    """Защита от подделки: если signature дописана после подсчёта hash —
    HMAC не сойдётся и initData должен быть отвергнут."""
    init = _build_init_data(user_id=42, username="vasya")
    tampered = init + "&signature=fake"
    assert patched_token.get_user_id(tampered) is None


# ─── signed_uid (fallback для клиентов с пустым initData) ─────────────


def test_signed_uid_roundtrip(patched_token):
    """make_signed_uid + verify_signed_uid: тот же uid вернётся обратно."""
    uid, ts, sig = patched_token.make_signed_uid(123456789)
    assert patched_token.verify_signed_uid(uid, ts, sig) == 123456789


def test_signed_uid_accepts_string_inputs(patched_token):
    """UI пришлёт всё как строки из query — verify должен это съесть."""
    uid, ts, sig = patched_token.make_signed_uid(42)
    assert patched_token.verify_signed_uid(str(uid), str(ts), sig) == 42


def test_signed_uid_wrong_signature_rejected(patched_token):
    uid, ts, _sig = patched_token.make_signed_uid(42)
    assert patched_token.verify_signed_uid(uid, ts, "00" * 32) is None


def test_signed_uid_tampered_uid_rejected(patched_token):
    """Подменили uid — sig перестанет совпадать."""
    _uid, ts, sig = patched_token.make_signed_uid(42)
    assert patched_token.verify_signed_uid(99, ts, sig) is None


def test_signed_uid_expired_rejected(patched_token):
    """Подпись старше TTL должна быть отвергнута."""
    ttl = patched_token.SIGNED_UID_TTL_SECONDS
    old_ts = int(time.time()) - ttl - 60
    uid, ts, sig = patched_token.make_signed_uid(42, ts=old_ts)
    assert patched_token.verify_signed_uid(uid, ts, sig) is None


def test_signed_uid_future_rejected(patched_token):
    """Подпись из далёкого будущего — подозрительно, отвергаем (>5 мин)."""
    future_ts = int(time.time()) + 600
    uid, ts, sig = patched_token.make_signed_uid(42, ts=future_ts)
    assert patched_token.verify_signed_uid(uid, ts, sig) is None


def test_signed_uid_missing_parts_rejected(patched_token):
    uid, ts, sig = patched_token.make_signed_uid(42)
    assert patched_token.verify_signed_uid(None, ts, sig) is None
    assert patched_token.verify_signed_uid(uid, None, sig) is None
    assert patched_token.verify_signed_uid(uid, ts, None) is None


def test_signed_uid_non_numeric_rejected(patched_token):
    assert patched_token.verify_signed_uid("abc", "123", "00" * 32) is None
    assert patched_token.verify_signed_uid("42", "abc", "00" * 32) is None
