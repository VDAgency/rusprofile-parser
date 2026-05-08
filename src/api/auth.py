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

    # Telegram с весны 2024 добавил поле `signature` — оно для
    # third-party валидации (Ed25519 подпись от Telegram), и в
    # HMAC-проверке его НЕ учитывают. Исключаем так же, как hash.
    pairs.pop("signature", None)

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
            "initData hash mismatch. Ключи в data_check: %s; age=%.0fs",
            sorted(pairs.keys()), age,
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
