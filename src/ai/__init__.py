"""Пакет ИИ-квалификации (Этап 2 v3).

Содержит:
- ``scoring``      — Stage 0 (хард-фильтры + универсальный сорт-скор)
- ``profile_extractor`` — Stage A (LLM Call №1, бриф → профиль)
- ``website_extractor`` — Stage 1 (текст сайта)
- ``keyword_matcher``   — Stage 2 (keyword-скоринг)
- ``qualifier``         — Stage 3 (LLM Call №2, оценка компании)
- ``llm_client``        — обёртка над OpenAI/OpenRouter
"""
