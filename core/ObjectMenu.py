import bpy
from .FeatureTypes import FeatureTypes
from .Material import Material
import math

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

        # if initial attributes are not already set, do so now
        try: 
            obj = bpy.context.active_object
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
        attr = mesh.attributes.get("cje_semantic_index")
        if attr:
             mesh.attributes.remove(attr)
        attr = mesh.attributes.new(name="cje_semantic_index", type='INT', domain='FACE')
        
        # Verify attribute validity
        if len(attr.data) != len(mesh.polygons):
             # Try one more update and recreate if needed, otherwise abort to avoid crash
             mesh.update()
             if attr: mesh.attributes.remove(attr)
             attr = mesh.attributes.new(name="cje_semantic_index", type='INT', domain='FACE')
             if len(attr.data) != len(mesh.polygons):
                 self.report({'ERROR'}, f"Mesh attribute mismatch: {len(attr.data)} vs {len(mesh.polygons)}. Try regularizing geometry.")
                 return {'CANCELLED'}

        surfaces = list(obj.get("cj_semantic_surfaces", []))
        materialCleaner()
        matSlot = 0
        for faceIndex, face in enumerate(obj.data.polygons):
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
        obj["cj_semantic_surfaces"] = surfaces
        obj["cj_dirty"] = True
        
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


