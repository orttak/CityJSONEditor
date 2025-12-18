from bpy.props import BoolProperty, EnumProperty, StringProperty, IntProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper
from .ImportProcess import ImportProcess

# Import Operator
class ImportCityJSON(Operator, ImportHelper):

    # Operator Metadata
    bl_idname = "cityjson.import_file"
    bl_label = "Import CityJSON"
    filename_ext = ".json"

    filter_glob: StringProperty(
        default="*.json",
        options={'HIDDEN'},
        maxlen=255,  # Max internal buffer length, longer would be clamped.
    )

    # List of Operator properties
    texture_setting: BoolProperty(
        name="Import Textures",
        description="Choose if textures present in the CityJSON file should be imported",
        default=True,
    )
    lod_strategy: EnumProperty(
        name="LoD Selection",
        description="Choose which LoDs to import",
        items=[
            ("ALL", "All", "Import all available LoDs"),
            ("HIGHEST", "Highest", "Import only the highest available LoD per object"),
            ("FILTER", "Filter", "Import only LoDs listed below"),
        ],
        default="ALL",
    )
    lod_filter: StringProperty(
        name="LoDs",
        description="Comma-separated LoDs to import when using 'Filter' (e.g., 1,2.2,3)",
        default="",
    )
    
    # Operator Main Method (Import-Process)
    def execute(self, context):
        importAndParse = ImportProcess(self.filepath, self.texture_setting, self.lod_filter, self.lod_strategy)
        return importAndParse.execute()

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "texture_setting")
        layout.prop(self, "lod_strategy")
        if self.lod_strategy == "FILTER":
            layout.prop(self, "lod_filter")
