"""
Shared CityJSON validation/preparation helpers.

This module is used by:
- ImportProcess (before constructing Blender meshes)
- Bridge fetch/high-fetch (lightweight validation before running operators)
- CLI/headless tools (validate_cityjson.py, blender_test_cycle.py)

It performs structural checks in line with CityJSON specs and a few safe normalizations:
- Ensures file is JSON and parses cleanly.
- Normalizes lod fields to numbers (defaults to 0.0 when missing/invalid).
- Verifies semantics exist and are consistent (no auto-fixing).
- Optionally strips textures and ensures texture keys are present to avoid importer crashes.
"""

import json
from pathlib import Path


def _peek_file(path: Path, max_bytes: int = 256) -> str:
    if not path.exists():
        return "<missing>"
    try:
        with path.open("rb") as fh:
            data = fh.read(max_bytes)
        return data.decode("utf-8", errors="replace")
    except Exception as exc:
        return f"<unreadable: {exc}>"


def _ensure_json_file(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return False, f"File does not exist: {path}"
    text = _peek_file(path)
    stripped = text.lstrip()
    if not stripped.startswith("{"):
        hint = ""
        if stripped.startswith("<"):
            hint = " Looks like XML/GML; export must be CityJSON."
        return False, f"File is not JSON (first bytes: {text[:60]!r}) at {path}.{hint}"
    return True, ""


def validate_cityjson(path: Path) -> tuple[bool, str, dict | None]:
    """Validate that the file is JSON and loads as a CityJSON dict (no schema check)."""
    ok, msg = _ensure_json_file(path)
    if not ok:
        return False, msg, None
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:
        return False, f"Could not read CityJSON ({path}): {exc}", None
    return True, "", data


def _normalize_cityjson_lods(data: dict) -> bool:
    """Normalize geometry lod fields to numeric values (string -> float, missing -> 0.0)."""
    changed = False
    cityobjects = data.get("CityObjects", {}) or {}
    for obj in cityobjects.values():
        geoms = obj.get("geometry") or []
        for geom in geoms:
            lod_val = geom.get("lod")
            if isinstance(lod_val, str):
                try:
                    geom["lod"] = float(lod_val)
                    changed = True
                except ValueError:
                    geom["lod"] = 0.0
                    changed = True
            elif lod_val is None:
                geom["lod"] = 0.0
                changed = True
    return changed


def _check_semantics(data: dict) -> tuple[bool, str]:
    """Ensure semantics are consistent when present; semantics are optional in CityJSON."""
    cityobjects = data.get("CityObjects", {}) or {}
    for co_id, obj in cityobjects.items():
        geoms = obj.get("geometry") or []
        for geom in geoms:
            semantics = geom.get("semantics")
            if semantics is None:
                continue  # semantics are optional
            if not isinstance(semantics, dict):
                return False, f"Semantics must be an object in CityObject '{co_id}'."
            values = semantics.get("values")
            surfaces = semantics.get("surfaces")
            if values is not None and (not isinstance(values, list) or (values and not values[0])):
                return False, f"Semantics values invalid for CityObject '{co_id}'."
            if values and not surfaces:
                return False, f"Semantics surfaces missing for CityObject '{co_id}'."
    return True, ""


def _strip_textures(data: dict) -> bool:
    """Strip texture/appearance content when textures are disabled."""
    changed = False
    for key in ["appearance", "appearances", "materials", "textures"]:
        if key in data:
            del data[key]
            changed = True
    cityobjects = data.get("CityObjects", {}) or {}
    for obj in cityobjects.values():
        geoms = obj.get("geometry") or []
        for geom in geoms:
            if "texture" in geom and geom["texture"] != {}:
                geom["texture"] = {}
                changed = True
    return changed


def _ensure_texture_keys(data: dict) -> bool:
    """Ensure every geometry has a 'texture' key to keep the importer stable."""
    changed = False
    cityobjects = data.get("CityObjects", {}) or {}
    for obj in cityobjects.values():
        geoms = obj.get("geometry") or []
        for geom in geoms:
            if "texture" not in geom:
                geom["texture"] = {}
                changed = True
    return changed


def prepare_cityjson_for_import(
    local_file: Path, allow_textures: bool, write_back: bool = False
) -> tuple[bool, str, dict | None, bool]:
    """
    Run structural validation and minimal normalization before importing into Blender.
    Returns the parsed CityJSON and whether it was modified for import.
    If write_back is True, the prepared JSON is persisted to disk when changes occurred.
    """
    ok, msg, data = validate_cityjson(local_file)
    if not ok:
        return False, msg, None, False
    changed = False
    if _normalize_cityjson_lods(data):
        changed = True
    sem_ok, sem_msg = _check_semantics(data)
    if not sem_ok:
        return False, sem_msg, None, False
    if not allow_textures and _strip_textures(data):
        changed = True
    if _ensure_texture_keys(data):
        changed = True
    if changed and write_back:
        try:
            local_file.write_text(json.dumps(data), encoding="utf-8")
        except Exception as exc:
            return False, f"Failed to write prepared CityJSON: {exc}", None, changed
    return True, "", data, changed
