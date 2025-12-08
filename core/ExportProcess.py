import json
import bpy
import os
import shutil
from .CityObject import ExportCityObject

class ExportProcess:
    
    def __init__(self, filepath, textureSetting):
        self.filepath = filepath
        self.jsonExport = None
        # True - export textures
        # False - do not export textures
        self.textureSetting = textureSetting
        self.textureReferenceList = []

    def createJSONStruct(self):
        if self.textureSetting: 
            emptyJSON = {
                "type": "CityJSON",
                "version": "2.0",
                "CityObjects": {},
                "transform":{
                    "scale":[
                        0.001, 
                        0.001,
                        0.001
                        ],
                    "translate":[]
                },
                "vertices": None,
                "appearance":{
                    "textures":[],
                    "vertices-texture":[]
                },
                "metadata": {}
            }
        else: 
            emptyJSON = {
                "type": "CityJSON",
                "version": "2.0",
                "CityObjects": {},
                "transform":{
                    "scale":[
                        0.001, 
                        0.001,
                        0.001
                        ],
                    "translate":[]
                },
                "vertices": None,
                "metadata": {}
            }
        self.jsonExport = emptyJSON
    
    def getMetadata(self):
        crs = None
        try:
            crs = bpy.context.scene.world['CRS']
        except Exception:
            crs = None
        if crs is not None:
            self.jsonExport.setdefault("metadata", {}).update({"referenceSystem" : str(crs)})

    def getTransform(self):
        try:
            translate = [
                bpy.context.scene.world['X_Origin'],
                bpy.context.scene.world['Y_Origin'],
                bpy.context.scene.world['Z_Origin'],
            ]
        except Exception:
            translate = [0, 0, 0]
        self.jsonExport["transform"]["translate"] = translate

    def getTextures(self):
        allTextures = bpy.data.images
        for texture in allTextures:
            imageType = texture.file_format
            if imageType == 'TARGA':
                pass
            else:
                basename = texture.name
                imageName = "appearance/" + basename
                textureJSON = {
                    "type": imageType,
                    "image": imageName,
                    "wrapMode":"wrap",
                    "textureType":"specific",
                    "borderColor":[
                    0.0,
                    0.0,
                    0.0,
                    1.0
                    ]
                }
                self.jsonExport['appearance']['textures'].append(textureJSON)
                self.textureReferenceList.append(basename)
                self.exportTextures(texture)

    def getVerticesTexture(self):
        meshes = bpy.data.meshes
       
        for mesh in meshes:
            if not mesh.uv_layers:
                raise RuntimeError(f"Mesh '{mesh.name}' has textures enabled for export but no UV layers.")
            uv_layer = mesh.uv_layers[0].data
            for polyIndex, poly  in enumerate(mesh.polygons):
                semantic = poly.material_index
                if semantic >= len(mesh.materials):
                    raise RuntimeError(f"Polygon {polyIndex} in mesh '{mesh.name}' references missing material index {semantic}.")
                loopTotal = poly.loop_total
                if len(mesh.materials[semantic].node_tree.nodes) > 2:
                    for loop_index in range(poly.loop_start, poly.loop_start + loopTotal):
                        uv = uv_layer[loop_index].uv
                        u = uv[0]
                        v = uv[1]
                        vertices_textureJSON = [round(u,7),
                                                round(v,7)]
                        self.jsonExport['appearance']['vertices-texture'].append(vertices_textureJSON)
                else: 
                    pass

    def createCityObject(self):
        vertexArray = []
        blendObjects = bpy.data.objects
        lastVertexIndex = 0
        for object in blendObjects:
            print("Create Export-Object: "+object.name)
            cityobj = ExportCityObject(object, lastVertexIndex, self.jsonExport, self.textureSetting, self.textureReferenceList)
            cityobj.execute()
            for vertex in cityobj.vertices:
                vertex[0] = round(vertex[0]/0.001)
                vertex[1] = round(vertex[1]/0.001)
                vertex[2] = round(vertex[2]/0.001)
                vertexArray.append(vertex)
            self.jsonExport["CityObjects"].update(cityobj.json)
            lastVertexIndex = cityobj.lastVertexIndex + 1
            print("lastVertexIndex "+str(lastVertexIndex))
        self.jsonExport['vertices'] = vertexArray

    def exportTextures(self, texture):
        fileSourceInfos = texture.filepath.split('\\')
        fileSourceName = fileSourceInfos[ len(fileSourceInfos) - 1 ]
        folderSource = texture.filepath.replace(fileSourceInfos[ len(fileSourceInfos) - 1 ],"")
        
        fileInfosTarget =self.filepath.split('\\')
        folderTarget =self.filepath.replace(fileInfosTarget[ len(fileInfosTarget) - 1 ],"")
        
        src_path = folderSource.replace("//","") + fileSourceName
        dst_path = folderTarget + r"appearance\\" + fileSourceName
        
        # create parent path for appearance
        path = os.path.join(folderTarget, 'appearance')
        if not os.path.exists(path):
            os.mkdir(path)
        shutil.copy((r'%s' %src_path), (r'%s' %dst_path))
    
    def writeData(self):
        with open(self.filepath, 'w', encoding='utf-8') as f:
            filecontent = json.dumps(self.jsonExport)
            f.write(filecontent)

    def updateMetadataExtent(self):
        vertices = self.jsonExport.get("vertices") or []
        if not vertices:
            return
        transform = self.jsonExport.get("transform") or {}
        scale = transform.get("scale") or [1,1,1]
        translate = transform.get("translate") or [0,0,0]
        actual_coords = []
        for v in vertices:
            actual_coords.append([
                v[0]*scale[0] + translate[0],
                v[1]*scale[1] + translate[1],
                v[2]*scale[2] + translate[2],
            ])
        if not actual_coords:
            return
        min_vals = [min(coord[i] for coord in actual_coords) for i in range(3)]
        max_vals = [max(coord[i] for coord in actual_coords) for i in range(3)]
        self.jsonExport.setdefault("metadata", {})["geographicalExtent"] = [
            round(min_vals[0],3), round(min_vals[1],3), round(min_vals[2],3),
            round(max_vals[0],3), round(max_vals[1],3), round(max_vals[2],3)
        ]

    def execute(self):
        print('##########################')
        print('### STARTING EXPORT... ###')
        print('##########################')

        self.createJSONStruct()
        self.getMetadata()
        self.getTransform()
        if self.textureSetting: 
            self.getTextures()
            self.getVerticesTexture()
        self.createCityObject()
        self.updateMetadataExtent()
        self.writeData()

        print('########################')
        print('### EXPORT FINISHED! ###')
        print('########################')
        return {'FINISHED'}
