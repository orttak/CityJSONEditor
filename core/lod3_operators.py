"""
LOD3 Modal Operators - Interactive window placement (FACE-BASED).

CRITICAL: Window'lar separate object DEĞİL, Building mesh'ine eklenen FACE'ler!
Export'ta semantic surfaces içinde parent-child relationship ile yazılır.

Based on CityJSON 2.0 spec and FZK_LOD3 örneği.
"""

import bpy
import bmesh
import gpu
import uuid
from gpu_extras.batch import batch_for_shader
from mathutils import Vector
from bpy.types import Operator
from bpy.props import IntProperty, FloatVectorProperty

from .schema import CJProps, CJTypes
from .lod3_utils import (
    get_face_ortho_matrix,
    mouse_to_face_local_coords,
    calculate_rectangle_dimensions,
    validate_wall_face,
    get_building_source_id
)
from .Material import Material


def id_prop_to_dict(value):
    """
    Recursively convert Blender ID properties to plain Python types.
    
    Handles nested dicts, lists, and primitive types.
    """
    if isinstance(value, dict):
        # Convert dict-like ID properties
        return {k: id_prop_to_dict(v) for k, v in value.items()}
    elif isinstance(value, (list, tuple)):
        # Convert list/tuple elements
        return [id_prop_to_dict(item) for item in value]
    elif hasattr(value, 'to_dict'):
        # Blender ID property with to_dict method
        return id_prop_to_dict(value.to_dict())
    elif hasattr(value, '__iter__') and not isinstance(value, (str, bytes)):
        # Other iterables (but not strings)
        try:
            return [id_prop_to_dict(item) for item in value]
        except TypeError:
            return value
    else:
        # Primitive type (int, float, str, bool, None)
        return value


class CITYJSON_OT_place_window_modal(Operator):
    """Place a window on selected wall face using click-drag-click interaction"""
    
    bl_idname = "cityjson.place_window_lod3"
    bl_label = "Place Window (LOD3)"
    bl_options = {'REGISTER', 'UNDO'}
    bl_description = "Interactive window placement: Click first corner → drag → click second corner"
    
    # State variables (operator properties)
    _building_obj = None  # Internal reference (not a property)
    _face_matrix = None   # Internal 4x4 Matrix
    
    target_face_idx: IntProperty(
        name="Target Face",
        description="Index of the wall face for window placement",
        default=-1
    )
    
    # Flattened 4x4 matrix (16 floats) for persistence
    face_matrix_flat: FloatVectorProperty(
        name="Face Matrix",
        description="Flattened 4x4 face transform matrix",
        size=16,
        default=(1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1)
    )
    
    first_point_local: FloatVectorProperty(
        name="First Point",
        description="First corner in face-local coordinates",
        size=3,
        default=(0, 0, 0)
    )
    
    current_point_local: FloatVectorProperty(
        name="Current Point",
        description="Current mouse position in face-local coordinates",
        size=3,
        default=(0, 0, 0)
    )
    
    click_count: IntProperty(
        name="Click Count",
        description="Number of clicks (0 or 1)",
        default=0,
        min=0,
        max=1
    )
    
    has_first_point: bpy.props.BoolProperty(
        name="Has First Point",
        description="Whether first point has been set",
        default=False
    )
    
    _draw_handle = None  # GPU draw handler (not a property)
    
    @classmethod
    def poll(cls, context):
        """Check if operator can run in current context"""
        obj = context.active_object
        if not obj:
            return False
        if obj.type != 'MESH':
            return False
        # Allow both EDIT and OBJECT mode (we'll handle mode switching in invoke)
        if obj.mode not in {'EDIT', 'OBJECT'}:
            return False
        # Check if it's a Building
        obj_type = obj.get("cityJSONType", "")
        if obj_type not in ("Building", "BuildingPart"):
            return False
        return True
    
    def invoke(self, context, event):
        """
        Setup and validation before modal loop starts.
        
        Checks:
        - Edit mode active
        - Exactly one face selected
        - Face is on a Building object
        - Face is a WallSurface
        """
        # Validation 0: Event must exist (context menu issue)
        if not event:
            self.report({'ERROR'}, "Internal error: No event provided")
            return {'CANCELLED'}
        
        # Validation 1: Edit mode
        obj = context.active_object
        if not obj or obj.mode != 'EDIT':
            self.report({'ERROR'}, "Enter Edit Mode and select a wall face")
            return {'CANCELLED'}
        
        # Validation 2: Get selected face (must switch to Object mode to read selection)
        bpy.ops.object.mode_set(mode='OBJECT')
        selected_faces = [f.index for f in obj.data.polygons if f.select]
        
        if len(selected_faces) != 1:
            self.report({'ERROR'}, "Select exactly one wall face")
            bpy.ops.object.mode_set(mode='EDIT')  # Restore edit mode before returning
            return {'CANCELLED'}
        
        face_idx = selected_faces[0]
        
        # Validation 3: Face suitability (in Object mode)
        is_valid, error_msg = validate_wall_face(obj, face_idx)
        if not is_valid:
            self.report({'ERROR'}, error_msg)
            bpy.ops.object.mode_set(mode='EDIT')  # Restore edit mode before returning
            return {'CANCELLED'}
        
        # Setup: Store building reference and face data
        self._building_obj = obj
        self.target_face_idx = face_idx
        
        # Calculate gravity-aligned face matrix (in Object mode for stability)
        try:
            self._face_matrix = get_face_ortho_matrix(obj, face_idx)
            # Flatten matrix for property storage
            self.face_matrix_flat = [v for row in self._face_matrix for v in row]
        except Exception as exc:
            self.report({'ERROR'}, f"Failed to calculate face matrix: {exc}")
            bpy.ops.object.mode_set(mode='EDIT')  # Restore edit mode before returning
            return {'CANCELLED'}
        
        # Update context
        context.scene.cityjson_editor.active_building = obj
        context.scene.cityjson_editor.active_face_index = face_idx
        
        # Initialize state
        self.has_first_point = False
        self.click_count = 0
        
        # Setup GPU draw handler for preview
        self._draw_handle = bpy.types.SpaceView3D.draw_handler_add(
            self._draw_preview_callback,
            (),  # No args for Blender 5.0
            'WINDOW',
            'POST_VIEW'
        )
        
        # Stay in OBJECT mode for modal loop (prevents face selection issues)
        # User can see the building but face selection is preserved in face_idx
        
        # Add modal handler
        context.window_manager.modal_handler_add(self)
        context.area.tag_redraw()
        
        self.report({'INFO'}, "Click first corner of window...")
        return {'RUNNING_MODAL'}
    
    def modal(self, context, event):
        """
        Modal event loop - handles mouse movement and clicks.
        
        State machine:
        - click_count == 0: Waiting for first click
        - click_count == 1: Waiting for second click (dragging)
        """
        # Validate region_data exists (can be None in some contexts)
        if not context.region_data:
            return {'PASS_THROUGH'}
        
        context.area.tag_redraw()
        
        # Reconstruct matrix if needed
        if self._face_matrix is None and self.face_matrix_flat:
            self._face_matrix = self._unflatten_matrix(self.face_matrix_flat)
        
        # Event: Mouse movement (update preview)
        if event.type == 'MOUSEMOVE':
            point = mouse_to_face_local_coords(
                context,
                event,
                self._building_obj,
                self.target_face_idx,
                self._face_matrix
            )
            if point:
                # Update current point for preview
                if self.click_count == 0:
                    # Before first click - show point indicator
                    self.current_point_local = point
                else:
                    # After first click - dragging rectangle
                    self.current_point_local = point
        
        # Event: Left mouse button (corner selection)
        elif event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            point = mouse_to_face_local_coords(
                context,
                event,
                self._building_obj,
                self.target_face_idx,
                self._face_matrix
            )
            
            if not point:
                self.report({'WARNING'}, "Click on the face surface")
                return {'RUNNING_MODAL'}
            
            if self.click_count == 0:
                # First click: Store corner
                self.first_point_local = point
                self.current_point_local = point
                self.has_first_point = True
                self.click_count = 1
                self.report({'INFO'}, "Click second corner to finish...")
            else:
                # Second click: Create window
                self.current_point_local = point
                
                # Already in Object mode (we stayed in it since invoke)
                success = self._create_window_object(context)
                self._cleanup(context)
                
                if success:
                    self.report({'INFO'}, "Window created successfully")
                    
                    # Debug output (safe to access after cleanup)
                    obj = self._building_obj
                    try:
                        surfaces = obj.get(CJProps.SURFACES, [])
                        print(f"\\n[CityJSONEditor] Window added to building '{obj.name}'")
                        print(f"[CityJSONEditor] Total surfaces: {len(surfaces)}")
                        print(f"[CityJSONEditor] LOD: {obj.get(CJProps.LOD)}")
                    except Exception:
                        pass
                    
                    return {'FINISHED'}
                else:
                    return {'CANCELLED'}
        
        # Event: Cancel (right-click or ESC)
        elif event.type in {'RIGHTMOUSE', 'ESC'}:
            self._cleanup(context)
            self.report({'INFO'}, "Window placement cancelled")
            return {'CANCELLED'}
        
        return {'RUNNING_MODAL'}
    
    def _unflatten_matrix(self, flat):
        """Convert flat 16-element array back to 4x4 Matrix"""
        from mathutils import Matrix
        return Matrix((
            (flat[0], flat[1], flat[2], flat[3]),
            (flat[4], flat[5], flat[6], flat[7]),
            (flat[8], flat[9], flat[10], flat[11]),
            (flat[12], flat[13], flat[14], flat[15])
        ))
    
    def _create_window_object(self, context):
        """
        Add window FACES to Building mesh (NOT separate object!).
        
        This follows CityJSON spec - windows are semantic surfaces,
        not separate CityObjects.
        
        Steps:
        1. Calculate dimensions
        2. Add 4 vertices to Building mesh
        3. Create quad face from vertices
        4. Assign semantic type (Window, parent=WallSurface)
        5. Assign material
        6. Mark Building as LOD3 and dirty
        
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            obj = self._building_obj
            
            # Step 1: Calculate rectangle dimensions
            width, height, center_u, center_v = calculate_rectangle_dimensions(
                Vector(self.first_point_local),
                Vector(self.current_point_local)
            )
            
            # Validation: Minimum size
            min_size = context.scene.cityjson_editor.min_window_size
            if width < min_size or height < min_size:
                self.report({'WARNING'}, f"Window too small (min: {min_size}m)")
                return False
            
            # Step 2: Get parent WallSurface index BEFORE entering Edit mode
            # Must read attributes in Object mode!
            if obj.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            
            surfaces = list(obj.get(CJProps.SURFACES, []))
            attr = obj.data.attributes.get(CJProps.SEMANTIC_INDEX)
            
            # Convert surfaces to plain dicts (avoid ID property proxies)
            surfaces = id_prop_to_dict(surfaces)
            
            # Debug logging (safe in Object mode before Edit mode transition)
            print(f"\n[CityJSONEditor] ========== WINDOW CREATION START ==========")
            print(f"[CityJSONEditor] Building: {obj.name}")
            print(f"[CityJSONEditor] Total surfaces in building: {len(surfaces)}")
            print(f"[CityJSONEditor] Selected face index: {self.target_face_idx}")
            print(f"[CityJSONEditor] Total polygons: {len(obj.data.polygons)}")
            print(f"[CityJSONEditor] Semantic attribute exists: {attr is not None}")
            if attr:
                print(f"[CityJSONEditor] Attribute data length: {len(attr.data)}")
            
            parent_idx = None
            if attr and self.target_face_idx >= 0 and self.target_face_idx < len(attr.data):
                parent_idx = attr.data[self.target_face_idx].value
                print(f"[CityJSONEditor] Read parent semantic index from face {self.target_face_idx}: {parent_idx}")
                
                if parent_idx < 0 or parent_idx >= len(surfaces):
                    print(f"[CityJSONEditor] ⚠️  WARNING: Invalid parent_idx {parent_idx} (must be 0-{len(surfaces)-1})")
                    parent_idx = None
                elif parent_idx is not None:
                    parent_surface = surfaces[parent_idx]
                    parent_type = parent_surface.get("type", "Unknown") if isinstance(parent_surface, dict) else "Unknown"
                    print(f"[CityJSONEditor] ✓ Parent surface [{parent_idx}]: {parent_type}")
                    print(f"[CityJSONEditor] Parent surface data: {parent_surface}")
            else:
                print(f"[CityJSONEditor] ⚠️  Cannot read parent semantic index")
                print(f"[CityJSONEditor]    - Attribute exists: {attr is not None}")
                print(f"[CityJSONEditor]    - Face index: {self.target_face_idx}")
                print(f"[CityJSONEditor]    - Attribute data length: {len(attr.data) if attr else 0}")

            
            # Step 3: Switch to Edit mode and get BMesh
            bpy.ops.object.mode_set(mode='EDIT')
            
            bm = bmesh.from_edit_mesh(obj.data)
            bm.faces.ensure_lookup_table()
            bm.verts.ensure_lookup_table()
            
            # Step 3: Calculate 4 corners in world space
            hw, hh = width / 2.0, height / 2.0
            corners_local = [
                Vector((center_u - hw, center_v - hh, 0)),  # Bottom-left
                Vector((center_u + hw, center_v - hh, 0)),  # Bottom-right
                Vector((center_u + hw, center_v + hh, 0)),  # Top-right
                Vector((center_u - hw, center_v + hh, 0))   # Top-left
            ]
            corners_world = [self._face_matrix @ c for c in corners_local]
            
            # Step 4: Add vertices to BMesh
            new_verts = [bm.verts.new(c) for c in corners_world]
            
            # Step 5: Create face (quad)
            try:
                new_face = bm.faces.new(new_verts)
            except ValueError as e:
                self.report({'ERROR'}, f"Cannot create face: {e}")
                bm.free()
                return False
            
            # Step 6: Update mesh and get new face index
            bmesh.update_edit_mesh(obj.data)
            
            # CRITICAL: Switch to Object mode to get proper face index
            bpy.ops.object.mode_set(mode='OBJECT')
            
            # Force mesh update to ensure attribute data array is resized
            obj.data.update()
            
            # Find the new face index (last face added)
            new_face_idx = len(obj.data.polygons) - 1
            
            print(f"[CityJSONEditor] New window face index: {new_face_idx}")
            print(f"[CityJSONEditor] Total faces after adding window: {len(obj.data.polygons)}")
            
            # Step 7: Get or create semantic attribute
            if attr is None:
                print(f"[CityJSONEditor] Creating new semantic attribute...")
                attr = obj.data.attributes.new(
                    name=CJProps.SEMANTIC_INDEX,
                    type='INT',
                    domain='FACE'
                )
            else:
                print(f"[CityJSONEditor] Refreshing semantic attribute reference...")
                # Refresh attribute reference after mesh update
                attr = obj.data.attributes.get(CJProps.SEMANTIC_INDEX)
            
            # Verify attribute data size matches polygon count
            print(f"[CityJSONEditor] Attribute data size: {len(attr.data)}")
            print(f"[CityJSONEditor] Polygon count: {len(obj.data.polygons)}")
            
            if len(attr.data) != len(obj.data.polygons):
                print(f"[CityJSONEditor] ❌ ERROR: Attribute size mismatch!")
                print(f"[CityJSONEditor]    Expected: {len(obj.data.polygons)}")
                print(f"[CityJSONEditor]    Got: {len(attr.data)}")
                self.report({'ERROR'}, "Attribute size mismatch after adding face")
                return False
            
            # Step 8: Create Window semantic surface
            window_idx = len(surfaces)
            window_id = f"Window_{uuid.uuid4().hex[:8]}"
            
            print(f"[CityJSONEditor] Creating Window semantic surface...")
            print(f"[CityJSONEditor] Window index in surfaces array: {window_idx}")
            print(f"[CityJSONEditor] Window ID: {window_id}")
            
            new_surface = {
                "type": "Window",
                "id": window_id,
                "name": f"Window_{window_idx + 1}"
            }
            
            if parent_idx is not None:
                new_surface["parent"] = parent_idx
                print(f"[CityJSONEditor] ✓ Setting parent: {parent_idx}")
                
                # Update parent's children array
                parent_surface = surfaces[parent_idx]
                if isinstance(parent_surface, dict):
                    children = parent_surface.get("children", [])
                    if not isinstance(children, list):
                        children = []
                        parent_surface["children"] = children
                    if window_idx not in children:
                        children.append(window_idx)
                        print(f"[CityJSONEditor] ✓ Added window to parent's children array: {children}")
            else:
                print(f"[CityJSONEditor] ⚠️  WARNING: No parent set (window orphaned!)")
            
            surfaces.append(new_surface)
            print(f"[CityJSONEditor] Window surface added to surfaces array (total: {len(surfaces)})")
            
            # Step 9: Assign semantic index to new face (already in Object mode)
            print(f"[CityJSONEditor] Assigning semantic index to window face...")
            print(f"[CityJSONEditor] Face index: {new_face_idx}")
            print(f"[CityJSONEditor] Semantic index (Window): {window_idx}")
            
            try:
                obj.data.attributes[CJProps.SEMANTIC_INDEX].data[new_face_idx].value = window_idx
                
                # Verify assignment
                verify_idx = obj.data.attributes[CJProps.SEMANTIC_INDEX].data[new_face_idx].value
                
                if verify_idx == window_idx:
                    print(f"[CityJSONEditor] ✓ SUCCESS: Semantic index assigned and verified!")
                    print(f"[CityJSONEditor]    Face {new_face_idx} → Semantic index {verify_idx}")
                else:
                    print(f"[CityJSONEditor] ❌ ERROR: Verification mismatch!")
                    print(f"[CityJSONEditor]    Expected: {window_idx}, Got: {verify_idx}")
                    
            except IndexError as e:
                print(f"[CityJSONEditor] ❌ ERROR: Cannot assign semantic index")
                print(f"[CityJSONEditor]    {e}")
                self.report({'ERROR'}, f"Cannot assign semantic index: {e}")
                return False
            
            # Step 10: Update object properties
            print(f"[CityJSONEditor] Updating object properties...")
            print(f"[CityJSONEditor] Surfaces array length: {len(surfaces)}")
            print(f"[CityJSONEditor] Last surface in array: {surfaces[-1]}")
            
            # Force conversion to plain Python types (avoid Blender ID properties)
            surfaces_clean = id_prop_to_dict(surfaces)
            
            obj[CJProps.SURFACES] = surfaces_clean
            obj[CJProps.DIRTY] = True
            obj[CJProps.LOD] = 3.0  # Mark as LOD3
            
            # Verify storage
            stored = obj.get(CJProps.SURFACES, [])
            print(f"[CityJSONEditor] Verification: Stored surfaces count = {len(stored)}")
            if len(stored) > 0:
                print(f"[CityJSONEditor] Last stored surface: {stored[-1]}")
            
            # Step 11: Assign material (back to Edit mode for material ops)
            bpy.ops.object.mode_set(mode='EDIT')
            
            mat = Material(
                type="Window",
                newObject=obj,
                objectID=obj.name,
                textureSetting=False,
                objectType=obj.get(CJProps.TYPE, "Building"),
                surfaceIndex=window_idx,
                surfaceValue=new_surface,
                filepath=None,
                rawObjectData=None,
                geometry=None
            )
            mat.createMaterial()
            mat.setColor()
            mat.addMaterialToFace(obj.active_material_index, new_face_idx)
            
            # Step 13: Back to Object mode
            bpy.ops.object.mode_set(mode='OBJECT')
            
            # Force viewport update
            for area in context.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
            
            # Success message
            print(f"[CityJSONEditor] ========================================")
            print(f"[CityJSONEditor] ✓ WINDOW CREATION COMPLETE")
            print(f"[CityJSONEditor] Dimensions: {width:.2f}m × {height:.2f}m")
            print(f"[CityJSONEditor] Face: {new_face_idx}, Semantic: {window_idx}")
            print(f"[CityJSONEditor] Parent: {parent_idx}")
            print(f"[CityJSONEditor] ==========================================\n")
            
            self.report({'INFO'}, f"Window added: {width:.2f}m × {height:.2f}m")
            
            return True
            
        except Exception as exc:
            self.report({'ERROR'}, f"Window creation failed: {exc}")
            import traceback
            traceback.print_exc()
            return False
    
    def _draw_preview_callback(self):
        """
        GPU draw callback - renders preview rectangle.
        
        Called automatically by Blender during viewport redraw.
        Draws blue rectangle outline and semi-transparent fill.
        """
        # Get context from bpy (callback doesn't receive context parameter in Blender 5.0)
        context = bpy.context
        
        # Safety checks
        if not self._face_matrix:
            return
        if not self.has_first_point:
            return
        
        # Only draw after first click
        if self.click_count == 0:
            return
        
        settings = context.scene.cityjson_editor
        if not settings.show_preview:
            return
        
        try:
            # Get rectangle corners in local space
            p1 = Vector(self.first_point_local)
            p2 = Vector(self.current_point_local)
            
            corners_local = [
                Vector((p1.x, p1.y, 0)),
                Vector((p2.x, p1.y, 0)),
                Vector((p2.x, p2.y, 0)),
                Vector((p1.x, p2.y, 0))
            ]
            
            # Transform to world space
            corners_world = [self._face_matrix @ c for c in corners_local]
            
            # Setup shader
            shader = gpu.shader.from_builtin('UNIFORM_COLOR')
            
            # Draw outline
            gpu.state.line_width_set(2.0)
            batch_outline = batch_for_shader(
                shader,
                'LINE_LOOP',
                {"pos": corners_world}
            )
            shader.bind()
            color = (*settings.preview_color, settings.preview_alpha)
            shader.uniform_float("color", color)
            batch_outline.draw(shader)
            
            # Draw fill (semi-transparent)
            batch_fill = batch_for_shader(
                shader,
                'TRI_FAN',
                {"pos": corners_world}
            )
            fill_color = (*settings.preview_color, settings.preview_alpha * 0.3)
            shader.uniform_float("color", fill_color)
            batch_fill.draw(shader)
            
        except Exception as exc:
            print(f"[CityJSONEditor] GPU draw error: {exc}")
    
    def _cleanup(self, context):
        """
        Cleanup modal state and GPU handlers.
        
        Called on FINISHED or CANCELLED.
        CRITICAL: Must remove draw handler to prevent memory leak.
        """
        # Remove GPU draw handler
        if self._draw_handle is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(
                    self._draw_handle,
                    'WINDOW'
                )
            except Exception as exc:
                print(f"[CityJSONEditor] Warning: Failed to remove draw handler: {exc}")
            self._draw_handle = None
        
        # Clear context
        context.scene.cityjson_editor.active_building = None
        context.scene.cityjson_editor.active_face_index = -1
        
        # Clear internal state
        self._building_obj = None
        self._face_matrix = None
        
        # Redraw viewport
        context.area.tag_redraw()


# Registration
classes = (
    CITYJSON_OT_place_window_modal,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
