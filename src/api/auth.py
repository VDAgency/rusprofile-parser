"""Валидация Telegram WebApp initData.

Алгоритм описан в официальной доке:
https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app

Mini App при открытии получает строку initData (`window.Telegram.WebApp.initData`),
которая содержит подпись HMAC-SHA256 от секретного ключа бота. Сервер
повторяет вычисление и сравнивает.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from urllib.parse import parse_qsl

from src.config import TELEGRAM_BOT_TOKEN

logger = logging.getLogger(__name__)

# Срок действия initData. Telegram рекомендует ≤24 ч; мы — 12 ч.
INIT_DATA_TTL_SECONDS = 12 * 60 * 60


def parse_init_data(init_data: str) -> dict | None:
    """Проверяет подпись initData и возвращает словарь полей.

    Возвращает None, если подпись неверна, истёк срок действия или
    данные битые. Подробная причина логируется в WARNING — без
    самого hash, чтобы не утекало в логи.
    """
    if not init_data:
        logger.warning("initData отсутствует")
        return None
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN пустой — initData проверить нечем")
        return None

    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception as e:
        logger.warning("initData parse_qsl упал: %s", e)
        return None

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        logger.warning(
            "initData без поля hash. Полученные ключи: %s", sorted(pairs.keys())
        )
        return None

    # ВАЖНО: поле `signature` (появилось весной 2024 для third-party
    # валидации через Ed25519) ВКЛЮЧАЕТСЯ в HMAC-проверку — Telegram
    # его учитывает при подсчёте hash. Не исключать!
    # См. aiogram.utils.web_app.check_webapp_signature как референс.

    # auth_date — unix timestamp в строке
    auth_date_raw = pairs.get("auth_date")
    try:
        auth_date = int(auth_date_raw) if auth_date_raw is not None else 0
    except ValueError:
        logger.warning("initData auth_date не int: %r", auth_date_raw)
        return None
    if auth_date <= 0:
        logger.warning("initData без корректного auth_date")
        return None
    age = time.time() - auth_date
    if age > INIT_DATA_TTL_SECONDS:
        logger.warning("initData просрочен (age=%.0fs > %ds)", age, INIT_DATA_TTL_SECONDS)
        return None

    data_check = "\n".join(
        f"{k}={pairs[k]}" for k in sorted(pairs.keys())
    )
    secret_key = hmac.new(
        b"WebAppData", TELEGRAM_BOT_TOKEN.encode("utf-8"), hashlib.sha256
    ).digest()
    expected = hmac.new(
        secret_key, data_check.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, received_hash):
        logger.warning(
            "initData hash mismatch. Ключи в data_check: %s; age=%.0fs; "
            "has_signature=%s",
            sorted(pairs.keys()), age, "signature" in pairs,
        )
        return None

    # Распаковываем user из JSON, если есть.
    user_raw = pairs.get("user")
    if user_raw:
        try:
            pairs["user"] = json.loads(user_raw)
        except json.JSONDecodeError:
            pass
    return pairs


def get_user_id(init_data: str) -> int | None:
    parsed = parse_init_data(init_data)
    if not parsed:
        return None
    user = parsed.get("user")
    if isinstance(user, dict):
        uid = user.get("id")
        if uid:
            try:
                return int(uid)
            except (TypeError, ValueError):
                return None
    return None


def get_username(init_data: str) -> str | None:
    parsed = parse_init_data(init_data)
    if not parsed:
        return None
    user = parsed.get("user")
    if isinstance(user, dict):
        return user.get("username")
    return None


# ---------------------------------------------------------------------------
# Signed UID — независимая от Telegram initData аутентификация.
#
# Зачем: у части Telegram-клиентов (Web/Desktop под Windows/macOS)
# `tg.initData` приходит пустым, и сервер не может верифицировать
# user_id штатным путём. Мы выдаём пользователю одноразовую подписанную
# ссылку на Mini App вида:
#
#   https://parserclients.ru/app/?signed_uid=<id>&ts=<unix>&sig=<hex>
#
# Бот генерирует её под каждого user_id перед отправкой кнопки
# WebApp (см. `bot.handlers._webapp_url_for`). UI читает три параметра
# из location.search и шлёт их в каждый /api/-запрос как query.
# Сервер пересчитывает HMAC и сравнивает.
#
# Подпись = HMAC-SHA256(TELEGRAM_BOT_TOKEN, f"{user_id}:{ts}").hexdigest()
# TTL = 30 дней (баланс безопасности и удобства: пользователь может
# вернуться к боту через неделю и старая ссылка ещё работает).
# ---------------------------------------------------------------------------

SIGNED_UID_TTL_SECONDS = 30 * 24 * 60 * 60


def make_signed_uid(user_id: int, ts: int | None = None) -> tuple[int, int, str]:
    """Возвращает (user_id, ts, sig) для встраивания в URL Mini App."""
    if ts is None:
        ts = int(time.time())
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN пустой — подписать нечем")
    payload = f"{int(user_id)}:{int(ts)}".encode("utf-8")
    sig = hmac.new(
        TELEGRAM_BOT_TOKEN.encode("utf-8"), payload, hashlib.sha256,
    ).hexdigest()
    return int(user_id), int(ts), sig


def verify_signed_uid(
    signed_uid: str | int | None,
    ts: str | int | None,
    sig: str | None,
) -> int | None:
    """Проверяет HMAC. Возвращает user_id (int) или None.

    Логирует причину отказа в WARNING. Не логирует сам sig.
    """
    if not signed_uid or not ts or not sig:
        return None
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN пустой — signed_uid проверить нечем")
        return None
    try:
        uid_int = int(signed_uid)
        ts_int = int(ts)
    except (TypeError, ValueError):
        logger.warning("signed_uid/ts не int: uid=%r ts=%r", signed_uid, ts)
        return None
    if uid_int <= 0 or ts_int <= 0:
        return None

    age = time.time() - ts_int
    if age > SIGNED_UID_TTL_SECONDS:
        logger.warning(
            "signed_uid просрочен: uid=%d age=%.0fs > %ds",
            uid_int, age, SIGNED_UID_TTL_SECONDS,
        )
        return None
    if age < -300:
        # Допускаем 5 минут расхождения времени; больше — подозрительно.
        logger.warning("signed_uid из будущего: uid=%d age=%.0fs", uid_int, age)
        return None

    payload = f"{uid_int}:{ts_int}".encode("utf-8")
    expected = hmac.new(
        TELEGRAM_BOT_TOKEN.encode("utf-8"), payload, hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, sig):
        logger.warning("signed_uid hash mismatch: uid=%d", uid_int)
        return None
    return uid_int
