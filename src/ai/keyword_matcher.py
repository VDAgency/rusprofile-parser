"""Stage 2 — keyword-скоринг текста сайта против ИИ-профиля.

Без I/O, без LLM. Решает «горячий / холодный / нужен LLM» по простым
правилам:

* `≥ KW_HOT_MIN` позитивных матчей и **0** негативных → ``hot``.
* `≥ KW_COLD_MIN` негативных матчей и **0** позитивных → ``cold``.
* Всё остальное → ``needs_llm``.

Текст и ключевые слова нормализуются (lowercase, ё→е, не-буквенно-цифровые
заменяются пробелами). Совпадение — по границам слов через `regex` (а не
встроенный `re`), чтобы `\b` корректно работал с кириллицей.
"""

from __future__ import annotations

from dataclasses import dataclass

import regex


@dataclass
class KeywordMatchResult:
    positive_matches: list[str]
    negative_matches: list[str]
    decision: str          # "hot" / "cold" / "needs_llm"
    decision_reason: str


def _normalize(text: str) -> str:
    if not text:
        return ""
    return text.lower().replace("ё", "е")


def _strip_to_words(text: str) -> str:
    """Заменяет всё не-буквенно-цифровое на пробел, убирает лишние пробелы."""
    return regex.sub(r"\s+", " ", regex.sub(r"[^\p{L}\p{N}]+", " ", text)).strip()


def _matches_in_text(text_norm: str, keyword: str) -> bool:
    """Поиск ключевого слова с учётом границ слов через regex.\b.

    Multi-word ключи («оптовые поставки») ищем как фразу с границами
    вокруг всей фразы. Для `\b` в `regex` поддерживаются юникодные
    word-chars (включая кириллицу).
    """
    kw = _normalize(keyword).strip()
    if not kw:
        return False
    # Эскейпим пунктуацию пользователя, но пробелы оставляем гибкими.
    parts = [regex.escape(part) for part in kw.split()]
    pattern = r"\b" + r"\s+".join(parts) + r"\b"
    return bool(regex.search(pattern, text_norm))


def match_keywords(
    site_text: str | None,
    keywords_positive: list[str] | None,
    keywords_negative: list[str] | None,
    *,
    hot_threshold: int = 3,
    cold_threshold: int = 2,
) -> KeywordMatchResult:
    """Сравнить текст сайта с ключевыми словами профиля.

    Возвращает агрегаты + готовое решение для qualify_service.
    """
    text_norm = _normalize(site_text or "")
    text_norm = _strip_to_words(text_norm)

    pos_kws = keywords_positive or []
    neg_kws = keywords_negative or []

    positive: list[str] = []
    for kw in pos_kws:
        if _matches_in_text(text_norm, kw):
            positive.append(kw)
    negative: list[str] = []
    for kw in neg_kws:
        if _matches_in_text(text_norm, kw):
            negative.append(kw)

    if len(positive) >= hot_threshold and len(negative) == 0:
        return KeywordMatchResult(
            positive_matches=positive,
            negative_matches=negative,
            decision="hot",
            decision_reason=(
                f"{len(positive)} positive, 0 negative → above hot threshold"
            ),
        )
    if len(negative) >= cold_threshold and len(positive) == 0:
        return KeywordMatchResult(
            positive_matches=positive,
            negative_matches=negative,
            decision="cold",
            decision_reason=(
                f"{len(negative)} negative, 0 positive → above cold threshold"
            ),
        )
    return KeywordMatchResult(
        positive_matches=positive,
        negative_matches=negative,
        decision="needs_llm",
        decision_reason=(
            f"{len(positive)} positive, {len(negative)} negative → ambiguous"
        ),
    )
