"""Навигация по иерархии ОКВЭД: поиск по коду, дочерние/родительские коды.

Все функции работают со справочником, полученным через ``load_handbook``.
"""

from src.okved.loader import load_handbook


def find_by_code(code: str) -> dict | None:
    """Ищет запись по точному коду (с учётом регистра для разделов A–U)."""
    if not code:
        return None
    target = str(code).strip()
    for item in load_handbook():
        if item.get("code") == target:
            return item
    return None


def expand_children(code: str, include_self: bool = True) -> list[str]:
    """Возвращает все коды-потомки заданного.

    Используется когда пользователь выбрал класс/подкласс и хочет
    «забрать всё, что под ним». Например, ``expand_children("46.49")``
    вернёт ``["46.49", "46.49.3", "46.49.4", "46.49.49"]`` (если
    они есть в справочнике).
    """
    if not code:
        return []
    target = str(code).strip()
    items = load_handbook()

    result: list[str] = []
    if include_self and find_by_code(target):
        result.append(target)

    # Стандартный обход: ищем тех, у кого parent совпадает, потом
    # рекурсивно — их потомков. Дерево неглубокое (уровень 1–6),
    # поэтому без оптимизаций.
    queue = [target]
    seen = {target}
    while queue:
        current = queue.pop(0)
        for item in items:
            if item.get("parent") == current and item.get("code") not in seen:
                seen.add(item["code"])
                result.append(item["code"])
                queue.append(item["code"])
    return result


def get_parents(code: str, include_self: bool = False) -> list[str]:
    """Возвращает цепочку родителей кода — от ближайшего к разделу.

    ``get_parents("46.49.3")`` → ``["46.49", "46.4", "46", "G"]``.
    """
    if not code:
        return []
    chain: list[str] = []
    current = find_by_code(str(code).strip())
    if not current:
        return chain
    if include_self:
        chain.append(current["code"])
    while current and current.get("parent"):
        parent_code = current["parent"]
        chain.append(parent_code)
        current = find_by_code(parent_code)
    return chain
