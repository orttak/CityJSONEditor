import bpy
from .FeatureTypes import FeatureTypes
from .Material import Material
import math


def id_prop_to_dict(value):
    """
    Recursively convert Blender ID properties to plain Python types.
    """
    if isinstance(value, dict):
        return {k: id_prop_to_dict(v) for k, v in value.items()}
    elif isinstance(value, (list, tuple)):
        return [id_prop_to_dict(item) for item in value]
    elif hasattr(value, 'to_dict'):
        return id_prop_to_dict(value.to_dict())
    elif hasattr(value, '__iter__') and not isinstance(value, (str, bytes)):
        try:
            return [id_prop_to_dict(item) for item in value]
        except TypeError:
            return value
    else:
        return value


class SetAttributes(bpy.types.Operator):
    bl_idname = "wm.set_attributes"
    bl_label = "SetAttributes"

    def execute(self,context):
        obj = bpy.context.active_object
        obj['cityJSONType'] = "Building"
        obj['LOD'] = 2 
        return {'FINISHED'} 

class SetConstructionOperator(bpy.types.Operator):
    bl_idname = "wm.set_cityjsontype"
    bl_label = "SetCityJSONType"
    cityJSONType:     bpy.props.StringProperty(name='cityJSONType',default='Building',)

    def execute(self, context):
        obj = bpy.context.active_object
        obj['cityJSONType'] = self.cityJSONType
        return {'FINISHED'} 
    

class VIEW3D_MT_cityobject_construction_submenu(bpy.types.Menu):
    bl_label = 'Construction'
    bl_idname = 'VIEW3D_MT_cityobject_construction_submenu'
    def draw(self, context):

        layout = self.layout
        layout.label(text="Construction")

        ft = FeatureTypes()
        list = ft.getAllFeatures()
        
        for feature in list:
            layout.operator(SetConstructionOperator.bl_idname, text=feature).cityJSONType = feature

class CalculateSemanticsOperator(bpy.types.Operator):
    bl_idname = "wm.calc_semantics"
    bl_label = "CalculateSemantics"

    def execute(self, context):
        
        # Check if an object is selected
        obj = bpy.context.active_object
        if obj is None:
            self.report({'ERROR'}, "No object selected. Select a Building in Object mode.")
            return {'CANCELLED'}
        
        if obj.type != 'MESH':
            self.report({'ERROR'}, "Selected object is not a mesh.")
            return {'CANCELLED'}

        # if initial attributes are not already set, do so now
        try: 
            if obj.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            type = obj['cityJSONType']
                        
        except:
            obj['cityJSONType'] = "Building"
            obj['LOD'] = 2 
        
        def materialCreator(surfaceType,matSlot,faceIndex):
            mat = Material(type=surfaceType, newObject=obj, objectID=obj.id_data.name, textureSetting=False, objectType=obj['cityJSONType'], surfaceIndex=None, surfaceValue=None, filepath=None, rawObjectData=None, geometry=None)
            mat.createMaterial()
            mat.setColor()
            mat.assignMaterials(faceIndex, matSlot)
            del mat

        def materialCleaner():
            bpy.ops.object.mode_set(mode='OBJECT')
            for face in obj.data.polygons:
                matIndex = face.material_index
                bpy.context.object.active_material_index = matIndex
                bpy.ops.object.material_slot_remove()
            # bpy.ops.object.mode_set(mode='EDIT')   
            bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=False, do_recursive=True)

        obj = context.object
        if obj.mode != 'OBJECT':
             bpy.ops.object.mode_set(mode='OBJECT')
        mesh = obj.data
        mesh.update()
        
        # Get or create semantic attribute - DON'T DELETE existing one!
        attr = mesh.attributes.get("cje_semantic_index")
        if attr is None:
            print("[CityJSONEditor] Creating new semantic attribute...")
            attr = mesh.attributes.new(name="cje_semantic_index", type='INT', domain='FACE')
        else:
            print(f"[CityJSONEditor] Using existing semantic attribute (preserving Window/Door faces)")
        
        # Verify attribute validity
        if len(attr.data) != len(mesh.polygons):
             # Size mismatch - need to recreate
             print(f"[CityJSONEditor] WARNING: Attribute size mismatch - recreating")
             mesh.update()
             mesh.attributes.remove(attr)
             attr = mesh.attributes.new(name="cje_semantic_index", type='INT', domain='FACE')
             if len(attr.data) != len(mesh.polygons):
                 self.report({'ERROR'}, f"Mesh attribute mismatch: {len(attr.data)} vs {len(mesh.polygons)}. Try regularizing geometry.")
                 return {'CANCELLED'}

        surfaces = list(obj.get("cj_semantic_surfaces", []))
        
        # Convert to plain dicts (avoid ID property issues)
        surfaces = id_prop_to_dict(surfaces)
        
        print(f"\n[CityJSONEditor] ========== CALCULATE SEMANTIC START ==========")
        print(f"[CityJSONEditor] Object: {obj.name}")
        print(f"[CityJSONEditor] Total faces: {len(mesh.polygons)}")
        print(f"[CityJSONEditor] Total surfaces: {len(surfaces)}")
        
        # Preserve existing Window/Door semantics before cleaning materials
        preserved_semantics = {}
        old_attr = mesh.attributes.get("cje_semantic_index")
        
        print(f"[CityJSONEditor] Semantic attribute exists: {old_attr is not None}")
        if old_attr:
            print(f"[CityJSONEditor] Attribute data length: {len(old_attr.data)}")
            
        if old_attr:
            for faceIndex in range(len(mesh.polygons)):
                try:
                    old_idx = old_attr.data[faceIndex].value
                    if old_idx >= 0 and old_idx < len(surfaces):
                        surf = surfaces[old_idx]
                        surf_type = surf.get("type", "") if isinstance(surf, dict) else ""
                        print(f"[CityJSONEditor] Face {faceIndex}: semantic_idx={old_idx}, type={surf_type}")
                        
                        if surf_type in ("Window", "Door"):
                            preserved_semantics[faceIndex] = old_idx
                            print(f"[CityJSONEditor] ✓ PRESERVING face {faceIndex} as {surf_type}")
                except Exception as e:
                    print(f"[CityJSONEditor] WARNING: Cannot read face {faceIndex}: {e}")
        
        print(f"[CityJSONEditor] ========================================")
        print(f"[CityJSONEditor] PRESERVED {len(preserved_semantics)} Window/Door faces")
        print(f"[CityJSONEditor] Preserved indices: {list(preserved_semantics.keys())}")
        print(f"[CityJSONEditor] ========================================\n")
        
        materialCleaner()
        matSlot = 0
        for faceIndex, face in enumerate(obj.data.polygons):
            # Check if this face was a Window/Door - preserve it
            if faceIndex in preserved_semantics:
                old_idx = preserved_semantics[faceIndex]
                surfaceType = surfaces[old_idx].get("type", "WallSurface")
                print(f"[CityJSONEditor] Face {faceIndex}: PRESERVED as {surfaceType} (idx={old_idx})")
                materialCreator(surfaceType, matSlot, faceIndex)
                attr.data[faceIndex].value = old_idx
                matSlot += 1
                continue
            
            # Calculate semantic type based on normal
            if math.isclose(face.normal[2] ,-1.0):
                surfaceType = "GroundSurface"
                materialCreator(surfaceType,matSlot,faceIndex)
                matSlot+=1
            elif math.isclose(face.normal[2],0,abs_tol=1e-3) or ((face.normal[2] < 0) and (math.isclose(face.normal[2],-1.0) == False)):
                surfaceType = "WallSurface"
                materialCreator(surfaceType,matSlot,faceIndex)
                matSlot+=1
            else:
                surfaceType = "RoofSurface"
                materialCreator(surfaceType,matSlot,faceIndex)
                matSlot+=1
            # map semantics list and attribute
            surface_idx = None
            for idx, surf in enumerate(surfaces):
                if isinstance(surf, dict) and surf.get("type") == surfaceType:
                    surface_idx = idx
                    break
            if surface_idx is None:
                surface_idx = len(surfaces)
                surfaces.append({"type": surfaceType})
            attr.data[faceIndex].value = surface_idx
        
        # Store as JSON (avoid ID property corruption)
        surfaces_clean = id_prop_to_dict(surfaces)
        obj["cj_semantic_surfaces"] = surfaces_clean
        obj["cj_dirty"] = True
        
        print(f"\n[CityJSONEditor] ========================================")
        print(f"[CityJSONEditor] CALCULATE SEMANTIC COMPLETE")
        print(f"[CityJSONEditor] Total surfaces stored: {len(surfaces_clean)}")
        print(f"[CityJSONEditor] ==========================================\n")
        
        return {'FINISHED'}

class SetActiveLODOperator(bpy.types.Operator):
    bl_idname = "wm.cje_set_active_lod"
    bl_label = "Set Active LoD"
    lod: bpy.props.FloatProperty(name="LoD", default=0.0)

    def execute(self, context):
        active = context.active_object
        if active is None or "cj_source_id" not in active:
            self.report({'WARNING'}, "Select a CityJSON object to switch LoD.")
            return {'CANCELLED'}
        source_id = active.get("cj_source_id", active.name.split("__")[0])
        for obj in context.scene.objects:
            if obj.get("cj_source_id") == source_id:
                obj.hide_set(obj.get("cj_lod") != self.lod)
        return {'FINISHED'}

class VIEW3D_MT_cityobject_lod_submenu(bpy.types.Menu):
    bl_label = 'LoD Switch'
    bl_idname = 'VIEW3D_MT_cityobject_lod_submenu'
    def draw(self, context):
        layout = self.layout
        active = context.active_object
        if active is None or "cj_source_id" not in active:
            layout.label(text="No CityJSON object selected")
            return
        source_id = active.get("cj_source_id", active.name.split("__")[0])
        lods = set()
        for obj in context.scene.objects:
            if obj.get("cj_source_id") == source_id and "cj_lod" in obj:
                lods.add(obj.get("cj_lod"))
        for lod_val in sorted(lods):
            layout.operator(SetActiveLODOperator.bl_idname, text=f"LoD {lod_val}").lod = float(lod_val)


