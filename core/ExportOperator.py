from bpy.props import BoolProperty, EnumProperty, StringProperty, IntProperty
from bpy.types import Operator
from bpy_extras.io_utils import ExportHelper
from .ExportProcess import ExportProcess

# Import Operator
class ExportCityJSON(Operator, ExportHelper):

    "Export scene as a CityJSON file"
    bl_idname = "cityjson.export_file"
    bl_label = "Export CityJSON"

    # ExportHelper mixin class uses this
    filename_ext = ".json"

    filter_glob: StringProperty(
        default="*.json",
        options={'HIDDEN'},
        maxlen=255,  # Max internal buffer length, longer would be clamped.
    )

    # List of Operator properties
    texture_setting: BoolProperty(
        name="Export Textures",
        description="Choose if textures present in blender should be exported to the CityJSON file",
        default=True,
    )
    patch_baseline: BoolProperty(
        name="Patch baseline (preserve unknown keys)",
        description="If enabled, merge export into stored baseline CityJSON to keep unknown/unedited fields",
        default=False,
    )
    export_changed_only: BoolProperty(
        name="Export only changed objects",
        description="When patching, only replace CityObjects marked dirty or new; keeps others from baseline.",
        default=False,
    )
    skip_failed_exports: BoolProperty(
        name="Skip failed objects",
        description="If an object cannot be exported, skip it and continue; otherwise abort export",
        default=True,
    )

    def execute(self, context):
        CityJSONExport = ExportProcess(self.filepath, self.texture_setting, self.skip_failed_exports, self.patch_baseline, self.export_changed_only)
        return CityJSONExport.execute()
