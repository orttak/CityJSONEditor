"""
CityObject import/export helpers: build Blender meshes from CityJSON and serialize back.
"""

import bpy
import numpy
import math
from .Mesh import Mesh
from .Material import Material
import time
import json
import copy

class ImportCityObject:
    """Create Blender mesh/object instances from a CityJSON CityObject."""

    def __init__(self, object, vertices, objID, textureSetting, rawObjectData, filepath, source_id=None, geom_index=0):
        # entire data of the object
        self.object = object
        # the object's mesh
        self.mesh = []
        # list of all vertices
        self.vertices = vertices
        # name/id of the object
        self.objectID = objID
        self.source_id = source_id or objID.split("__")[0]
        self.geom_index = geom_index
        # Import-setting which lets the user choose if textures present in the CityJSON should be imported
        # True - import textures
        # False - do not import textures
        self.textureSetting = textureSetting
        # materials of the object which encode the objects face semantics
        self.materials = []
        # type of the given object e.g. "Building" or "Bridge" etc.
        self.objectType = self.object['type']
        # LOD of the given object
        geom_lod = None
        geoms = []
        try:
            geoms = self.object.get("geometry") or []
            if geoms:
                lod_raw = geoms[0].get("lod")
                geom_lod = float(lod_raw) if lod_raw is not None else None
        except Exception:
            geom_lod = None
        self.objectLOD = geom_lod if geom_lod is not None else 0
        self.has_semantics = any((g.get("semantics") is not None) for g in geoms)
        # entire Data of the file
        self.rawObjectData = rawObjectData
        # File to be imported
        self.filepath = filepath

    # Print iterations progress
    def printProgressBar (self, iteration, total, prefix = '', suffix = '', decimals = 1, length = 100, fill = '█', printEnd = "\r",time = ''):
        """
        Call in a loop to create terminal progress bar
        @params:
            iteration   - Required  : current iteration (Int)
            total       - Required  : total iterations (Int)
            prefix      - Optional  : prefix string (Str)
            suffix      - Optional  : suffix string (Str)
            decimals    - Optional  : positive number of decimals in percent complete (Int)
            length      - Optional  : character length of bar (Int)
            fill        - Optional  : bar fill character (Str)
            printEnd    - Optional  : end character (e.g. "\r", "\r\n") (Str)
        """
        percent = ("{0:." + str(decimals) + "f}").format(100 * (iteration / float(total)))
        filledLength = int(length * iteration // total)
        bar = fill * filledLength + '-' * (length - filledLength)
        print(f'\r{prefix} |{bar}| {percent}% ({iteration}/{total}) {suffix}  {time}', end = printEnd)
        # Print New Line on Complete
        if iteration == total: 
            print()

    def createMesh(self, object, vertices, oid):
        # create the objects mesh and store the data
        mesh = Mesh(object,vertices,oid)
        self.mesh = mesh.execute()

    def createObject(self, mesh):
        # create a new object with the stored mesh
        newObj = bpy.data.objects.new(self.objectID, mesh)
        # create a custom property of the object to save its type and LOD
        newObj['cityJSONType'] = self.objectType
        newObj['LOD'] = self.objectLOD
        newObj['cj_has_semantics'] = self.has_semantics
        # assign gmlid/identifier/objectid if present in attributes
        attrs = self.object.get("attributes") or {}
        gmlid = attrs.get("gmlid") or attrs.get("identifier") or attrs.get("objectid")
        if gmlid is None:
            gmlid = self.objectID
        newObj['gmlid'] = gmlid
        # store structured data for editing instead of raw JSON caching
        try:
            geoms = self.object.get("geometry") or []
            geom = geoms[0] if geoms else {}
            newObj["cj_geometry_type"] = geom.get("type", "Solid")
            newObj["cj_semantic_surfaces"] = (geom.get("semantics") or {}).get("surfaces") or []
        except Exception as exc:
            print(f"[CityJSONEditor] Warning: failed to store geometry metadata for '{self.objectID}': {exc}")
        try:
            newObj["cj_attributes"] = copy.deepcopy(attrs)
        except Exception as exc:
            print(f"[CityJSONEditor] Warning: failed to store attributes for '{self.objectID}': {exc}")
        newObj["cj_source_id"] = self.source_id
        newObj["cj_geom_index"] = self.geom_index
        newObj["cj_lod"] = self.objectLOD
        newObj["cj_dirty"] = False
        # get the collection with the title "Collection"
        # Add to LOD-based collection for better organization
        lod = int(self.objectLOD) if self.objectLOD else 0
        lod_coll_name = f"LOD_{lod}"
        
        # Ensure LOD collection exists
        if lod_coll_name not in bpy.data.collections:
            lod_coll = bpy.data.collections.new(lod_coll_name)
            bpy.context.scene.collection.children.link(lod_coll)
        else:
            lod_coll = bpy.data.collections[lod_coll_name]
        
        # Link object to LOD collection
        lod_coll.objects.link(newObj)
        
        return newObj

    def _semantics_for_geometry(self, geom):
        semantics = geom.get("semantics") if isinstance(geom, dict) else None
        boundaries = geom.get("boundaries") or []
        face_count = 0
        if geom.get("type") == "Solid":
            for shell in boundaries:
                face_count += len(shell)
        else:
            face_count = len(boundaries)
        if semantics is None:
            return None
        if not isinstance(semantics, dict):
            raise ValueError(f"Semantics must be an object for '{self.objectID}'.")
        values = semantics.get("values")
        surfaces = semantics.get("surfaces")
        if values and not isinstance(values, list):
            raise ValueError(f"Semantics values invalid for '{self.objectID}'.")
        if values and not values[0]:
            raise ValueError(f"Semantics values empty for '{self.objectID}'.")
        if values and not surfaces:
            raise ValueError(f"Semantics surfaces missing for '{self.objectID}'.")
        first = values[0] if values else []
        if face_count and first and len(first) != face_count:
            raise ValueError(f"Semantic values count ({len(first)}) does not match face count ({face_count}) for object '{self.objectID}'.")
        return semantics if values else None


    def createMaterials(self, newObject):
        if not self.has_semantics:
            return
        mesh_data = newObject.data
        try:
            attr = mesh_data.attributes.get("cje_semantic_index")
            if attr is None:
                attr = mesh_data.attributes.new(name="cje_semantic_index", type='INT', domain='FACE')
        except Exception:
            attr = None
        for geom in self.object.get('geometry', []):
            if self.object['type']=='GenericCityObject':
                continue
            semantics = self._semantics_for_geometry(geom)
            if semantics is None:
                print(f"No semantics found for object '{self.objectID}'; skipping material assignment.")
                continue
            values = semantics.get("values", [[]])
            surfaces = semantics.get("surfaces", [])
            try:
                newObject["cj_semantic_surfaces"] = surfaces
            except Exception:
                pass
            if not values or not isinstance(values, list) or not values[0]:
                raise ValueError(f"Semantics values missing for object '{self.objectID}'.")
            face_values = values[0]
            l = len(face_values)
            # self.printProgressBar(0, l, prefix = 'Materials:', suffix = 'Complete', length = 50)
            for surfaceIndex, surfaceValue in enumerate(face_values):
                time_mat = time.time()
                surface_idx = surfaceValue if surfaceValue is not None else 0
                surface_idx = surface_idx if surface_idx < len(surfaces) else 0
                surface_type = surfaces[surface_idx].get("type", "WallSurface") if surfaces else "WallSurface"
                material = Material(surface_type, newObject, self.objectID, self.textureSetting, self.objectType, surfaceIndex, surface_idx, self.rawObjectData, self.filepath, geom )
                material.execute()
                stored_value = surfaceValue if surfaceValue is not None else -1
                try:
                    if attr:
                        attr.data[surfaceIndex].value = stored_value
                    else:
                        newObject.data.polygons[surfaceIndex]["cje_semantic_index"] = stored_value
                except Exception:
                    try:
                        newObject.data.polygons[surfaceIndex]["cje_semantic_index"] = stored_value
                    except Exception:
                        pass
                time_needed = time.time() - time_mat
                # Update Progress Bar
                # self.printProgressBar(surfaceIndex+1 , l, prefix = 'Materials:', suffix = 'Complete', length = 50, time='t/m: %.4f sec' % (time_needed))
            if not l:
                raise ValueError(f"No semantic values found for object '{self.objectID}'.")
            
    def uvMapping(self, object, data, geom):

        texture_block = geom.get("texture") or {}
        if not texture_block or "appearance" not in data:
            raise RuntimeError(f"Texture block/appearance missing for object '{self.objectID}'.")
        themeNames = list(texture_block.keys())
        if not themeNames:
            raise RuntimeError(f"No texture themes found for object '{self.objectID}'.")
        themeName = themeNames[0]

        # uv coordinates from json file
        uv_coords = (data.get('appearance') or {}).get('vertices-texture')
        if not uv_coords:
            raise RuntimeError(f"Texture vertices missing for object '{self.objectID}'.")
        # all data from the json file
        mesh_data = object.data
        # create a new uv layer
        # this uv-unwraps all faces even if they don't have a texture (is irrelevent though)
        uv_layer = mesh_data.uv_layers.new()
        # set the new uv layer as the active layer
        mesh_data.uv_layers.active = uv_layer

        # iterate through faces
        values = (texture_block.get(themeName) or {}).get("values") or []
        if not values or not values[0]:
            raise RuntimeError(f"Texture values missing for object '{self.objectID}'.")
        for face_index, face in enumerate(values[0]):
            # if the face has a texture (texture reference is not none)
            if face != [[None]]:
                # get the polygon/face from the newly created mesh
                poly = mesh_data.polygons[face_index]
                # iterate through the mesh-loops of the polygon/face
                for vert_idx, loop_idx in enumerate(poly.loop_indices):
                    # get the index of the uv that belongs to the vertex of the face
                    # this is mapped using the values in the geom['texture'][theme_name]['values'], where the value at index 0 is the
                    # index of the cooresponding texture-appearance, which means that the index of the vertex has to be increased by 1
                    texture_map_value = face[0][vert_idx+1]
                    # set UVs of the uv-layer using the texture_map_value as index for the list in the json data
                    uv_layer.data[loop_idx].uv = (uv_coords[texture_map_value][0],uv_coords[texture_map_value][1])
            
            # if there is no texture --> do nothing  
            else:
                pass


    def execute(self):
        self.createMesh(self.object, self.vertices, self.objectID)
        newObject = self.createObject(self.mesh)
        # select the object
        newObject.select_set(True)
        bpy.context.view_layer.objects.active = newObject
        # create the objects materials and assign them
        self.createMaterials(newObject)
        geoms = self.object.get('geometry') or []
        if self.textureSetting == True and geoms:
            try:
                # UV Mapping of the textures
                self.uvMapping(newObject, self.rawObjectData, geoms[0])
            except:
                if not getattr(bpy.types.Scene, "cje_warned_uv", False):
                    print("[CityJSONEditor] UV Mapping was not possible for some objects.")
                    bpy.types.Scene.cje_warned_uv = True
        else: pass

class ExportCityObject:
    """Serialize a Blender object back into a CityJSON CityObject."""
    def __init__(self, object, lastVertexIndex, jsonExport, textureSetting, textureReferenceList):
        self.object = object
        # all vertices of the current object
        self.vertices = []
        self.objID = self.object.name
        self.export_id = self.object.get("cj_source_id", self.objID.split("__")[0])
        self.objType = self.object.get('cityJSONType', "Building")
        lod_raw = self.object.get('LOD', 0)
        try:
            self.lod = float(lod_raw)
        except Exception:
            self.lod = 0
        self.maxValue = ""
        try:
            self.offsetArray = [bpy.context.scene.world['X_Origin'],bpy.context.scene.world['Y_Origin'],bpy.context.scene.world['Z_Origin']]
        except Exception:
            self.offsetArray = [0,0,0]
        self.objGeoExtent = []
        self.json = {}
        self.geometry = []
        self.lastVertexIndex = lastVertexIndex
        self.semanticValues = []
        self.semanticSurfaces = []
        self.scalefactor = 0.001
        self.jsonExport = jsonExport
        self.textureValues = []
        self.textureSetting = textureSetting
        self.counter = 0
        self.textureReferenceList = textureReferenceList
        self.geometry_type = self.object.get("cj_geometry_type", "Solid")
        self.source_semantics = {"surfaces": []}
        try:
            stored_surfaces = self.object.get("cj_semantic_surfaces", [])
            if isinstance(stored_surfaces, list):
                self.source_semantics["surfaces"] = copy.deepcopy(stored_surfaces)
        except Exception:
            self.source_semantics = {"surfaces": []}
        self.is_dirty = bool(self.object.get("cj_dirty", False))
        try:
            self.has_semantics = bool(self.object.get("cj_has_semantics", bool(self.source_semantics.get("surfaces"))))
        except Exception:
            self.has_semantics = bool(self.source_semantics.get("surfaces"))
        self.include_semantics = self.has_semantics or self.is_dirty
        try:
            self.attributes = copy.deepcopy(self.object.get("cj_attributes", {}))
        except Exception:
            self.attributes = {}
        try:
            self.geom_index = int(self.object.get("cj_geom_index", 0))
        except Exception:
            self.geom_index = 0


    def getVertices(self):
        vertexArray = []
        vertices = self.object.data.vertices
        for vertex in vertices:
            vertexCoordinates = vertex.co
            vertexJSON = []
            vertexJSON.append(vertexCoordinates[0])
            vertexJSON.append(vertexCoordinates[1])
            vertexJSON.append(vertexCoordinates[2])
            vertexArray.append(vertexJSON)
        self.vertices = vertexArray

    def getObjectExtend(self):
        objGeoExtend = []
        vertices = numpy.asarray(self.vertices)
        maxValue = vertices.max(axis=0, keepdims=True)[0]
        maxValue = maxValue+self.offsetArray
        minValue = vertices.min(axis=0, keepdims=True)[0]
        minValue = minValue+self.offsetArray
        for i in minValue:
            objGeoExtend.append(round(i,3))
        for i in maxValue:
            objGeoExtend.append(round(i,3))
        self.objGeoExtent = objGeoExtend

    def _surface_key(self, surface):
        try:
            return json.dumps(surface, sort_keys=True)
        except Exception:
            return str(surface.get("type", ""))

    def _surface_from_source(self, surface_type):
        surfaces = (self.source_semantics.get("surfaces") or {}) if isinstance(self.source_semantics, dict) else []
        if isinstance(surfaces, list):
            for surf in surfaces:
                if isinstance(surf, dict) and surf.get("type") == surface_type:
                    return copy.deepcopy(surf)
        return {"type": surface_type}

    def getBoundaries(self):
        # get the mesh by name
        mesh = bpy.data.meshes.get(self.objID)
        if not mesh or not mesh.polygons:
            self.geometry = []
            return
        
        boundaries = []
        # iterate through polygons
        for poly in mesh.polygons:
            loop = []
            # iterate through loops inside polygons
            # get the vertex coordinates and find the Index in the corresponding list of all vertices
            for loop_index in poly.loop_indices:
                vertexIndex = mesh.loops[loop_index].vertex_index
                vertexValue = []
                vertexValue.append(mesh.vertices[vertexIndex].co[0])
                vertexValue.append(mesh.vertices[vertexIndex].co[1])
                vertexValue.append(mesh.vertices[vertexIndex].co[2])
                exportIndex = self.vertices.index(vertexValue)
                # close the loop
                loop.append(exportIndex+self.lastVertexIndex)
            boundaries.append([loop])
        if boundaries:
            maxVertex = max([max(j) for j in [max(i) for i in boundaries]])
            self.lastVertexIndex = maxVertex
        geom_type = self.geometry_type if self.geometry_type in ("Solid", "MultiSurface") else "Solid"
        geom_entry = {
            "type": geom_type,
            "lod": f"{float(self.lod):g}",
        }
        if geom_type == "MultiSurface":
            geom_entry["boundaries"] = boundaries
        else:
            geom_entry["boundaries"] = [boundaries]
        self.geometry = [geom_entry]

    def getSemantics(self):
        if not self.include_semantics:
            self.semanticValues = []
            self.semanticSurfaces = []
            return
        mesh = bpy.data.meshes[self.objID]
        # Clear local semantic values so they don't leak into subsequent objects if this one had no geometry
        self.semanticValues = []
        self.semanticSurfaces = [copy.deepcopy(s) for s in (self.source_semantics.get("surfaces") or [])] if isinstance(self.source_semantics, dict) else []
        surface_lookup = {self._surface_key(s): idx for idx, s in enumerate(self.semanticSurfaces)}
        attr = None
        try:
            attr = mesh.attributes.get("cje_semantic_index")
        except Exception:
            attr = None
        # iterate through polygons
        for polyIndex, poly  in enumerate(mesh.polygons):
            # index of the material slot of the current polygon in blender
            blenderMaterialIndex = poly.material_index 
            semanticSurface = "WallSurface"
            if blenderMaterialIndex < len(mesh.materials) and mesh.materials[blenderMaterialIndex]:
                mat = mesh.materials[blenderMaterialIndex]
                try:
                    semanticSurface = mat.get('CJEOtype', semanticSurface)
                except Exception:
                    semanticSurface = semanticSurface
            stored_idx = None
            if attr:
                try:
                    stored_idx = attr.data[polyIndex].value
                except Exception:
                    stored_idx = None
            if stored_idx is None:
                try:
                    stored_idx = poly.get("cje_semantic_index")
                except Exception:
                    try:
                        stored_idx = poly["cje_semantic_index"]
                    except Exception:
                        stored_idx = None
            surface_idx = None
            if stored_idx is not None:
                if stored_idx == -1:
                    self.semanticValues.append(None)
                else:
                    try:
                        surface_idx = int(stored_idx)
                    except Exception:
                        surface_idx = None
                    if surface_idx is not None:
                        while surface_idx >= len(self.semanticSurfaces):
                            new_surface = self._surface_from_source(semanticSurface)
                            self.semanticSurfaces.append(new_surface)
                            surface_lookup[self._surface_key(new_surface)] = len(self.semanticSurfaces) - 1
                        self.semanticValues.append(surface_idx)
                    else:
                        self.semanticValues.append(None)
            else:
                key = self._surface_key({"type": semanticSurface})
                if key in surface_lookup:
                    surface_idx = surface_lookup[key]
                else:
                    surface_idx = len(self.semanticSurfaces)
                    new_surface = self._surface_from_source(semanticSurface)
                    self.semanticSurfaces.append(new_surface)
                    surface_lookup[key] = surface_idx
                self.semanticValues.append(surface_idx)

            if self.textureSetting and blenderMaterialIndex < len(mesh.materials):
                # extract uv mapping
                self.getTextureMapping(mesh, poly, blenderMaterialIndex, polyIndex)

    def getTextureMapping(self, mesh, poly, semantic, polyIndex):
        #check if face has texture
        if len(mesh.materials[semantic].node_tree.nodes) > 2:
            print(str(polyIndex) + " has texture!")
            #face_material = semantic - self.counter

            # index of texture in appearances section of CityJSON
            # name of the image of the material
            img = mesh.materials[semantic].node_tree.nodes['Image Texture'].image
            if not img:
                print(str(polyIndex) + " has texture node but NO image assigned!")
                self.textureValues.append([[None]])
                return
            faceMaterial = img.name
            try:
                textureIndex =  self.textureReferenceList.index(faceMaterial)
            except ValueError:
                print(f"[CityJSONEditor] Image '{faceMaterial}' not found in textureReferenceList. Skipping.")
                self.textureValues.append([[None]])
                return

            # number of loops in the polygon (is equal to vertices)
            loopTotal = poly.loop_total
            uv_layer = mesh.uv_layers[0].data
            uvList = self.jsonExport['appearance']['vertices-texture']
            #self.textureValues.append([[face_material]])
            self.textureValues.append([[textureIndex]])

            for loop_index in range(poly.loop_start, poly.loop_start + loopTotal):
                uv = uv_layer[loop_index].uv
                u = uv[0]
                v = uv[1]
                vertices_textureJSON = [round(u,7),
                                        round(v,7)]
                uv_index = uvList.index(vertices_textureJSON)
                self.textureValues[polyIndex][0].append(uv_index) 
        else:
            print(str(polyIndex) + " does NOT have texture!")
            self.textureValues.append([[None]])
            self.counter =+ 1

    def createJSON(self):
        base = {}
        base["type"] = self.objType
        base["attributes"] = self.attributes if isinstance(self.attributes, dict) else {}
        if "gmlid" in self.object and "gmlid" not in base["attributes"]:
            try:
                base["attributes"]["gmlid"] = self.object["gmlid"]
            except Exception:
                pass
        has_semantics = any(v is not None for v in self.semanticValues) or bool(self.semanticSurfaces)
        if self.include_semantics and self.objType != 'GenericCityObject' and has_semantics:
            sem_values = self.semanticValues
            if self.geometry and self.geometry[0].get("type") == "Solid":
                sem_values = [self.semanticValues]
            self.geometry[0].update({"semantics" : {"values" : sem_values,"surfaces" : self.semanticSurfaces}})
        
        # Only include texture if we have at least one non-null mapping (integer index)
        def has_any_int(item):
            if isinstance(item, int):
                return True
            if isinstance(item, list):
                return any(has_any_int(sub) for sub in item)
            return False

        has_valid_texture = has_any_int(self.textureValues)
        
        if has_valid_texture:
            self.geometry[0].update({"texture" : {"default" : { "values" : [self.textureValues] }}})
        
        if self.geometry:
            for geom in self.geometry:
                if "lod" in geom:
                    try:
                        geom["lod"] = f"{float(geom['lod']):g}"
                    except (ValueError, TypeError):
                        geom["lod"] = str(geom["lod"])
        base["geometry"] = self.geometry
        self.json = {self.export_id : base}
        
    def execute(self):
        self.getVertices()
        self.getObjectExtend()
        self.getBoundaries()
        if self.objType == 'GenericCityObject':
            pass
        elif self.include_semantics:
            self.getSemantics()
        self.createJSON()
        return self.export_id, self.json[self.export_id]
        
