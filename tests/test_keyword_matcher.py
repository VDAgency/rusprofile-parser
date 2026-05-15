"""Тесты Stage 2 — keyword-скоринг."""

import pytest

from src.ai.keyword_matcher import KeywordMatchResult, match_keywords


SITE_TEXT_HOT = """
Мы оптовый поставщик канцтоваров для бизнеса. Работаем со складом
в Подмосковье, доставляем по всей России. Партнёрская программа для
дилеров: специальные цены, маркетинговая поддержка.
"""

SITE_TEXT_COLD = """
Розничный магазин канцтоваров в центре города. Работаем только для
физических лиц. Госзакупки не обслуживаем — это не наш профиль.
"""

SITE_TEXT_AMBIGUOUS = """
Магазин канцелярских товаров. Опт и розница. Доставка по городу.
"""

# Включаем словоформы — реальные клиенты так и делают (LLM в Stage A
# извлекает несколько форм + клиент правит в Mini App).
POS_KW = [
    "опт", "оптовый", "оптовая", "оптовые", "оптовых",
    "склад", "складом", "склады", "со склада",
    "дилер", "дилеров", "дилерская сеть",
    "партнерская программа", "b2b", "корпоративным клиентам",
]
NEG_KW = ["розничный магазин", "только для физлиц", "госзакупки"]


def test_hot_decision():
    res = match_keywords(SITE_TEXT_HOT, POS_KW, NEG_KW)
    assert res.decision == "hot"
    assert len(res.positive_matches) >= 3
    assert len(res.negative_matches) == 0


def test_cold_decision():
    res = match_keywords(SITE_TEXT_COLD, POS_KW, NEG_KW)
    assert res.decision == "cold"
    assert len(res.negative_matches) >= 2
    assert len(res.positive_matches) == 0


def test_ambiguous_needs_llm():
    res = match_keywords(SITE_TEXT_AMBIGUOUS, POS_KW, NEG_KW)
    assert res.decision == "needs_llm"


def test_empty_text():
    res = match_keywords("", POS_KW, NEG_KW)
    assert res.decision == "needs_llm"
    assert res.positive_matches == []
    assert res.negative_matches == []


def test_word_boundary_avoids_false_positives():
    """Ключевое слово 'опт' не должно матчить 'оптимальный', 'оптика'."""
    text = "Оптимальный магазин оптики, только лучшие очки"
    res = match_keywords(text, ["опт"], [])
    assert "опт" not in res.positive_matches


def test_yo_normalization():
    """ё в тексте сайта должна нормализоваться к е, чтобы матчить ключи без ё."""
    text = "Партнёрская программа для дилеров"
    res = match_keywords(text, ["партнерская программа"], [])
    assert "партнерская программа" in res.positive_matches


def test_multi_word_phrase():
    text = "Мы поставляем оптовые партии."
    res = match_keywords(text, ["оптовые партии"], [])
    assert "оптовые партии" in res.positive_matches


def test_thresholds_adjustable():
    """Один позитивный матч и hot_threshold=1 → hot."""
    res = match_keywords(
        "У нас есть склад", ["склад"], [],
        hot_threshold=1, cold_threshold=1,
    )
    assert res.decision == "hot"


def test_returns_dataclass():
    res = match_keywords("текст", [], [])
    assert isinstance(res, KeywordMatchResult)


def test_mixed_pos_and_neg_means_needs_llm():
    """Если есть и позитивные, и негативные совпадения — LLM решает."""
    text = "Оптовые поставки, склад. Только физлицам, госзакупки не делаем."
    res = match_keywords(text, ["опт", "склад"], ["только физлицам", "госзакупки"])
    assert res.decision == "needs_llm"
    assert len(res.positive_matches) > 0
    assert len(res.negative_matches) > 0
