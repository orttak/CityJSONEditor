"""
LOD3 Utility Functions - Math helpers for window placement.

Critical functions:
- get_face_ortho_matrix(): Gravity-aligned coordinate system
- mouse_to_face_local_coords(): 2D screen → 3D face coordinates
- create_rectangle_mesh(): Window geometry builder
- validate_wall_face(): Face suitability checks

Mathematical guarantee: Windows always align parallel to ground (World Z).
"""

import bpy
import bmesh
from mathutils import Vector, Matrix
from mathutils.geometry import intersect_ray_tri
from bpy_extras import view3d_utils
from .schema import CJProps, CJTypes


def get_face_ortho_matrix(obj, face_index):
    """
    Calculate gravity-aligned transform matrix for a face.
    
    This is CRITICAL for ensuring windows are always upright, regardless
    of wall angle. The tangent (X) axis is forced to be horizontal (parallel
    to world XY plane), and bitangent (Y) points upward on the wall surface.
    
    Coordinate system:
        X axis = Horizontal (tangent, parallel to ground)
        Y axis = Vertical on wall (bitangent, upward)
        Z axis = Face normal (outward)
        Origin = Face center
    
    Args:
        obj (bpy.types.Object): Building object
        face_index (int): Index of target face
    
    Returns:
        Matrix: 4x4 world-space transform matrix
    
    Example:
        matrix = get_face_ortho_matrix(building, 42)
        local_point = Vector((0.5, 1.0, 0))  # 0.5m right, 1m up
        world_point = matrix @ local_point
    """
    # BMesh for face access
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    
    if face_index < 0 or face_index >= len(bm.faces):
        bm.free()
        raise IndexError(f"Face index {face_index} out of range")
    
    face = bm.faces[face_index]
    
    # 1. Transform normal to world space
    normal_local = face.normal.copy()
    normal_world = obj.matrix_world.to_3x3() @ normal_local
    normal_world.normalize()
    
    # 2. Calculate tangent (X axis) - MUST be horizontal
    world_up = Vector((0, 0, 1))
    
    # Special case: Face is horizontal (roof/floor)
    if abs(normal_world.dot(world_up)) > 0.99:
        # For horizontal faces, use world X as tangent
        tangent = Vector((1, 0, 0))
    else:
        # For walls: Cross product with world up gives horizontal line
        tangent = world_up.cross(normal_world)
        tangent.normalize()
    
    # 3. Calculate bitangent (Y axis) - Points upward on wall
    bitangent = normal_world.cross(tangent)
    bitangent.normalize()
    
    # 4. Calculate face center in world space
    center_local = face.calc_center_median()
    center_world = obj.matrix_world @ center_local
    
    # 5. Construct 4x4 matrix (column-major order)
    # Each column is an axis vector + position
    matrix = Matrix((
        (tangent.x,    bitangent.x,    normal_world.x,    center_world.x),
        (tangent.y,    bitangent.y,    normal_world.y,    center_world.y),
        (tangent.z,    bitangent.z,    normal_world.z,    center_world.z),
        (0.0,          0.0,            0.0,               1.0)
    ))
    
    bm.free()
    return matrix


def mouse_to_face_local_coords(context, event, obj, face_index, face_matrix):
    """
    Convert 2D mouse screen coordinates to 3D face-local coordinates.
    
    Uses ray-casting to find intersection point on face, then transforms
    to face-local coordinate system using the provided matrix.
    
    Args:
        context (bpy.context): Blender context
        event: Mouse event with .mouse_region_x, .mouse_region_y
        obj (bpy.types.Object): Target object
        face_index (int): Target face index
        face_matrix (Matrix): Face transform matrix from get_face_ortho_matrix()
    
    Returns:
        Vector or None: (u, v, 0) in face-local space, or None if ray miss
    
    Example:
        matrix = get_face_ortho_matrix(obj, face_idx)
        local_pt = mouse_to_face_local_coords(context, event, obj, face_idx, matrix)
        if local_pt:
            print(f"Clicked at U={local_pt.x}, V={local_pt.y}")
    """
    # Get mouse ray in 3D space
    region = context.region
    rv3d = context.region_data
    coord = (event.mouse_region_x, event.mouse_region_y)
    
    ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    ray_direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    
    # Get face vertices in world space
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    
    if face_index < 0 or face_index >= len(bm.faces):
        bm.free()
        return None
    
    face = bm.faces[face_index]
    verts_world = [obj.matrix_world @ v.co for v in face.verts]
    
    # Triangulate face (handles ngons)
    tris = []
    for i in range(1, len(verts_world) - 1):
        tris.append((verts_world[0], verts_world[i], verts_world[i + 1]))
    
    bm.free()
    
    # Ray-triangle intersection test
    intersection_world = None
    for tri in tris:
        hit = intersect_ray_tri(*tri, ray_direction, ray_origin, False)
        if hit:
            intersection_world = hit
            break
    
    if not intersection_world:
        return None
    
    # Transform world coords → local coords
    matrix_inv = face_matrix.inverted()
    intersection_local = matrix_inv @ intersection_world
    
    # Clamp Z to 0 (should be on face plane)
    return Vector((intersection_local.x, intersection_local.y, 0.0))


def create_rectangle_mesh(name, width, height, depth=0.05):
    """
    Create a 3D extruded rectangle mesh for window geometry.
    
    Creates a CUBOID (not flat plane) to ensure visibility:
    - Front face (wall-facing)
    - Back face (building interior)
    - 4 side faces (frame edges)
    
    This prevents z-fighting with wall surface.
    
    Args:
        name (str): Mesh name
        width (float): X dimension (meters)
        height (float): Y dimension (meters)
        depth (float): Z extrusion depth (meters, default: 0.05 = 5cm)
    
    Returns:
        bpy.types.Mesh: Created 3D mesh (6 faces)
    
    Example:
        mesh = create_rectangle_mesh("Window_001", 1.2, 1.5, 0.05)
        obj = bpy.data.objects.new("Window", mesh)
    """
    mesh = bpy.data.meshes.new(name)
    
    # Half dimensions for centering
    hw = width / 2.0
    hh = height / 2.0
    hd = depth / 2.0  # Center depth around Z=0
    
    # 8 vertices (cuboid)
    verts = [
        # Front face (Z = +hd, wall-facing)
        (-hw, -hh,  hd),  # 0: Bottom-left front
        ( hw, -hh,  hd),  # 1: Bottom-right front
        ( hw,  hh,  hd),  # 2: Top-right front
        (-hw,  hh,  hd),  # 3: Top-left front
        
        # Back face (Z = -hd, building interior)
        (-hw, -hh, -hd),  # 4: Bottom-left back
        ( hw, -hh, -hd),  # 5: Bottom-right back
        ( hw,  hh, -hd),  # 6: Top-right back
        (-hw,  hh, -hd),  # 7: Top-left back
    ]
    
    # 6 faces (quad faces, counter-clockwise winding)
    faces = [
        (0, 1, 2, 3),  # Front face (visible from outside)
        (5, 4, 7, 6),  # Back face (interior)
        (4, 5, 1, 0),  # Bottom edge
        (6, 7, 3, 2),  # Top edge
        (7, 4, 0, 3),  # Left edge
        (5, 6, 2, 1),  # Right edge
    ]
    
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    
    # Add UV coordinates (simple planar mapping)
    if not mesh.uv_layers:
        uv_layer = mesh.uv_layers.new(name="UVMap")
        # Simple UV coords for each face
        uv_coords_per_face = [(0, 0), (1, 0), (1, 1), (0, 1)]
        
        for loop_idx in range(len(mesh.loops)):
            local_idx = loop_idx % 4
            uv_layer.data[loop_idx].uv = uv_coords_per_face[local_idx]
    
    return mesh


def ensure_lod3_collection():
    """
    Ensure LOD_3_Openings collection exists and is linked to scene.
    
    DEPRECATED: Use get_building_lod3_collection() for better hierarchy.
    
    Creates collection if it doesn't exist. This collection holds all
    LOD3 opening objects (windows, doors) to keep scene organized.
    
    Returns:
        bpy.types.Collection: The LOD_3_Openings collection
    
    Example:
        col = ensure_lod3_collection()
        col.objects.link(window_obj)
    """
    col_name = "LOD_3_Openings"
    
    # Check if collection exists
    if col_name in bpy.data.collections:
        return bpy.data.collections[col_name]
    
    # Create new collection
    collection = bpy.data.collections.new(col_name)
    bpy.context.scene.collection.children.link(collection)
    
    return collection


def get_building_lod3_collection(building_obj):
    """
    Get or create building-specific LOD3 sub-collection.
    
    This creates a hierarchical structure:
        Scene Collection
        └── LOD_3
            └── Building_XXX_LOD3
                ├── Window_1
                ├── Window_2
                └── ...
    
    This matches the import structure and keeps windows organized
    per building in the Outliner.
    
    Args:
        building_obj (bpy.types.Object): Building object
    
    Returns:
        bpy.types.Collection: Building-specific LOD3 collection
    
    Example:
        col = get_building_lod3_collection(building)
        col.objects.link(window_obj)
    """
    building_id = get_building_source_id(building_obj)
    building_col_name = f"{building_id}_LOD3"
    
    # Ensure LOD_3 main collection exists
    if "LOD_3" not in bpy.data.collections:
        lod3_main = bpy.data.collections.new("LOD_3")
        bpy.context.scene.collection.children.link(lod3_main)
    else:
        lod3_main = bpy.data.collections["LOD_3"]
    
    # Check if building sub-collection exists
    if building_col_name in bpy.data.collections:
        building_col = bpy.data.collections[building_col_name]
        print(f"[CityJSONEditor] Using existing collection: {building_col_name}")
        return building_col
    
    # Create building-specific sub-collection
    building_col = bpy.data.collections.new(building_col_name)
    lod3_main.children.link(building_col)
    
    print(f"[CityJSONEditor] Created collection: {building_col_name}")
    return building_col


def validate_wall_face(obj, face_index):
    """
    Validate if a face is suitable for window placement.
    
    Checks:
    1. Object is a Building
    2. Face index is valid
    3. Face is tagged as WallSurface (if semantic data exists)
    4. Face area is sufficient (>= 0.1 m²)
    
    Args:
        obj (bpy.types.Object): Target object
        face_index (int): Face index to validate
    
    Returns:
        tuple: (is_valid: bool, error_message: str)
        
    Example:
        is_valid, error = validate_wall_face(building, 42)
        if not is_valid:
            self.report({'ERROR'}, error)
            return {'CANCELLED'}
    """
    # Check 1: Is it a Building?
    if not obj:
        return False, "No object provided"
    
    obj_type = obj.get(CJProps.TYPE, "")
    if obj_type != CJTypes.BUILDING and obj_type != "BuildingPart":
        return False, f"Object is not a Building (type: {obj_type})"
    
    # Check 2: Valid face index?
    if not obj.data or not hasattr(obj.data, 'polygons'):
        return False, "Object has no mesh data"
    
    if face_index < 0 or face_index >= len(obj.data.polygons):
        return False, f"Invalid face index: {face_index}"
    
    face = obj.data.polygons[face_index]
    
    # Check 3: Is it a WallSurface? (optional, only if semantic data exists)
    surfaces = obj.get(CJProps.SURFACES, [])
    attr = obj.data.attributes.get(CJProps.SEMANTIC_INDEX)
    
    if attr and surfaces and face_index < len(attr.data):
        semantic_idx = attr.data[face_index].value
        if 0 <= semantic_idx < len(surfaces):
            surface = surfaces[semantic_idx]
            surface_type = surface.get("type", "") if isinstance(surface, dict) else ""
            
            # Reject Window/Door faces - can't add window to a window!
            if surface_type in ("Window", "Door"):
                return False, f"Cannot add window to {surface_type}. Select a WallSurface."
            
            # Accept any "*WallSurface" type
            if surface_type and "Wall" not in surface_type:
                return False, f"Face is not a wall (type: {surface_type}). Select a WallSurface."
    
    # Check 4: Sufficient area?
    if face.area < 0.1:  # 0.1 m² minimum
        return False, f"Face too small ({face.area:.3f} m²). Minimum area: 0.1 m²"
    
    return True, ""


def get_building_source_id(obj):
    """
    Get the CityJSON source ID of a building object.
    
    Falls back to object name if cj_source_id is not set.
    
    Args:
        obj (bpy.types.Object): Building object
    
    Returns:
        str: Source ID (e.g., "Building_123")
    """
    return obj.get(CJProps.SOURCE_ID, obj.name.split("__")[0])


def calculate_rectangle_dimensions(point1, point2):
    """
    Calculate width and height from two corner points.
    
    Args:
        point1 (Vector): First corner (local coords)
        point2 (Vector): Second corner (local coords)
    
    Returns:
        tuple: (width: float, height: float, center_u: float, center_v: float)
    """
    width = abs(point2.x - point1.x)
    height = abs(point2.y - point1.y)
    center_u = (point1.x + point2.x) / 2.0
    center_v = (point1.y + point2.y) / 2.0
    
    return width, height, center_u, center_v
