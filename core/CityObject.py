"""
CityObject import/export helpers: build Blender meshes from CityJSON and serialize back.
"""

import bpy
import numpy
import math
from .Mesh import Mesh
from .Material import Material
import time

class ImportCityObject:
    """Create Blender mesh/object instances from a CityJSON CityObject."""

    def __init__(self, object, vertices, objID, textureSetting, rawObjectData, filepath):
        # entire data of the object
        self.object = object
        # the object's mesh
        self.mesh = []
        # list of all vertices
        self.vertices = vertices
        # name/id of the object
        self.objectID = objID
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
        try:
            geoms = self.object.get("geometry") or []
            if geoms:
                lod_raw = geoms[0].get("lod")
                geom_lod = float(lod_raw) if lod_raw is not None else None
        except Exception:
            geom_lod = None
        self.objectLOD = math.floor(geom_lod) if geom_lod is not None else 0
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
        # assign gmlid/identifier/objectid if present in attributes
        attrs = self.object.get("attributes") or {}
        gmlid = attrs.get("gmlid") or attrs.get("identifier") or attrs.get("objectid")
        if gmlid is None:
            gmlid = self.objectID
        newObj['gmlid'] = gmlid
        # get the collection with the title "Collection"
        collection = bpy.data.collections.get("Collection")
        # add the new object to the collection
        collection.objects.link(newObj)
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
        for geom in self.object.get('geometry', []):
            if self.object['type']=='GenericCityObject':
                continue
            semantics = self._semantics_for_geometry(geom)
            if semantics is None:
                print(f"No semantics found for object '{self.objectID}'; skipping material assignment.")
                continue
            values = semantics.get("values", [[]])
            surfaces = semantics.get("surfaces", [])
            if not values or not isinstance(values, list) or not values[0]:
                raise ValueError(f"Semantics values missing for object '{self.objectID}'.")
            face_values = values[0]
            l = len(face_values)
            self.printProgressBar(0, l, prefix = 'Materials:', suffix = 'Complete', length = 50)
            for surfaceIndex, surfaceValue in enumerate(face_values):
                time_mat = time.time()
                surface_idx = surfaceValue if surfaceValue is not None else 0
                surface_idx = surface_idx if surface_idx < len(surfaces) else 0
                surface_type = surfaces[surface_idx].get("type", "WallSurface") if surfaces else "WallSurface"
                material = Material(surface_type, newObject, self.objectID, self.textureSetting, self.objectType, surfaceIndex, surface_idx, self.rawObjectData, self.filepath, geom )
                material.execute()
                time_needed = time.time() - time_mat
                # Update Progress Bar
                self.printProgressBar(surfaceIndex+1 , l, prefix = 'Materials:', suffix = 'Complete', length = 50, time='t/m: %.4f sec' % (time_needed))
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
        print("Mesh has been created!")
        # select the object to become active object
        bpy.data.objects[self.objectID].select_set(True)
        bpy.context.view_layer.objects.active = bpy.data.objects[self.objectID]
        # create the objects materials and assign them
        self.createMaterials(newObject)
        if self.textureSetting ==True:
            try:
                # UV Mapping of the textures
                self.uvMapping(newObject, self.rawObjectData, self.object['geometry'][0])
            except:
                    print("UV Mapping was not possible because the CityJSON file does not contain appearances!")
        else: pass

class ExportCityObject:
    """Serialize a Blender object back into a CityJSON CityObject."""
    def __init__(self, object, lastVertexIndex, jsonExport, textureSetting, textureReferenceList):
        self.object = object
        # all vertices of the current object
        self.vertices = []
        self.objID = self.object.name
        self.objType = self.object['cityJSONType']
        self.lod = self.object['LOD']
        self.maxValue = ""
        self.offsetArray = [bpy.context.scene.world['X_Origin'],bpy.context.scene.world['Y_Origin'],bpy.context.scene.world['Z_Origin']]
        self.objGeoExtent = []
        self.json = {}
        self.geometry = []
        self.lastVertexIndex = lastVertexIndex
        self.semanticValues = []
        self.scalefactor = 0.001
        self.jsonExport = jsonExport
        self.textureValues = []
        self.textureSetting = textureSetting
        self.counter = 0
        self.textureReferenceList = textureReferenceList


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

    def getBoundaries(self):
        # get the mesh by name
        mesh = bpy.data.meshes[self.objID]
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
        maxVertex = max([max(j) for j in [max(i) for i in boundaries]])
        self.lastVertexIndex = maxVertex
        self.geometry = [{
            "type": "Solid",
            "lod": self.lod,
            "boundaries" : [boundaries]
        }]

    def getSemantics(self):
        mesh = bpy.data.meshes[self.objID]
        self.semanticValues = []
        self.semanticSurfaces =[]
        # iterate through polygons
        for polyIndex, poly  in enumerate(mesh.polygons):
            # index of the material slot of the current polygon in blender
            blenderMaterialIndex = poly.material_index 

            # List of all polygons in index order from 0 to xxx
            self.semanticValues.append(polyIndex)
            
            # type of surface (semantic of surface) of current polygon
            semanticSurface = mesh.materials[blenderMaterialIndex]['CJEOtype']
            # List of semantics in reordered from blenders indices to order of polygon indices
            # example: polygon 4 has semanticValue index of 4 --> semantics of material that is in material slot 163 in blender is written at the current index of 4 in the semanticSurfaces list via append  
            self.semanticSurfaces.append({"type": semanticSurface})

            if self.textureSetting:
                # extract uv mapping
                self.getTextureMapping(mesh, poly, blenderMaterialIndex, polyIndex)

    def getTextureMapping(self, mesh, poly, semantic, polyIndex):
        #check if face has texture
        if len(mesh.materials[semantic].node_tree.nodes) > 2:
            print(str(polyIndex) + " has texture!")
            #face_material = semantic - self.counter

            # index of texture in appearances section of CityJSON
            # name of the image of the material
            faceMaterial = mesh.materials[semantic].node_tree.nodes['Image Texture'].image.name
            textureIndex =  self.textureReferenceList.index(faceMaterial)

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
        self.json = {self.objID : {"type": self.objType}}
        if self.objType == 'GenericCityObject':
            pass
        else:
            self.geometry[0].update({"semantics" : {"values" : [self.semanticValues],"surfaces" : self.semanticSurfaces}})
        if self.textureSetting: 
            self.geometry[0].update({"texture" : {"default" : { "values" : [self.textureValues] }}})
        self.json[self.objID].update({"geometry" : self.geometry})
        
    def execute(self):
        self.getVertices()
        self.getObjectExtend()
        self.getBoundaries()
        if self.objType == 'GenericCityObject':
            pass
        else: 
            self.getSemantics()
        self.createJSON()
        
