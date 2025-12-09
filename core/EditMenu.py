import bpy
from .FeatureTypes import FeatureTypes
from .Material import Material

class VIEW3D_MT_cityedit_mesh_context_submenu(bpy.types.Menu):
    bl_label = 'SurfaceTypes'
    bl_idname = 'VIEW3D_MT_cityedit_mesh_context_submenu'
    
    def draw(self, context):
        layout = self.layout
        obj = bpy.context.active_object
        try:
            constructionType = obj["cityJSONType"] 
            features = FeatureTypes()
            layout.label(text=constructionType)  
            for surface in features.getAllElementsOfFeatureType(constructionType):
                layout.operator(SetSurfaceOperator.bl_idname, text=surface).surfaceType = surface
        except:
            layout.label(text="set construction type in object mode or select object in object mode")  

class SetSurfaceOperator(bpy.types.Operator):
    bl_idname = "wm.set_surface"
    bl_label = "SetSurfaceOperator2"
    surfaceType: bpy.props.StringProperty(
        name = 'surfaceType',
        default = ''
    )

    def execute(self, context):
        obj = context.object
        if obj.type == 'MESH':
            mesh = obj.data # Assumed that obj.type == 'MESH'
            bpy.context.object.update_from_editmode()
            attr = mesh.attributes.get("cje_semantic_index")
            if attr is None:
                try:
                    attr = mesh.attributes.new(name="cje_semantic_index", type='INT', domain='FACE')
                except Exception:
                    attr = None
            surfaces = list(obj.get("cj_semantic_surfaces", []))
            # iterate faces
            for face in mesh.polygons:
                # get faces that are selected
                if face.select == True:
                    try:
                        material = bpy.context.object.active_material.name
                        #print("name of the surface's old material: "+ str(material))
                    except:
                        print("The Face does not have a Material or the Material has already been removed!")

                    # create the material as object
                    mat = Material(type=self.surfaceType, newObject=obj, objectID=obj.id_data.name, textureSetting=False, objectType=obj['cityJSONType'], surfaceIndex=None, surfaceValue=None, filepath=None, rawObjectData=None, geometry=None)
                    mat.createMaterial()                
                    # set the color of the material
                    mat.setColor()
                    # assign the new material to the face
                    mat.addMaterialToFace(bpy.context.object.active_material_index, face.index )
                    # track semantics surface index for export
                    surface_idx = None
                    for idx, surf in enumerate(surfaces):
                        if isinstance(surf, dict) and surf.get("type") == self.surfaceType:
                            surface_idx = idx
                            break
                    if surface_idx is None:
                        surface_idx = len(surfaces)
                        surfaces.append({"type": self.surfaceType})
                    try:
                        if attr:
                            attr.data[face.index].value = surface_idx
                        else:
                            face["cje_semantic_index"] = surface_idx
                    except Exception:
                        try:
                            face["cje_semantic_index"] = surface_idx
                        except Exception:
                            pass
                    obj["cj_dirty"] = True
            obj["cj_semantic_surfaces"] = surfaces
        
        # remove the now unused material-slots and delete the data-blocks
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.material_slot_remove_unused()
        bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=False, do_recursive=True)
        bpy.ops.object.mode_set(mode='EDIT')
      
        return {'FINISHED'}

    
