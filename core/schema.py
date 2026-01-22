"""
CityJSON Schema Constants - Central definition of property names and types.

This module defines all magic strings used throughout the CityJSONEditor
to prevent typos and make refactoring easier.

Usage:
    from .schema import CJProps, CJTypes, CJCollections, CJExport
    
    obj[CJProps.TYPE] = CJTypes.BUILDING
    collection = bpy.data.collections[CJCollections.LOD_3_OPENINGS]
"""


class CJProps:
    """Blender Object Custom Property Names (stored on bpy.types.Object)"""
    
    # Core identification
    TYPE = "cityJSONType"                # String: "Building", "Window", etc.
    LOD = "LOD"                          # Float: 0.0, 1.0, 2.0, 3.0
    SOURCE_ID = "cj_source_id"           # String: "Building_123" (unique ID)
    
    # Parent-child relationships (LOD3 openings)
    PARENT_IDS = "cj_parent_ids"         # List[str]: ["Building_123"]
    TARGET_FACE = "cj_target_face_idx"   # Int: Face index on parent building
    
    # Export tracking
    DIRTY = "cj_dirty"                   # Bool: Modified since import
    GEOMETRY_TYPE = "cj_geometry_type"   # String: "Solid", "MultiSurface"
    ATTRIBUTES = "cj_attributes"         # Dict: CityJSON attributes
    
    # LOD2 semantic surfaces (existing system)
    SURFACES = "cj_semantic_surfaces"    # List[Dict]: Semantic surfaces
    SEMANTIC_INDEX = "cje_semantic_index"  # Mesh attribute name (face domain)
    HAS_SEMANTICS = "cj_has_semantics"   # Bool: Has semantic data
    GEOM_INDEX = "cj_geom_index"         # Int: Geometry index
    
    # Import metadata
    GML_ID = "gmlid"                     # String: Original GML ID


class CJTypes:
    """CityJSON CityObject Type Values"""
    
    # Buildings
    BUILDING = "Building"
    BUILDING_PART = "BuildingPart"
    BUILDING_INSTALLATION = "BuildingInstallation"
    
    # Openings (LOD3+)
    WINDOW = "Window"
    DOOR = "Door"
    
    # Semantic surfaces
    WALL_SURFACE = "WallSurface"
    ROOF_SURFACE = "RoofSurface"
    GROUND_SURFACE = "GroundSurface"
    CLOSURE_SURFACE = "ClosureSurface"
    FLOOR_SURFACE = "FloorSurface"
    CEILING_SURFACE = "CeilingSurface"
    
    # Other
    GENERIC = "GenericCityObject"


class CJCollections:
    """Blender Collection Names for LOD Organization"""
    
    LOD_0 = "LOD_0"
    LOD_1 = "LOD_1"
    LOD_2 = "LOD_2"
    LOD_3 = "LOD_3"
    LOD_3_OPENINGS = "LOD_3_Openings"  # Windows, Doors


class CJExport:
    """CityJSON Export Field Names (JSON structure)"""
    
    # CityObject fields
    TYPE = "type"
    PARENTS = "parents"              # List[str] - Object-level parent references
    CHILDREN = "children"            # List[str] - Object-level child references
    GEOMETRY = "geometry"
    ATTRIBUTES = "attributes"
    
    # Geometry fields
    LOD = "lod"
    BOUNDARIES = "boundaries"
    SEMANTICS = "semantics"
    
    # Semantic fields
    SURFACES = "surfaces"
    VALUES = "values"
    PARENT = "parent"                # Int - Semantic surface parent index
    CHILDREN_SEMANTIC = "children"   # List[int] - Semantic surface child indices


class CJSemantic:
    """Semantic Surface Property Names (within geometry.semantics)"""
    
    TYPE = "type"
    PARENT = "parent"         # Int: Parent surface index
    CHILDREN = "children"     # List[int]: Child surface indices
    VALUES = "values"         # List: Semantic value indices
    SURFACES = "surfaces"     # List[Dict]: Surface definitions


# Version info
CITYJSON_VERSION = "2.0"
CITYJSON_EXPORT_VERSION = "2.0"  # Use 2.0 for tool compatibility
