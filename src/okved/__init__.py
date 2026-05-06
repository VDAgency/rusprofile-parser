"""Справочник ОКВЭД-2 с поиском по человекочитаемым описаниям.

Используется для подбора кодов под пользовательский запрос вида
«канцтовары», «реклама», «автосалоны» и т. п.; данные — в
``data/okved/okved_enriched.json`` и ``data/okved/okved_targets.json``.
"""

from src.okved.loader import load_handbook, load_presets
from src.okved.search import search_by_text, SearchHit
from src.okved.tree import expand_children, get_parents, find_by_code
from src.okved.presets import get_preset, get_preset_codes

__all__ = [
    "load_handbook",
    "load_presets",
    "search_by_text",
    "SearchHit",
    "expand_children",
    "get_parents",
    "find_by_code",
    "get_preset",
    "get_preset_codes",
]
