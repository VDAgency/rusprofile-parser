"""Тесты модуля src/okved.

Проверяют целостность справочника, поиск по «живым» запросам,
иерархию и пресет под сувенирку.
"""

import pytest

from src.okved import (
    expand_children,
    find_by_code,
    get_parents,
    get_preset,
    get_preset_codes,
    load_handbook,
    load_presets,
    search_by_text,
)
from src.okved.search import filter_codes_in_handbook


# ---- целостность справочника ---------------------------------------------


def test_handbook_loads_and_not_empty():
    items = load_handbook()
    assert len(items) > 50, "В справочнике должно быть достаточно записей"


def test_every_parent_exists_in_handbook():
    """У каждого кода с непустым parent родитель должен быть в файле."""
    items = load_handbook()
    codes = {item["code"] for item in items}
    missing: list[str] = []
    for item in items:
        parent = item.get("parent")
        if parent and parent not in codes:
            missing.append(f"{item['code']} → parent={parent} не найден")
    assert not missing, "Сломанная иерархия:\n" + "\n".join(missing)


def test_required_fields_present():
    required = {"code", "name", "level", "parent", "section", "is_leaf",
                "description", "keywords"}
    for item in load_handbook():
        missing = required - set(item.keys())
        assert not missing, (
            f"У записи {item.get('code')} нет полей: {missing}"
        )


def test_codes_are_unique():
    codes = [item["code"] for item in load_handbook()]
    assert len(codes) == len(set(codes)), "Дубликаты кодов в справочнике"


# ---- поиск ----------------------------------------------------------------


def _top_codes(query: str, n: int = 5) -> list[str]:
    return [hit.code for hit in search_by_text(query, limit=n)]


def test_search_kanc_returns_46_49_3():
    """«канцтовары» должно вытащить 46.49.3 в топ."""
    top = _top_codes("канцтовары", n=5)
    assert "46.49.3" in top, f"46.49.3 не в топ-5: {top}"


def test_search_advertising_returns_73_11():
    top = _top_codes("реклама", n=5)
    assert "73.11" in top, f"73.11 не в топ-5: {top}"


def test_search_autosalon_returns_45_11():
    top = _top_codes("автосалон", n=5)
    assert "45.11" in top, f"45.11 не в топ-5: {top}"


def test_search_souvenirs_returns_46_49_49():
    top = _top_codes("сувениры", n=5)
    assert "46.49.49" in top, f"46.49.49 не в топ-5: {top}"


def test_search_food_wholesale_returns_46_3():
    """«опт продуктов» должен вытащить продуктовые коды в топ."""
    top = _top_codes("опт продуктов", n=5)
    assert any(c.startswith("46.3") for c in top), (
        f"Нет ни одного 46.3* в топе: {top}"
    )


def test_search_empty_returns_empty():
    assert search_by_text("") == []
    assert search_by_text("   ") == []


def test_search_no_match_returns_empty():
    """Бессмысленный запрос не должен возвращать рандом."""
    assert search_by_text("xyzqwerty12345") == []


def test_search_by_code_prefix():
    """Если ввели код «46» — класс 46 должен быть в топе."""
    top = _top_codes("46", n=3)
    assert "46" in top, f"Класс 46 не в топ-3 при запросе кода: {top}"


# ---- иерархия -------------------------------------------------------------


def test_find_by_code():
    item = find_by_code("46.49.3")
    assert item is not None
    assert "канцеляр" in item["name"].lower()


def test_find_by_code_missing():
    assert find_by_code("99.99.99") is None
    assert find_by_code("") is None


def test_expand_children_46_49():
    """46.49 должно раскрыться в свои подгруппы."""
    children = expand_children("46.49")
    assert "46.49" in children  # сам код
    assert "46.49.3" in children
    assert "46.49.4" in children
    assert "46.49.49" in children


def test_expand_children_class_46():
    """Класс 46 — большое дерево, должен включать 46.4 и 46.49.3 и т. д."""
    children = expand_children("46")
    assert "46.4" in children
    assert "46.49" in children
    assert "46.49.3" in children


def test_expand_children_leaf():
    """Если код-лист — возвращается только он сам."""
    children = expand_children("46.49.3")
    assert children == ["46.49.3"]


def test_get_parents_chain():
    """Цепочка от 46.49.3 до раздела G."""
    parents = get_parents("46.49.3")
    assert parents == ["46.49", "46.4", "46", "G"]


def test_get_parents_for_section():
    """У раздела родителей нет."""
    parents = get_parents("G")
    assert parents == []


# ---- пресеты --------------------------------------------------------------


def test_presets_load():
    presets = load_presets()
    assert len(presets) >= 1
    ids = {p["id"] for p in presets}
    assert "souvenirs_default" in ids


def test_souvenirs_preset_codes_exist():
    """Все коды из пресета 'Сувенирка' должны быть в справочнике."""
    codes = get_preset_codes("souvenirs_default")
    assert codes, "Пресет souvenirs_default не должен быть пустым"
    invalid = [c for c in codes if find_by_code(c) is None]
    assert not invalid, f"В пресете коды, которых нет в справочнике: {invalid}"


def test_corporate_preset_has_no_codes_but_filters():
    """Корпоративный пресет — пустой по кодам, но с фильтрами."""
    preset = get_preset("souvenirs_corporate")
    assert preset is not None
    assert preset.get("codes") == []
    rec = preset.get("recommended_filters") or {}
    assert rec.get("msp") == ["MEDIUM"]
    assert rec.get("finance_revenue_from") == 100000000


def test_get_preset_missing():
    assert get_preset("nonexistent") is None
    assert get_preset_codes("nonexistent") == []


# ---- санитайзер -----------------------------------------------------------


def test_filter_codes_in_handbook():
    valid = filter_codes_in_handbook(["46.49.3", "46.49.49", "99.99.99", ""])
    assert valid == ["46.49.3", "46.49.49"]
