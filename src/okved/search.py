"""Поиск кодов ОКВЭД по человекочитаемому запросу.

Алгоритм простой и предсказуемый, без ML и стемминга:

* Запрос приводим к нижнему регистру и режем по пробелам/пунктуации.
* Для каждого кода считаем score:
    * +5  за каждый токен запроса, целиком входящий в `keywords`;
    * +3  за вхождение токена в `description` (подстрока);
    * +2  за вхождение в официальное `name`;
    * +4  за совпадение префикса самого `code` (например, ввели «46»);
    * +1  бонус, если запись — лист (`is_leaf=true`), чтобы более
          специфичные коды вытаскивались выше широких.
* Сортируем по score (по убыванию), обрезаем по limit.

Если потом качество окажется недостаточным — заменим на RapidFuzz,
pymorphy2 или простую схему с инвертированным индексом. Сейчас
~250 записей, наивный обход дешевле, чем построение индекса.
"""

import re
from dataclasses import dataclass
from typing import Iterable

from src.okved.loader import load_handbook


@dataclass
class SearchHit:
    """Один результат поиска."""

    code: str
    name: str
    description: str
    level: int
    is_leaf: bool
    section: str
    section_name: str
    score: float


_TOKEN_RE = re.compile(r"[\w\-./]+", flags=re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """Простая токенизация: нижний регистр + слова с буквами/цифрами."""
    if not text:
        return []
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def _score_item(item: dict, query_tokens: list[str], raw_query: str) -> float:
    """Считает релевантность одной записи запросу.

    Веса (выверены тестами):
    * +15 — пользователь ввёл прямо ``code`` записи.
    * +12 — вся фраза запроса = одному из ``keywords`` целиком.
    *  +4 — фраза запроса — подстрока какого-то ``keyword``.
    * +10 — токен запроса полностью совпадает с одним из ``keywords``.
    *  +5 — токен — отдельное слово внутри ``keyword``.
    *  +3 — префиксное совпадение токена со словом из ``keywords``
            (даёт работать русской морфологии: «сувениры» ↔ «сувениров»).
    *  +4 — совпал префикс самого ``code`` («46» → 46.31, 46.49 …).
    *  +2 — токен встречается в ``description``.
    *  +1 — токен встречается в ``name``.
    Бонусы (только если score > 0):
    *  +2 — запись — лист (``is_leaf``).
    *  +1 — уровень 5–6 (специфичные подгруппы/виды).
    *  +0.5 — уровень 4 (группа).
    """
    if not query_tokens:
        return 0.0

    name = (item.get("name") or "").lower()
    description = (item.get("description") or "").lower()
    code = (item.get("code") or "").lower()
    keywords = [str(k).lower() for k in (item.get("keywords") or [])]
    keyword_words: set[str] = set()
    for kw in keywords:
        keyword_words.update(kw.split())

    score = 0.0

    raw = raw_query.strip().lower()
    if raw and len(raw) >= 3:
        if any(raw == kw for kw in keywords):
            score += 12.0
        elif any(raw in kw for kw in keywords):
            score += 4.0

    for token in query_tokens:
        if not token or len(token) < 2:
            continue

        if code == token:
            score += 15.0
            continue

        if code and code.startswith(token + "."):
            score += 4.0

        if any(token == kw for kw in keywords):
            score += 10.0
        elif token in keyword_words:
            score += 5.0
        elif len(token) >= 4:
            threshold = max(4, len(token) - 2)
            matched = False
            for w in keyword_words:
                if len(w) < threshold:
                    continue
                if _common_prefix_len(token, w) >= threshold:
                    matched = True
                    break
            if matched:
                score += 3.0

        if token in description:
            score += 2.0
        if token in name:
            score += 1.0

    if score <= 0:
        return 0.0

    if item.get("is_leaf"):
        score += 2.0
    level = int(item.get("level") or 0)
    if level >= 5:
        score += 1.0
    elif level == 4:
        score += 0.5

    return score


def search_by_text(query: str, limit: int = 20) -> list[SearchHit]:
    """Ищет коды ОКВЭД по человекочитаемому запросу.

    Возвращает не более ``limit`` записей, отсортированных по
    релевантности. При пустом запросе — пустой список.
    """
    query = (query or "").strip()
    if not query:
        return []

    tokens = _tokenize(query)
    if not tokens:
        return []

    scored: list[tuple[float, dict]] = []
    for item in load_handbook():
        score = _score_item(item, tokens, query)
        if score > 0:
            scored.append((score, item))

    scored.sort(key=lambda pair: (-pair[0], pair[1].get("code") or ""))

    hits: list[SearchHit] = []
    for score, item in scored[:limit]:
        hits.append(
            SearchHit(
                code=item.get("code", ""),
                name=item.get("name", ""),
                description=item.get("description", ""),
                level=int(item.get("level") or 0),
                is_leaf=bool(item.get("is_leaf")),
                section=item.get("section", ""),
                section_name=item.get("section_name", ""),
                score=score,
            )
        )
    return hits


def filter_codes_in_handbook(codes: Iterable[str]) -> list[str]:
    """Возвращает только те коды, которые есть в справочнике.

    Пригождается для валидации ввода с фронтенда: если пользователь
    как-то прислал несуществующий код, мы его отбросим.
    """
    handbook = {item.get("code") for item in load_handbook()}
    return [c for c in codes if c and c in handbook]
