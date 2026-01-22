# Addon Metadata
bl_info = {
    "name": "CityJSONEditor Bridge",
    "author": "Konstantinos Mastorakis, Tim Balschmiter, Hagen Schoenkaese, Mert Cakir",
    "version": (2, 2, 0),
    "blender": (3, 5, 1),
    "location": "File > Import/Export > CityJSON (.json) || View3D > Sidebar > CityDB",
    "description": "CityJSONEditor fork with integrated CityDB bridge for spec-compliant CityJSON import/export",
    "warning": "",
    "wiki_url": "",
    "category": "Import-Export",
}

import bpy
from .core.ImportOperator import ImportCityJSON
from .core.ExportOperator import ExportCityJSON
from .core import EditMenu, ObjectMenu
from .core import properties, lod3_operators
from . import bridge




classes = (
    # Import Operator
    ImportCityJSON,
    # Export Operator
    ExportCityJSON,
    # EditMode Menu
    EditMenu.SetSurfaceOperator,
    EditMenu.VIEW3D_MT_cityedit_mesh_context_submenu,
    # EditMenu.CalculateSemanticsOperator,
    # ObjectMode Menu
    ObjectMenu.SetConstructionOperator,
    ObjectMenu.VIEW3D_MT_cityobject_construction_submenu,
    ObjectMenu.SetAttributes,
    ObjectMenu.CalculateSemanticsOperator,
    ObjectMenu.SetActiveLODOperator,
    ObjectMenu.VIEW3D_MT_cityobject_lod_submenu,
    # 🆕 LOD3 Tools
    properties.CityJSONEditorSettings,
    lod3_operators.CITYJSON_OT_place_window_modal,

)

def menu_func_import(self, context):
    """Defines the menu item for CityJSON import"""
    self.layout.operator(ImportCityJSON.bl_idname, text="CityJSON (.json)")

def menu_func_export(self, context):
    """Defines the menu item for CityJSON export"""
    self.layout.operator(ExportCityJSON.bl_idname, text="CityJSON (.json)")

def objectmenu_func(self, context):
    """create context menu in object mode"""
    layout = self.layout
    layout.separator()
    layout.label(text="CityJSON Options")
    layout.operator(ObjectMenu.SetAttributes.bl_idname, text="set initial attributes")
    layout.menu(ObjectMenu.VIEW3D_MT_cityobject_construction_submenu.bl_idname, text="set Construction")
    layout.operator(ObjectMenu.CalculateSemanticsOperator.bl_idname, text="calculate Semantics")
    layout.menu(ObjectMenu.VIEW3D_MT_cityobject_lod_submenu.bl_idname, text="set LoD visibility")

def editmenu_func(self, context):
    """create context menu in edit mode"""
    is_vert_mode, is_edge_mode, is_face_mode = context.tool_settings.mesh_select_mode
    if is_face_mode:
        layout = self.layout
        layout.separator()
        layout.label(text="CityJSON Options")
        layout.menu(EditMenu.VIEW3D_MT_cityedit_mesh_context_submenu.bl_idname, text="set SurfaceType")
        
        # 🆕 LOD3 Tools (only for Building objects)
        obj = context.active_object
        if obj and obj.get("cityJSONType") == "Building":
            layout.separator()
            layout.label(text="LOD3 Tools")
            # Use operator_context to force INVOKE_DEFAULT for modal operator
            layout.operator_context = 'INVOKE_DEFAULT'
            layout.operator("cityjson.place_window_lod3", text="Place Window (LOD3)", icon='MESH_PLANE')




def register():
    """Registers the classes and functions of the addon"""
    for cls in classes:
        bpy.utils.register_class(cls)
    
    # 🆕 Register PropertyGroup
    bpy.types.Scene.cityjson_editor = bpy.props.PointerProperty(
        type=properties.CityJSONEditorSettings
    )
    
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)

    # add menu to object mode context menu
    bpy.types.VIEW3D_MT_object.append(objectmenu_func)
    bpy.types.VIEW3D_MT_object_context_menu.append(objectmenu_func)
    # add menu to edit mode context menu
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.append(editmenu_func)
    bridge.register()
    
    
def unregister():
    """Unregisters the classes and functions of the addon"""
    bridge.unregister()
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    bpy.types.VIEW3D_MT_object.remove(objectmenu_func)
    bpy.types.VIEW3D_MT_object_context_menu.remove(objectmenu_func)
    bpy.types.VIEW3D_MT_edit_mesh_context_menu.remove(editmenu_func)
    
    # 🆕 Unregister PropertyGroup
    del bpy.types.Scene.cityjson_editor
    
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
