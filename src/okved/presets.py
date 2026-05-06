"""Доступ к курируемым пресетам ОКВЭД (см. data/okved/okved_targets.json)."""

from src.okved.loader import load_presets


def get_preset(preset_id: str) -> dict | None:
    """Возвращает пресет по ``id`` или None."""
    if not preset_id:
        return None
    for preset in load_presets():
        if preset.get("id") == preset_id:
            return preset
    return None


def get_preset_codes(preset_id: str) -> list[str]:
    """Возвращает список кодов ОКВЭД из пресета (пустой, если нет)."""
    preset = get_preset(preset_id)
    if not preset:
        return []
    return list(preset.get("codes") or [])
