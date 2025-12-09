"""
CityJSON export pipeline used by the Blender operator.
Collects scene data, validates, and writes CityJSON 2.0.
"""

import json
import copy
import bpy
import os
import shutil
from .CityObject import ExportCityObject

class ExportProcess:
    """Handles CityJSON export from Blender objects to file."""

    def __init__(self, filepath, textureSetting, skip_failed_exports=True, patch_baseline=False, export_changed_only=False):
        self.filepath = filepath
        self.jsonExport = None
        # True - export textures
        # False - do not export textures
        self.textureSetting = textureSetting
        self.textureReferenceList = []
        self.skip_failed_exports = skip_failed_exports
        self.patch_baseline = patch_baseline
        self.export_changed_only = export_changed_only
        self.skipped_objects = []
        self.baseline_data = self._load_baseline()

    def _load_baseline(self):
        txt = bpy.data.texts.get("CJE_BASELINE")
        if not txt:
            return None
        try:
            content = txt.as_string()
            return json.loads(content)
        except Exception:
            return None

    def createJSONStruct(self):
        meta = {}
        version = "2.0"
        try:
            meta = copy.deepcopy(bpy.context.scene.get("cj_metadata", {}))
        except Exception:
            meta = {}
        try:
            version = str(bpy.context.scene.get("cj_version", "2.0"))
        except Exception:
            version = "2.0"
        base = {
            "type": "CityJSON",
            "version": version or "2.0",
            "CityObjects": {},
            "transform": {
                "scale": [0.001, 0.001, 0.001],
                "translate": [],
            },
            "vertices": None,
            "metadata": meta if isinstance(meta, dict) else {},
        }
        # always start with clean geometry/appearance containers
        base["CityObjects"] = {}
        base["vertices"] = None
        base.setdefault("transform", {}).setdefault("scale", [0.001, 0.001, 0.001])
        base["transform"].setdefault("translate", [])
        if self.textureSetting:
            app = base.get("appearance") or {}
            app["textures"] = []
            app["vertices-texture"] = []
            base["appearance"] = app
        else:
            if "appearance" in base:
                del base["appearance"]
        self.jsonExport = base
    
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
        try:
            scale = [
                bpy.context.scene.world.get('Scale_X', 0.001),
                bpy.context.scene.world.get('Scale_Y', 0.001),
                bpy.context.scene.world.get('Scale_Z', 0.001),
            ]
        except Exception:
            scale = [0.001, 0.001, 0.001]
        self.jsonExport.setdefault("transform", {})
        self.jsonExport["transform"]["scale"] = scale
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
                print(f"[CityJSONEditor] Skipping texture export for '{mesh.name}': no UV layers.")
                continue
            uv_layer = mesh.uv_layers[0].data
            for polyIndex, poly  in enumerate(mesh.polygons):
                semantic = poly.material_index
                if semantic >= len(mesh.materials):
                    print(f"[CityJSONEditor] Skipping texture on poly {polyIndex} in '{mesh.name}': material index {semantic} missing.")
                    continue
                loopTotal = poly.loop_total
                mat = mesh.materials[semantic]
                node_tree = getattr(mat, "node_tree", None)
                if node_tree and len(node_tree.nodes) > 2:
                    for loop_index in range(poly.loop_start, poly.loop_start + loopTotal):
                        uv = uv_layer[loop_index].uv
                        u = uv[0]
                        v = uv[1]
                        vertices_textureJSON = [round(u,7),
                                                round(v,7)]
                        self.jsonExport['appearance']['vertices-texture'].append(vertices_textureJSON)
                else:
                    # No texture nodes; skip quietly
                    continue

    def createCityObject(self):
        baseline_cityobjects = (self.baseline_data.get("CityObjects") or {}) if self.baseline_data else {}
        baseline_vertices = (self.baseline_data.get("vertices") or []) if (self.baseline_data and self.export_changed_only) else []
        vertexArray = list(baseline_vertices)
        blendObjects = [obj for obj in bpy.data.objects if getattr(obj, "type", "") == "MESH"]
        lastVertexIndex = len(vertexArray)
        scale = (self.jsonExport.get("transform") or {}).get("scale") or [0.001, 0.001, 0.001]
        if len(scale) != 3:
            scale = [0.001, 0.001, 0.001]
        scale = [s if s not in (None, 0) else 0.001 for s in scale]
        grouped = copy.deepcopy(baseline_cityobjects) if self.export_changed_only and baseline_cityobjects else {}

        objs = []
        for object in blendObjects:
            if "cityJSONType" not in object:
                continue
            export_id = object.get("cj_source_id", object.name.split("__")[0])
            objs.append((export_id, object))

        dirty_ids = set()
        if self.export_changed_only:
            for export_id, obj in objs:
                dirty = obj.get("cj_dirty", False) or export_id not in baseline_cityobjects
                if dirty:
                    dirty_ids.add(export_id)

        for export_id, object in objs:
            if self.export_changed_only and dirty_ids and export_id not in dirty_ids:
                continue
            print("Create Export-Object: "+object.name)
            try:
                cityobj = ExportCityObject(object, lastVertexIndex, self.jsonExport, self.textureSetting, self.textureReferenceList)
                export_id, base_obj = cityobj.execute()
            except Exception as exc:
                if self.skip_failed_exports:
                    print(f"[CityJSONEditor] Skipping export of '{object.name}': {exc}")
                    self.skipped_objects.append((object.name, str(exc)))
                    continue
                raise
            for vertex in cityobj.vertices:
                vertex[0] = round(vertex[0]/scale[0])
                vertex[1] = round(vertex[1]/scale[1])
                vertex[2] = round(vertex[2]/scale[2])
                vertexArray.append(vertex)
            lastVertexIndex = cityobj.lastVertexIndex + 1
            entry = grouped.get(export_id)
            if entry is None or self.export_changed_only:
                entry = {"type": base_obj["type"], "attributes": base_obj.get("attributes", {}), "geometry": []}
                grouped[export_id] = entry
            elif not entry.get("attributes"):
                entry["attributes"] = base_obj.get("attributes", {})
            entry["geometry"].extend(base_obj.get("geometry", []))
            print("lastVertexIndex "+str(lastVertexIndex))
        self.jsonExport['vertices'] = vertexArray
        self.jsonExport['CityObjects'] = grouped

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

    def applyBaselinePatch(self):
        if not self.patch_baseline:
            return
        baseline_txt = bpy.data.texts.get("CJE_BASELINE")
        if not baseline_txt:
            print("[CityJSONEditor] No baseline found; writing full export.")
            return
        try:
            baseline_str = baseline_txt.as_string()
        except Exception:
            try:
                baseline_str = baseline_txt.as_string()
            except Exception:
                baseline_str = None
        if baseline_str is None:
            print("[CityJSONEditor] Could not read baseline text; writing full export.")
            return
        try:
            baseline = json.loads(baseline_str)
        except Exception as exc:
            print(f"[CityJSONEditor] Baseline JSON invalid: {exc}; writing full export.")
            return
        # preserve unknown keys from baseline while replacing core content
        patched = baseline
        for key in ["CityObjects", "vertices", "appearance", "transform", "metadata", "version", "type", "extensions"]:
            if key in self.jsonExport:
                patched[key] = self.jsonExport[key]
        self.jsonExport = patched

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
        self.applyBaselinePatch()
        self.writeData()
        if self.skipped_objects:
            print(f"[CityJSONEditor] Export skipped {len(self.skipped_objects)} object(s):")
            for name, err in self.skipped_objects:
                print(f" - {name}: {err}")

        print('########################')
        print('### EXPORT FINISHED! ###')
        print('########################')
        return {'FINISHED'}
