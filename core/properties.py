"""
CityJSON Editor PropertyGroups - Scene-level context tracking for LOD3 editing.

This module defines PropertyGroups that store editor state, such as:
- Active building for LOD3 editing
- Selected wall face for window placement
- User preferences for window dimensions

PropertyGroups are registered to bpy.types.Scene.cityjson_editor
"""

import bpy
from bpy.types import PropertyGroup
from bpy.props import (
    PointerProperty,
    IntProperty,
    FloatProperty,
    BoolProperty,
    FloatVectorProperty
)


class CityJSONEditorSettings(PropertyGroup):
    """
    Scene-level settings for CityJSON LOD3 editing workflow.
    
    Accessed via: bpy.context.scene.cityjson_editor
    
    Example:
        settings = context.scene.cityjson_editor
        settings.active_building = my_building_obj
        print(settings.active_face_index)
    """
    
    # Active context for LOD3 editing
    active_building: PointerProperty(
        type=bpy.types.Object,
        name="Active Building",
        description="Currently selected LOD2 building for LOD3 window/door placement"
    )
    
    active_face_index: IntProperty(
        name="Active Face Index",
        description="Index of the wall face currently targeted for opening placement",
        default=-1,
        min=-1
    )
    
    # Window placement defaults (user hints)
    default_window_depth: FloatProperty(
        name="Window Depth",
        description="Default extrusion depth for window geometry (meters)",
        default=0.15,
        min=0.01,
        max=1.0,
        precision=3,
        unit='LENGTH'
    )
    
    last_window_width: FloatProperty(
        name="Last Window Width",
        description="Width of the last placed window (used as hint for next placement)",
        default=1.2,
        min=0.1,
        max=10.0,
        precision=2,
        unit='LENGTH'
    )
    
    last_window_height: FloatProperty(
        name="Last Window Height",
        description="Height of the last placed window (used as hint for next placement)",
        default=1.5,
        min=0.1,
        max=10.0,
        precision=2,
        unit='LENGTH'
    )
    
    # Preview settings (modal operator)
    show_preview: BoolProperty(
        name="Show Preview",
        description="Show rectangle preview during interactive window placement",
        default=True
    )
    
    preview_color: FloatVectorProperty(
        name="Preview Color",
        description="Color of the rectangle preview overlay",
        subtype='COLOR',
        size=3,
        default=(0.5, 0.8, 1.0),
        min=0.0,
        max=1.0
    )
    
    preview_alpha: FloatProperty(
        name="Preview Alpha",
        description="Transparency of the preview rectangle (0=invisible, 1=opaque)",
        default=0.8,
        min=0.0,
        max=1.0
    )
    
    # Shrinkwrap settings
    shrinkwrap_offset: FloatProperty(
        name="Shrinkwrap Offset",
        description="Distance offset from wall surface to prevent z-fighting (meters)",
        default=0.05,  # 5cm offset for visibility
        min=0.01,      # Minimum 1cm
        max=0.5,       # Maximum 50cm
        precision=3,
        unit='LENGTH'
    )
    
    # Validation settings
    min_window_size: FloatProperty(
        name="Minimum Window Size",
        description="Minimum width/height for window creation (meters)",
        default=0.1,
        min=0.01,
        max=1.0,
        precision=2,
        unit='LENGTH'
    )
    
    # Advanced options
    auto_parent: BoolProperty(
        name="Auto Parent",
        description="Automatically set Blender parent relationship (window → building)",
        default=True
    )
    
    validate_wall_surface: BoolProperty(
        name="Validate Wall Surface",
        description="Only allow window placement on faces tagged as WallSurface",
        default=True
    )


# Registration helpers (called from __init__.py)
def register():
    """Register PropertyGroup to Scene"""
    bpy.types.Scene.cityjson_editor = PointerProperty(type=CityJSONEditorSettings)


def unregister():
    """Unregister PropertyGroup from Scene"""
    del bpy.types.Scene.cityjson_editor
