"""
CityDB bridge: Blender panel/operators to fetch/validate/push CityJSON via dockerized citydb-tool.
CityJSON import/export logic remains in the CityJSONEditor core operators.
"""

import os
import shlex
import subprocess
import shutil
import tempfile
from pathlib import Path
from typing import List

import bpy
from bpy.props import (
    BoolProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup
from .core.validation import validate_cityjson

ADDON_ID = (__package__ or __name__).split(".")[0]

HELP_TEXT = [
    "1) Set the workspace folder (mounted as /input in docker).",
    "2) \"Fetch from CityDB\" exports low-LoD (overview) CityJSON, validates with cjio, and loads it.",
    "3) Select a building and run \"Load high LoD for selection\" to fetch detailed geometry.",
    "4) Edit using CityJSONEditor tools.",
    "5) \"Export CityJSON\" writes and validates the file; \"Push to DB\" sends a validated file into CityDB.",
    "Use \"Load defaults\" / \"Save as defaults\" to manage presets.",
]

DEFAULT_WORKDIR = Path.home() / "citydb_bridge"


def _addon_prefs() -> AddonPreferences | None:
    try:
        prefs = bpy.context.preferences
    except Exception:
        return None
    addon = prefs.addons.get(ADDON_ID) if prefs else None
    return addon.preferences if addon else None


def _normalize_path_for_docker(path: Path) -> str:
    return path.as_posix()


def _ensure_dirs(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _mask_password(cmd: List[str], password: str) -> str:
    masked = []
    for part in cmd:
        if password and password in part:
            masked.append(part.replace(password, "******"))
        else:
            masked.append(part)
    return " ".join(shlex.quote(p) for p in masked)


def _ensure_semantic_materials(context) -> None:
    """
    CityJSONEditor expects a material with 'CJEOtype' per mesh to derive semantics.
    If a mesh has no materials (or none tagged), create/assign a default one.
    """
    for obj in context.scene.objects:
        if getattr(obj, "type", None) != "MESH" or not getattr(obj, "data", None):
            continue
        mesh = obj.data
        if not mesh.materials:
            mat = bpy.data.materials.new(name=f"{obj.name}_mat")
            mat["CJEOtype"] = "WallSurface"
            mesh.materials.append(mat)
            for poly in mesh.polygons:
                poly.material_index = 0
            continue
        # Ensure at least one material has CJEOtype
        has_tag = any(("CJEOtype" in m) for m in mesh.materials)
        if not has_tag:
            mesh.materials[0]["CJEOtype"] = "WallSurface"
        # Fix any polygons pointing past the materials array
        for poly in mesh.polygons:
            if poly.material_index >= len(mesh.materials):
                poly.material_index = 0


def _ensure_gmlid_props(context, data: dict) -> None:
    """
    Set a 'gmlid' custom property on imported objects based on CityObject ids so high-LoD fetch can find them.
    """
    cityobjects = data.get("CityObjects", {}) or {}
    # Prefer explicit identifiers from attributes (gmlid/identifier/objectid); otherwise use the CityObject key.
    id_map = {}
    for co_id, co in cityobjects.items():
        attrs = co.get("attributes") or {}
        preferred = attrs.get("gmlid") or attrs.get("identifier") or attrs.get("objectid") or co_id
        id_map[co_id] = preferred
    ids = set(id_map.keys())
    for obj in context.scene.objects:
        if "gmlid" in obj:
            continue
        # Blender may append suffixes like ".001"; match by base name and parents/data.
        base_name = obj.name.split(".")[0]
        candidate_ids = {obj.name, base_name}
        if obj.data and getattr(obj.data, "name", None):
            candidate_ids.add(obj.data.name.split(".")[0])
        if obj.parent:
            candidate_ids.add(obj.parent.name.split(".")[0])
        match = ids.intersection(candidate_ids)
        if match:
            co_id = next(iter(match))
            obj["gmlid"] = id_map.get(co_id, co_id)


def _ensure_texture_keys_in_file(path: Path) -> tuple[bool, str]:
    """
    Ensure every geometry has a 'texture' key to prevent CityJSONEditor import errors.
    Returns (changed, error_message).
    """
    if not path.exists():
        return False, f"File not found: {path}"
    try:
        import json as _json

        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"Could not load JSON: {exc}"

    changed = False
    cityobjects = data.get("CityObjects", {}) or {}
    for obj in cityobjects.values():
        geoms = obj.get("geometry") or []
        for geom in geoms:
            if "texture" not in geom:
                geom["texture"] = {}
                changed = True
    if changed:
        try:
            path.write_text(_json.dumps(data), encoding="utf-8")
        except Exception as exc:  # pragma: no cover - defensive
            return False, f"Failed to write JSON with texture keys: {exc}"
    return changed, ""


def _validate_with_cjio(path: Path) -> tuple[bool, str]:
    """
    Validate CityJSON with cjio if available. Returns (ok, message).
    For CityJSON < 2.0, attempts an in-memory upgrade for validation only.
    """
    env = os.environ.copy()
    version = None
    try:
        with path.open("r", encoding="utf-8") as fh:
            version = (json.load(fh) or {}).get("version")
    except Exception:
        version = None

    cjio_dir = Path(__file__).resolve().parent / "cjio"
    venv_cjio = Path(__file__).resolve().parent.parent / ".venv" / "bin" / "cjio"
    cmd_prefix: List[str] = []
    if "CJIO_BIN" in env and env["CJIO_BIN"]:
        cmd_prefix = [env["CJIO_BIN"]]
    elif cjio_dir.exists():
        env["PYTHONPATH"] = (
            f"{env.get('PYTHONPATH','')}:{cjio_dir.parent.as_posix()}".strip(":")
        )
        py_bin = getattr(bpy.app, "binary_path_python", None) or getattr(bpy.app, "python_executable", None)
        if not py_bin:
            import sys as _sys
            py_bin = _sys.executable
        cmd_prefix = [py_bin, "-m", "cjio"]
    elif venv_cjio.exists():
        cmd_prefix = [str(venv_cjio)]
    else:
        found = shutil.which("cjio")
        if found:
            cmd_prefix = [found]

    if not cmd_prefix:
        return False, "cjio not installed; set CJIO_BIN or install into .venv"

    target = path
    temp_file = None

    # cjio 0.10+ validates only v2.0; upgrade temporary copy if needed.
    if version and version != "2.0":
        temp_file = Path(tempfile.gettempdir()) / f"{path.stem}_cjio_tmp.json"
        upgrade_cmd = cmd_prefix + [str(path), "upgrade", "save", str(temp_file)]
        try:
            upgrade_result = subprocess.run(upgrade_cmd, capture_output=True, text=True, check=False, env=env)
        except Exception as exc:  # pragma: no cover - defensive
            return False, f"Failed to run cjio upgrade: {exc}"
        if upgrade_result.returncode != 0:
            return (
                False,
                upgrade_result.stderr.strip()
                or upgrade_result.stdout.strip()
                or f"cjio upgrade failed with code {upgrade_result.returncode}",
            )
        target = temp_file

    cmd = cmd_prefix + [str(target), "validate"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
    except Exception as exc:  # pragma: no cover - defensive
        return False, f"Failed to run cjio: {exc}"
    finally:
        if temp_file and temp_file.exists():
            try:
                temp_file.unlink()
            except Exception:
                pass

    if result.returncode != 0:
        return False, result.stderr.strip() or result.stdout.strip() or f"cjio exited with {result.returncode}"
    return True, "cjio validation passed."


def _has_texture_data(data: dict) -> bool:
    if "appearance" in data or "appearances" in data or "materials" in data or "textures" in data:
        return True
    cityobjects = data.get("CityObjects", {}) or {}
    for obj in cityobjects.values():
        geoms = obj.get("geometry") or []
        for geom in geoms:
            if geom.get("texture"):
                return True
    return False


def _require_cityjson_editor() -> bool:
    return hasattr(bpy.ops, "cityjson") and hasattr(bpy.ops.cityjson, "import_file") and hasattr(
        bpy.ops.cityjson, "export_file"
    )


class CityDBBridgePreferences(AddonPreferences):
    bl_idname = ADDON_ID

    docker_image: StringProperty(
        name="Docker image",
        description="citydb-tool docker image",
        default="3dcitydb/citydb-tool:latest",
    )
    docker_network: StringProperty(
        name="Docker network",
        description="Docker network to attach to (empty to skip)",
        default="docker_default",
    )
    default_workdir: StringProperty(
        name="Workspace folder",
        description="Local folder mounted into docker as /input",
        subtype="DIR_PATH",
        default=str(DEFAULT_WORKDIR),
    )
    default_import_subdir: StringProperty(
        name="Fetch subfolder",
        description="Relative folder for files exported from CityDB",
        default="from_db",
    )
    default_import_filename: StringProperty(
        name="Fetch filename",
        description="CityJSON filename fetched from CityDB",
        default="from_citydb.json",
    )
    default_high_import_filename: StringProperty(
        name="High-LoD filename",
        description="CityJSON filename for per-building high-LoD fetch",
        default="selected_high.json",
    )
    default_export_subdir: StringProperty(
        name="Push subfolder",
        description="Relative folder for files exported from Blender",
        default="to_db",
    )
    default_export_filename: StringProperty(
        name="Push filename",
        description="CityJSON filename written before sending to CityDB",
        default="to_citydb.json",
    )
    db_host: StringProperty(
        name="DB host",
        description="3DCityDB host",
        default="citydb",
    )
    db_port: IntProperty(
        name="DB port",
        description="3DCityDB port",
        default=5432,
    )
    db_name: StringProperty(
        name="DB name",
        description="3DCityDB database name",
        default="citydb",
    )
    db_schema: StringProperty(
        name="DB schema",
        description="Optional schema name",
        default="",
    )
    db_user: StringProperty(
        name="DB user",
        description="3DCityDB username",
        default="postgres",
    )
    db_password: StringProperty(
        name="DB password",
        description="3DCityDB password",
        subtype="PASSWORD",
        default="postgres",
    )
    low_lods: StringProperty(
        name="Low LoDs",
        description="LoDs used for the overview fetch (comma-separated)",
        default="1",
    )
    high_lods: StringProperty(
        name="High LoDs",
        description="LoDs used when fetching a selected building",
        default="2,3",
    )
    high_sql_template: StringProperty(
        name="High-LoD SQL filter",
        description="SQL subquery returning feature IDs; {gmlid} is replaced with the selected object's gmlid/objectid",
        default="select id from citydb.feature where objectid = '{gmlid}'",
    )
    replace_on_high: BoolProperty(
        name="Replace selection on high-LoD load",
        description="Delete selected objects before loading detailed geometry",
        default=True,
    )
    fallback_on_empty: BoolProperty(
        name="Fallback if empty",
        description="If overview fetch returns no CityObjects, retry with fallback LoDs",
        default=True,
    )
    fallback_lods_low: StringProperty(
        name="Fallback LoDs",
        description="LoDs to try if the overview fetch is empty",
        default="1,2",
    )
    extra_export_args: StringProperty(
        name="Extra export args",
        description="Additional citydb-tool export args (advanced)",
        default="",
    )
    extra_import_args: StringProperty(
        name="Extra import args",
        description="Additional citydb-tool import args (advanced)",
        default="",
    )

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        col.label(text="Defaults (shown in the panel):")
        col.prop(self, "docker_image")
        col.prop(self, "docker_network")
        col.prop(self, "default_workdir")
        col.prop(self, "default_import_subdir")
        col.prop(self, "default_import_filename")
        col.prop(self, "default_high_import_filename")
        col.prop(self, "default_export_subdir")
        col.prop(self, "default_export_filename")
        col.prop(self, "db_host")
        col.prop(self, "db_port")
        col.prop(self, "db_name")
        col.prop(self, "db_schema")
        col.prop(self, "db_user")
        col.prop(self, "db_password")
        col.prop(self, "low_lods")
        col.prop(self, "high_lods")
        col.prop(self, "high_sql_template")
        col.prop(self, "replace_on_high")
        col.prop(self, "fallback_on_empty")
        col.prop(self, "fallback_lods_low")
        col.prop(self, "extra_export_args")
        col.prop(self, "extra_import_args")
        col.label(text="Save Blender preferences to persist these defaults.")


class CityDBBridgeSettings(PropertyGroup):
    working_dir: StringProperty(
        name="Workspace folder",
        subtype="DIR_PATH",
        default=str(DEFAULT_WORKDIR),
    )
    import_subdir: StringProperty(
        name="Fetch subfolder",
        default="from_db",
    )
    import_filename: StringProperty(
        name="Fetch filename",
        default="from_citydb.json",
    )
    high_import_filename: StringProperty(
        name="High-LoD filename",
        default="selected_high.json",
    )
    export_subdir: StringProperty(
        name="Push subfolder",
        default="to_db",
    )
    export_filename: StringProperty(
        name="Push filename",
        default="to_citydb.json",
    )
    db_host: StringProperty(
        name="DB host",
        default="citydb",
    )
    db_port: IntProperty(
        name="DB port",
        default=5432,
    )
    db_name: StringProperty(
        name="DB name",
        default="citydb",
    )
    db_schema: StringProperty(
        name="DB schema",
        default="",
    )
    db_user: StringProperty(
        name="DB user",
        default="postgres",
    )
    db_password: StringProperty(
        name="DB password",
        subtype="PASSWORD",
        default="postgres",
    )
    low_lods: StringProperty(
        name="Low LoDs",
        default="1",
    )
    high_lods: StringProperty(
        name="High LoDs",
        default="2,3",
    )
    high_sql_template: StringProperty(
        name="High-LoD SQL filter",
        description="SQL subquery returning feature IDs; {gmlid} is replaced with the selected object's gmlid/objectid",
        default="select id from citydb.feature where objectid = '{gmlid}'",
    )
    replace_on_high: BoolProperty(
        name="Replace selection on high-LoD load",
        default=True,
    )
    fallback_on_empty: BoolProperty(
        name="Fallback if empty",
        default=True,
    )
    fallback_lods_low: StringProperty(
        name="Fallback LoDs",
        default="1,2",
    )
    docker_network: StringProperty(
        name="Docker network",
        default="docker_default",
    )
    docker_image: StringProperty(
        name="Docker image",
        default="3dcitydb/citydb-tool:latest",
    )
    extra_export_args: StringProperty(
        name="Extra export args",
        default="",
    )
    extra_import_args: StringProperty(
        name="Extra import args",
        default="",
    )
    import_textures: BoolProperty(
        name="Import textures",
        default=True,
    )
    export_textures: BoolProperty(
        name="Export textures",
        default=True,
    )
    last_message: StringProperty(
        name="Last result",
        default="",
        maxlen=2048,
    )


def _sync_from_prefs(settings: CityDBBridgeSettings, prefs: CityDBBridgePreferences) -> None:
    settings.working_dir = prefs.default_workdir
    settings.import_subdir = prefs.default_import_subdir
    settings.import_filename = prefs.default_import_filename
    settings.high_import_filename = prefs.default_high_import_filename
    settings.export_subdir = prefs.default_export_subdir
    settings.export_filename = prefs.default_export_filename
    settings.db_host = prefs.db_host
    settings.db_port = prefs.db_port
    settings.db_name = prefs.db_name
    settings.db_schema = prefs.db_schema
    settings.db_user = prefs.db_user
    settings.db_password = prefs.db_password
    settings.docker_network = prefs.docker_network
    settings.docker_image = prefs.docker_image
    settings.low_lods = prefs.low_lods
    settings.high_lods = prefs.high_lods
    settings.high_sql_template = prefs.high_sql_template
    settings.replace_on_high = prefs.replace_on_high
    settings.fallback_on_empty = prefs.fallback_on_empty
    settings.fallback_lods_low = prefs.fallback_lods_low
    settings.extra_export_args = prefs.extra_export_args
    settings.extra_import_args = prefs.extra_import_args


class CITYDB_OT_LoadDefaults(Operator):
    bl_idname = "citydb_bridge.load_defaults"
    bl_label = "Load defaults"
    bl_description = "Load addon defaults into this .blend"

    def execute(self, context):
        prefs = _addon_prefs()
        if prefs is None:
            self.report({"ERROR"}, "Addon preferences unavailable.")
            return {"CANCELLED"}
        settings = context.scene.citydb_bridge_settings
        _sync_from_prefs(settings, prefs)
        settings.last_message = "Settings loaded from addon defaults."
        self.report({"INFO"}, settings.last_message)
        return {"FINISHED"}


class CITYDB_OT_SaveDefaults(Operator):
    bl_idname = "citydb_bridge.save_defaults"
    bl_label = "Save as defaults"
    bl_description = "Copy current settings into addon defaults (save Blender prefs to persist)"

    def execute(self, context):
        prefs = _addon_prefs()
        if prefs is None:
            self.report({"ERROR"}, "Addon preferences unavailable.")
            return {"CANCELLED"}
        settings = context.scene.citydb_bridge_settings
        prefs.default_workdir = settings.working_dir
        prefs.default_import_subdir = settings.import_subdir
        prefs.default_import_filename = settings.import_filename
        prefs.default_high_import_filename = settings.high_import_filename
        prefs.default_export_subdir = settings.export_subdir
        prefs.default_export_filename = settings.export_filename
        prefs.db_host = settings.db_host
        prefs.db_port = settings.db_port
        prefs.db_name = settings.db_name
        prefs.db_schema = settings.db_schema
        prefs.db_user = settings.db_user
        prefs.db_password = settings.db_password
        prefs.docker_network = settings.docker_network
        prefs.docker_image = settings.docker_image
        prefs.low_lods = settings.low_lods
        prefs.high_lods = settings.high_lods
        prefs.high_sql_template = settings.high_sql_template
        prefs.replace_on_high = settings.replace_on_high
        prefs.fallback_on_empty = settings.fallback_on_empty
        prefs.fallback_lods_low = settings.fallback_lods_low
        prefs.extra_export_args = settings.extra_export_args
        prefs.extra_import_args = settings.extra_import_args
        settings.last_message = "Defaults updated. Save user preferences to keep them."
        self.report({"INFO"}, settings.last_message)
        return {"FINISHED"}


def _validate_settings(settings: CityDBBridgeSettings) -> str:
    missing = []
    if not settings.db_host:
        missing.append("DB host")
    if not settings.db_name:
        missing.append("DB name")
    if not settings.db_user:
        missing.append("DB user")
    if not settings.working_dir:
        missing.append("Workspace folder")
    return ", ".join(missing)


def _build_paths(settings: CityDBBridgeSettings):
    root = Path(settings.working_dir).expanduser().resolve()
    import_dir = root / settings.import_subdir
    high_import = root / settings.import_subdir
    export_dir = root / settings.export_subdir
    _ensure_dirs(import_dir)
    _ensure_dirs(export_dir)
    return {
        "root": root,
        "import_file": import_dir / settings.import_filename,
        "high_import_file": high_import / settings.high_import_filename,
        "export_file": export_dir / settings.export_filename,
    }


def _build_export_command(
    settings: CityDBBridgeSettings,
    output_container_path: str,
    lods: str | None,
    sql_filter: str | None,
) -> List[str]:
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{_normalize_path_for_docker(Path(settings.working_dir).expanduser().resolve())}:/input",
    ]
    if settings.docker_network:
        cmd.extend(["--network", settings.docker_network])
    cmd.extend(
        [
            settings.docker_image,
            "export",
            "cityjson",
            "-H",
            settings.db_host,
        ]
    )
    if settings.db_port:
        cmd.extend(["-P", str(settings.db_port)])
    cmd.extend(["-d", settings.db_name])
    if settings.db_schema:
        cmd.extend(["-S", settings.db_schema])
    cmd.extend(
        [
            "-u",
            settings.db_user,
        ]
    )
    if settings.db_password:
        cmd.extend(["-p", settings.db_password])
    # Force Blender-friendly CityJSON output.
    cmd.extend(["--cityjson-version", "2.0", "--no-json-lines"])
    if lods:
        cmd.extend(["-l", lods])
    if sql_filter:
        cmd.extend(["--sql-filter", sql_filter])
    if settings.extra_export_args:
        cmd.extend(shlex.split(settings.extra_export_args))
    cmd.extend(["-o", output_container_path])
    return cmd


def _build_export_gml_command(
    settings: CityDBBridgeSettings,
    output_container_path: str,
    lods: str | None,
) -> List[str]:
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{_normalize_path_for_docker(Path(settings.working_dir).expanduser().resolve())}:/input",
    ]
    if settings.docker_network:
        cmd.extend(["--network", settings.docker_network])
    cmd.extend(
        [
            settings.docker_image,
            "export",
            "citygml",
            "-H",
            settings.db_host,
        ]
    )
    if settings.db_port:
        cmd.extend(["-P", str(settings.db_port)])
    cmd.extend(["-d", settings.db_name])
    if settings.db_schema:
        cmd.extend(["-S", settings.db_schema])
    cmd.extend(
        [
            "-u",
            settings.db_user,
        ]
    )
    if settings.db_password:
        cmd.extend(["-p", settings.db_password])
    if lods:
        cmd.extend(["-l", lods])
    if settings.extra_export_args:
        cmd.extend(shlex.split(settings.extra_export_args))
    cmd.extend(["-o", output_container_path])
    return cmd


def _run_command(cmd: List[str], password: str) -> subprocess.CompletedProcess:
    display = _mask_password(cmd, password)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Docker not found. Command was: {display}") from exc

    if result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        msg = f"Command failed ({result.returncode}). Command: {display}"
        if stdout:
            msg += f"\nstdout: {stdout}"
        if stderr:
            msg += f"\nstderr: {stderr}"
        raise RuntimeError(msg)
    return result


class CITYDB_OT_FetchFromDB(Operator):
    bl_idname = "citydb_bridge.fetch"
    bl_label = "Fetch from CityDB"
    bl_description = "Export CityJSON from CityDB with docker and load it with CityJSONEditor"

    def execute(self, context):
        wm = getattr(bpy.context, "window_manager", None)
        if wm:
            try:
                wm.progress_begin(0, 3)
            except Exception:
                wm = None
        settings = context.scene.citydb_bridge_settings
        missing = _validate_settings(settings)
        if missing:
            self.report({"ERROR"}, f"Missing required settings: {missing}")
            return {"CANCELLED"}
        if not _require_cityjson_editor():
            self.report({"ERROR"}, "CityJSONEditor add-on must be enabled.")
            return {"CANCELLED"}

        paths = _build_paths(settings)
        target_in_container = f"/input/{settings.import_subdir}/{settings.import_filename}"

        lods = settings.low_lods.strip() or None
        cmd = _build_export_command(settings, target_in_container, lods=lods, sql_filter=None)

        try:
            _run_command(cmd, settings.db_password)
            if wm:
                wm.progress_update(1)
        except RuntimeError as exc:
            settings.last_message = str(exc)
            self.report({"ERROR"}, settings.last_message)
            if wm:
                wm.progress_end()
            return {"CANCELLED"}

        local_file = paths["import_file"]
        if not local_file.exists():
            settings.last_message = f"Export command finished, but file not found: {local_file}"
            self.report({"ERROR"}, settings.last_message)
            if wm:
                wm.progress_end()
            return {"CANCELLED"}

        ok, msg, data = validate_cityjson(local_file)
        if not ok:
            settings.last_message = f"CityJSON file validation failed: {msg}"
            self.report({"ERROR"}, settings.last_message)
            if wm:
                wm.progress_end()
            return {"CANCELLED"}
        val_ok, val_msg = _validate_with_cjio(local_file)
        if not val_ok:
            settings.last_message = f"cjio validation failed: {val_msg}"
            self.report({"ERROR"}, settings.last_message)
            if wm:
                wm.progress_end()
            return {"CANCELLED"}

        if settings.fallback_on_empty and (not data.get("CityObjects")):
            fallback_lods = settings.fallback_lods_low.strip()
            if fallback_lods:
                fallback_cmd = _build_export_command(
                    settings, target_in_container, lods=fallback_lods, sql_filter=None
                )
                try:
                    _run_command(fallback_cmd, settings.db_password)
                    if wm:
                        wm.progress_update(2)
                except RuntimeError as exc:
                    settings.last_message = f"Fallback export failed: {exc}"
                    self.report({"ERROR"}, settings.last_message)
                    if wm:
                        wm.progress_end()
                    return {"CANCELLED"}
                ok, msg, data = validate_cityjson(local_file)
                if not ok or not data.get("CityObjects"):
                    settings.last_message = (
                        f"Overview fetch returned empty even after fallback LoDs ({fallback_lods}). {msg}"
                    )
                    self.report({"ERROR"}, settings.last_message)
                    if wm:
                        wm.progress_end()
                    return {"CANCELLED"}
                val_ok, val_msg = _validate_with_cjio(local_file)
                if not val_ok:
                    settings.last_message = f"cjio validation failed after fallback: {val_msg}"
                    self.report({"ERROR"}, settings.last_message)
                    if wm:
                        wm.progress_end()
                    return {"CANCELLED"}

        op_result = bpy.ops.cityjson.import_file(
            filepath=str(local_file),
            texture_setting=settings.import_textures and _has_texture_data(data),
        )
        if "FINISHED" not in op_result:
            settings.last_message = f"CityJSONEditor import returned: {op_result}"
            self.report({"ERROR"}, settings.last_message)
            if wm:
                wm.progress_end()
            return {"CANCELLED"}

        _ensure_gmlid_props(context, data)
        settings.last_message = f"Loaded {local_file}"
        self.report({"INFO"}, settings.last_message)
        if wm:
            wm.progress_end()
        return {"FINISHED"}


class CITYDB_OT_FetchHighForSelection(Operator):
    bl_idname = "citydb_bridge.fetch_high"
    bl_label = "Load high LoD for selection"
    bl_description = "Fetch higher LoD geometry for the selected building and load it"

    def execute(self, context):
        settings = context.scene.citydb_bridge_settings
        missing = _validate_settings(settings)
        if missing:
            self.report({"ERROR"}, f"Missing required settings: {missing}")
            return {"CANCELLED"}
        if not _require_cityjson_editor():
            self.report({"ERROR"}, "CityJSONEditor add-on must be enabled.")
            return {"CANCELLED"}
        if not context.selected_objects:
            self.report({"ERROR"}, "Select a building object with a gmlid property.")
            return {"CANCELLED"}

        gmlid = context.selected_objects[0].get("gmlid")
        if not gmlid:
            self.report({"ERROR"}, "Selected object is missing a gmlid property.")
            return {"CANCELLED"}

        paths = _build_paths(settings)
        target_in_container = f"/input/{settings.import_subdir}/{settings.high_import_filename}"
        sql_filter = settings.high_sql_template.replace("{gmlid}", str(gmlid))

        lods = settings.high_lods.strip() or None
        cmd = _build_export_command(settings, target_in_container, lods=lods, sql_filter=sql_filter)

        try:
            _run_command(cmd, settings.db_password)
        except RuntimeError as exc:
            settings.last_message = str(exc)
            self.report({"ERROR"}, settings.last_message)
            return {"CANCELLED"}

        local_file = paths["high_import_file"]
        if not local_file.exists():
            settings.last_message = f"High-LoD export finished, but file not found: {local_file}"
            self.report({"ERROR"}, settings.last_message)
            return {"CANCELLED"}

        ok, msg, data = validate_cityjson(local_file)
        if not ok:
            settings.last_message = f"CityJSON file validation failed: {msg}"
            self.report({"ERROR"}, settings.last_message)
            return {"CANCELLED"}
        val_ok, val_msg = _validate_with_cjio(local_file)
        if not val_ok:
            settings.last_message = f"cjio validation failed: {val_msg}"
            self.report({"ERROR"}, settings.last_message)
            return {"CANCELLED"}

        if settings.replace_on_high:
            for obj in list(context.selected_objects):
                bpy.data.objects.remove(obj, do_unlink=True)

        op_result = bpy.ops.cityjson.import_file(
            filepath=str(local_file),
            texture_setting=settings.import_textures and _has_texture_data(data),
        )
        if "FINISHED" not in op_result:
            settings.last_message = f"CityJSONEditor import returned: {op_result}"
            self.report({"ERROR"}, settings.last_message)
            return {"CANCELLED"}

        _ensure_gmlid_props(context, data)
        settings.last_message = f"Loaded high-LoD for gmlid '{gmlid}' from {local_file}"
        self.report({"INFO"}, settings.last_message)
        return {"FINISHED"}


class CITYDB_OT_ExportToFile(Operator):
    bl_idname = "citydb_bridge.export_file"
    bl_label = "Export CityJSON"
    bl_description = "Export CityJSON with CityJSONEditor and validate it with cjio (no DB push)"

    def execute(self, context):
        settings = context.scene.citydb_bridge_settings
        missing = _validate_settings(settings)
        if missing:
            self.report({"ERROR"}, f"Missing required settings: {missing}")
            return {"CANCELLED"}
        if not _require_cityjson_editor():
            self.report({"ERROR"}, "CityJSONEditor add-on must be enabled.")
            return {"CANCELLED"}

        paths = _build_paths(settings)
        mount = f"{_normalize_path_for_docker(paths['root'])}:/input"
        source_in_container = f"/input/{settings.export_subdir}/{settings.export_filename}"

        # Pre-flight: ensure meshes have semantics materials so CityJSONEditor doesn't crash
        _ensure_semantic_materials(context)

        export_result = bpy.ops.cityjson.export_file(
            filepath=str(paths["export_file"]),
            check_existing=False,
            texture_setting=settings.export_textures,
        )
        if "FINISHED" not in export_result:
            settings.last_message = f"CityJSONEditor export returned: {export_result}"
            self.report({"ERROR"}, settings.last_message)
            return {"CANCELLED"}

        changed, err = _ensure_texture_keys_in_file(paths["export_file"])
        if err:
            settings.last_message = f"Post-export fix failed: {err}"
            self.report({"ERROR"}, settings.last_message)
            return {"CANCELLED"}

        ok, vmsg = _validate_with_cjio(paths["export_file"])
        if not ok:
            settings.last_message = f"cjio validation failed: {vmsg}"
            self.report({"ERROR"}, settings.last_message)
            return {"CANCELLED"}

        settings.last_message = f"Exported and validated CityJSON: {paths['export_file']}"
        self.report({"INFO"}, settings.last_message)
        return {"FINISHED"}


class CITYDB_OT_PushToDB(Operator):
    bl_idname = "citydb_bridge.push_to_db"
    bl_label = "Push to DB"
    bl_description = "Import an existing validated CityJSON file into CityDB via docker"

    def execute(self, context):
        settings = context.scene.citydb_bridge_settings
        missing = _validate_settings(settings)
        if missing:
            self.report({"ERROR"}, f"Missing required settings: {missing}")
            return {"CANCELLED"}

        paths = _build_paths(settings)
        export_file = paths["export_file"]
        if not export_file.exists():
            msg = f"Export file not found: {export_file}"
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        ok, vmsg = _validate_with_cjio(export_file)
        if not ok:
            settings.last_message = f"cjio validation failed: {vmsg}"
            self.report({"ERROR"}, settings.last_message)
            return {"CANCELLED"}

        mount = f"{_normalize_path_for_docker(paths['root'])}:/input"
        source_in_container = f"/input/{settings.export_subdir}/{settings.export_filename}"

        cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            mount,
        ]
        if settings.docker_network:
            cmd.extend(["--network", settings.docker_network])
        cmd.extend(
            [
                settings.docker_image,
                "import",
                "cityjson",
                "-H",
                settings.db_host,
            ]
        )
        if settings.db_port:
            cmd.extend(["-P", str(settings.db_port)])
        cmd.extend(["-d", settings.db_name])
        if settings.db_schema:
            cmd.extend(["-S", settings.db_schema])
        cmd.extend(
            [
                "-u",
                settings.db_user,
            ]
        )
        if settings.db_password:
            cmd.extend(["-p", settings.db_password])
        if settings.extra_import_args:
            cmd.extend(shlex.split(settings.extra_import_args))
        cmd.append(source_in_container)

        try:
            _run_command(cmd, settings.db_password)
        except RuntimeError as exc:
            settings.last_message = str(exc)
            self.report({"ERROR"}, settings.last_message)
            return {"CANCELLED"}

        settings.last_message = f"Pushed {export_file} into CityDB."
        self.report({"INFO"}, settings.last_message)
        return {"FINISHED"}


class CITYDB_OT_ValidateFile(Operator):
    bl_idname = "citydb_bridge.validate_file"
    bl_label = "Validate CityJSON (cjio)"
    bl_description = "Validate a CityJSON file with cjio"

    filepath: StringProperty(
        name="CityJSON file",
        description="Path to CityJSON file to validate",
        subtype="FILE_PATH",
        default="",
    )

    def execute(self, context):
        settings = context.scene.citydb_bridge_settings
        target = Path(self.filepath) if self.filepath else _build_paths(settings)["import_file"]
        if not target.exists():
            msg = f"File not found: {target}"
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}
        ok, vmsg = _validate_with_cjio(target)
        if not ok:
            settings.last_message = f"cjio validation failed: {vmsg}"
            self.report({"ERROR"}, settings.last_message)
            return {"CANCELLED"}
        settings.last_message = f"cjio validation passed: {target}"
        self.report({"INFO"}, settings.last_message)
        return {"FINISHED"}


class CITYDB_OT_ExportGMLValidate(Operator):
    bl_idname = "citydb_bridge.export_gml_validate"
    bl_label = "Export GML + Validate"
    bl_description = "Export CityGML from CityDB and validate it with citygml-tools (Docker)"

    def execute(self, context):
        settings = context.scene.citydb_bridge_settings
        missing = _validate_settings(settings)
        if missing:
            self.report({"ERROR"}, f"Missing required settings: {missing}")
            return {"CANCELLED"}

        paths = _build_paths(settings)
        mount = f"{_normalize_path_for_docker(paths['root'])}:/input"
        gml_filename = Path(settings.import_filename).with_suffix(".gml").name
        target_in_container = f"/input/{settings.import_subdir}/{gml_filename}"
        gml_local = paths["import_file"].with_suffix(".gml")

        lods = settings.low_lods.strip() or None
        export_cmd = _build_export_gml_command(settings, target_in_container, lods=lods)

        try:
            _run_command(export_cmd, settings.db_password)
        except RuntimeError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        if not gml_local.exists():
            msg = f"Export finished, but file not found: {gml_local}"
            self.report({"ERROR"}, msg)
            return {"CANCELLED"}

        # Validate with citygml-tools
        validate_cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            mount,
            "ghcr.io/citygml4j/citygml-tools:latest",
            "validate",
            f"/input/{settings.import_subdir}/{gml_filename}",
        ]
        try:
            _run_command(validate_cmd, settings.db_password)
        except RuntimeError as exc:
            self.report({"ERROR"}, f"GML validation failed: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Exported and validated: {gml_local}")
        return {"FINISHED"}


class CITYDB_PT_BridgePanel(Panel):
    bl_label = "CityDB Bridge"
    bl_idname = "CITYDB_PT_bridge"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "CityDB"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.citydb_bridge_settings

        box = layout.box()
        box.label(text="Workspace")
        box.prop(settings, "working_dir")

        row = layout.row(align=True)
        row.operator(CITYDB_OT_LoadDefaults.bl_idname, text="Load defaults", icon="FILE_REFRESH")
        row.operator(CITYDB_OT_SaveDefaults.bl_idname, text="Save defaults", icon="FOLDER_REDIRECT")
        layout.operator("preferences.addon_show", text="Addon preferences").module = ADDON_ID

        box = layout.box()
        box.label(text="CityJSON options")
        box.prop(settings, "import_textures")
        box.prop(settings, "export_textures")
        op_row = box.row(align=True)
        op_row.operator(CITYDB_OT_FetchFromDB.bl_idname, icon="IMPORT")
        op_row.operator(CITYDB_OT_FetchHighForSelection.bl_idname, icon="ZOOM_IN")
        op_row.operator(CITYDB_OT_ValidateFile.bl_idname, icon="CHECKMARK")
        export_row = box.row(align=True)
        export_row.operator(CITYDB_OT_ExportToFile.bl_idname, icon="EXPORT")
        export_row.operator(CITYDB_OT_PushToDB.bl_idname, icon="FILE_TICK")
        box.operator(CITYDB_OT_ExportGMLValidate.bl_idname, icon="FILE_CACHE")

        help_box = layout.box()
        help_box.label(text="Guide")
        for line in HELP_TEXT:
            help_box.label(text=line)

        if settings.last_message:
            layout.separator()
            layout.label(text="Last result:")
            layout.label(text=settings.last_message)


class CITYDB_MT_TopMenu(bpy.types.Menu):
    bl_label = "CityDB Bridge"
    bl_idname = "CITYDB_MT_top_menu"

    def draw(self, context):
        layout = self.layout
        layout.operator(CITYDB_OT_FetchFromDB.bl_idname, icon="IMPORT")
        layout.operator(CITYDB_OT_FetchHighForSelection.bl_idname, icon="ZOOM_IN")
        layout.operator(CITYDB_OT_ExportToFile.bl_idname, icon="EXPORT")
        layout.operator(CITYDB_OT_PushToDB.bl_idname, icon="FILE_TICK")
        layout.operator(CITYDB_OT_ValidateFile.bl_idname, icon="CHECKMARK")
        layout.separator()
        layout.operator("preferences.addon_show", text="Addon preferences").module = ADDON_ID


def _menu_func(self, context):
    self.layout.menu(CITYDB_MT_TopMenu.bl_idname)


def _menu_registered() -> bool:
    try:
        return any(
            getattr(draw, "__name__", "") == _menu_func.__name__
            for draw in bpy.types.VIEW3D_MT_editor_menus._dyn_ui_initialize()
        )
    except Exception:
        return False


classes = (
    CityDBBridgePreferences,
    CityDBBridgeSettings,
    CITYDB_OT_LoadDefaults,
    CITYDB_OT_SaveDefaults,
    CITYDB_OT_FetchFromDB,
    CITYDB_OT_FetchHighForSelection,
    CITYDB_OT_ExportToFile,
    CITYDB_OT_PushToDB,
    CITYDB_OT_ValidateFile,
    CITYDB_OT_ExportGMLValidate,
    CITYDB_PT_BridgePanel,
    CITYDB_MT_TopMenu,
)


def _maybe_sync_defaults():
    prefs = _addon_prefs()
    try:
        scene = getattr(bpy.context, "scene", None)
    except Exception:
        scene = None
    if prefs and scene and hasattr(scene, "citydb_bridge_settings"):
        _sync_from_prefs(scene.citydb_bridge_settings, prefs)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.citydb_bridge_settings = PointerProperty(type=CityDBBridgeSettings)
    _maybe_sync_defaults()
    try:
        if not _menu_registered():
            bpy.types.VIEW3D_MT_editor_menus.append(_menu_func)
    except Exception:
        pass


def unregister():
    try:
        bpy.types.VIEW3D_MT_editor_menus.remove(_menu_func)
    except Exception:
        pass
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    if hasattr(bpy.types, "Scene") and hasattr(bpy.types.Scene, "citydb_bridge_settings"):
        del bpy.types.Scene.citydb_bridge_settings


if __name__ == "__main__":
    register()
