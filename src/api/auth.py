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
    данные битые.
    """
    if not init_data:
        return None
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN пустой — initData проверить нечем")
        return None

    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    # auth_date — unix timestamp в строке
    auth_date_raw = pairs.get("auth_date")
    try:
        auth_date = int(auth_date_raw) if auth_date_raw is not None else 0
    except ValueError:
        return None
    if auth_date <= 0:
        return None
    if time.time() - auth_date > INIT_DATA_TTL_SECONDS:
        logger.warning("initData просрочен (auth_date %s)", auth_date)
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
        logger.warning("initData hash mismatch")
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
