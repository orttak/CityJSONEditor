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
from .schema import CJProps, CJExport

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
        if self.baseline_data is not None:
            self.keep_transform = "transform" in self.baseline_data
        else:
            try:
                self.keep_transform = bool(bpy.context.scene.get("cj_has_transform", True))
            except Exception:
                self.keep_transform = True
        self._cached_objs = None

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
        version = "2.0.1"  # Default to 2.0.1 for better schema compatibility
        try:
            meta = copy.deepcopy(bpy.context.scene.get("cj_metadata", {}))
        except Exception:
            meta = {}
        # Try to get the version from the scene, but upgrade if it's 2.0 or lower
            # Use 2.0 as 2.0.1 is not supported by cjio/cjvalpy
        version = "2.0"
        if bpy.context.scene.get("cj_version"):
            # We strictly use 2.0 for the exported file header for tool compatibility
            version = "2.0"
        
        base = {
            "type": "CityJSON",
            "version": version,
            "metadata": meta
        }
        if self.keep_transform:
            base["transform"] = {
                "scale": [0.001, 0.001, 0.001],
                "translate": [],
            }
        # always start with clean geometry/appearance containers
        base["CityObjects"] = {}
        base["vertices"] = None
        if self.textureSetting:
            app = base.get("appearance") or {}
            app["textures"] = []
            app["vertices-texture"] = []
            base["appearance"] = app
        else:
            if "appearance" in base:
                del base["appearance"]
        self.jsonExport = base
    
    def _gather_objects(self):
        if self._cached_objs is not None:
            return self._cached_objs
        objs = []
        for obj in bpy.data.objects:
            if getattr(obj, "type", "") != "MESH":
                continue
            if "cityJSONType" not in obj:
                continue
            export_id = obj.get("cj_source_id", obj.name.split("__")[0])
            objs.append((export_id, obj))
        self._cached_objs = objs
        return objs

    def getMetadata(self):
        crs = None
        try:
            crs = bpy.context.scene.world['CRS']
        except Exception:
            crs = None
        if crs is not None:
            crs_str = str(crs).strip()
            # Basic validation for OGC CRS URLs, and skip "undefined" placeholders
            if crs_str and crs_str.lower() != "undefined":
                self.jsonExport.setdefault("metadata", {}).update({"referenceSystem" : crs_str})

    def getTransform(self):
        if not self.keep_transform:
            self.jsonExport.pop("transform", None)
            return
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
                if not basename:
                    print(f"[CityJSONEditor] Skipping image with no name.")
                    continue
                # Normalize image type for CityJSON schema (usually JPG or PNG)
                cityjson_type = imageType.upper()
                if cityjson_type == 'JPEG':
                    cityjson_type = 'JPG'
                
                imageName = "appearance/" + basename
                textureJSON = {
                    "type": cityjson_type,
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

    def _walk_indices(self, node, func):
        if isinstance(node, list):
            for i, item in enumerate(node):
                if isinstance(item, list):
                    self._walk_indices(item, func)
                else:
                    try:
                        node[i] = func(item)
                    except Exception:
                        pass

    def _update_all_boundaries(self, func):
        cityobjects = self.jsonExport.get("CityObjects") or {}
        for obj in cityobjects.values():
            geoms = obj.get("geometry") or []
            for geom in geoms:
                boundaries = geom.get("boundaries")
                if isinstance(boundaries, list):
                    self._walk_indices(boundaries, func)

    def _remove_duplicate_vertices(self):
        vertices = self.jsonExport.get("vertices") or []
        if not vertices:
            return 0
        index_map = [-1] * len(vertices)
        new_vertices = []
        seen = {}
        for i, v in enumerate(vertices):
            key = tuple(v)
            existing = seen.get(key)
            if existing is None:
                existing = len(new_vertices)
                seen[key] = existing
                new_vertices.append(v)
            index_map[i] = existing
        self._update_all_boundaries(lambda idx: index_map[idx])
        self.jsonExport["vertices"] = new_vertices
        return len(vertices) - len(new_vertices)

    def _remove_orphan_vertices(self):
        vertices = self.jsonExport.get("vertices") or []
        if not vertices:
            return 0
        used = {}
        ordered = []

        def collect(node):
            if isinstance(node, list):
                for item in node:
                    collect(item)
            else:
                if not isinstance(node, int):
                    return
                if node < 0 or node >= len(vertices):
                    return
                if node not in used:
                    used[node] = len(ordered)
                    ordered.append(node)

        cityobjects = self.jsonExport.get("CityObjects") or {}
        for obj in cityobjects.values():
            geoms = obj.get("geometry") or []
            for geom in geoms:
                boundaries = geom.get("boundaries")
                if isinstance(boundaries, list):
                    collect(boundaries)

        if len(ordered) == len(vertices):
            return 0

        self._update_all_boundaries(lambda idx: used[idx])
        self.jsonExport["vertices"] = [vertices[i] for i in ordered]
        return len(vertices) - len(self.jsonExport["vertices"])

    def _cleanup_vertices(self):
        removed_dupes = self._remove_duplicate_vertices()
        removed_orphans = self._remove_orphan_vertices()
        if removed_dupes or removed_orphans:
            print(f"[CityJSONEditor] Cleaned vertices: -{removed_dupes} duplicates, -{removed_orphans} orphans.")

    def createCityObject(self):
        baseline_cityobjects = (self.baseline_data.get("CityObjects") or {}) if self.baseline_data else {}
        baseline_vertices = (self.baseline_data.get("vertices") or []) if (self.baseline_data and self.export_changed_only) else []
        vertexArray = list(baseline_vertices)
        blendObjects = [obj for _, obj in self._gather_objects()]
        lastVertexIndex = len(vertexArray)
        default_scale = [0.001, 0.001, 0.001] if self.keep_transform else [1, 1, 1]
        scale = (self.jsonExport.get("transform") or {}).get("scale") or default_scale
        if len(scale) != 3:
            scale = list(default_scale)
        scale = [s if s not in (None, 0) else default_scale[idx] for idx, s in enumerate(scale)]
        grouped = copy.deepcopy(baseline_cityobjects) if baseline_cityobjects else {}

        objs = self._gather_objects()

        dirty_ids = set()
        if self.export_changed_only:
            for export_id, obj in objs:
                dirty = obj.get("cj_dirty", False) or export_id not in baseline_cityobjects
                if dirty:
                    dirty_ids.add(export_id)

        objs_count = len(objs)
        for i, (export_id, object) in enumerate(objs):
            if self.export_changed_only and dirty_ids and export_id not in dirty_ids:
                continue
            if i % 50 == 0:
                print(f"Create Export-Object {i+1}/{objs_count}: {object.name}")
            try:
                cityobj = ExportCityObject(object, lastVertexIndex, self.jsonExport, self.textureSetting, self.textureReferenceList)
                export_id, base_obj = cityobj.execute()
            except Exception as exc:
                if self.skip_failed_exports:
                    print(f"[CityJSONEditor] Skipping export of '{object.name}': {exc}")
                    self.skipped_objects.append((object.name, str(exc)))
                    continue
                raise
            
            # 🆕 ADD PARENTS FIELD (LOD3 Window → Building relationship)
            parent_ids = object.get(CJProps.PARENT_IDS)
            if parent_ids and isinstance(parent_ids, list) and parent_ids:
                base_obj[CJExport.PARENTS] = parent_ids
            
            for vertex in cityobj.vertices:
                v = list(vertex)
                v[0] = round(v[0]/scale[0])
                v[1] = round(v[1]/scale[1])
                v[2] = round(v[2]/scale[2])
                vertexArray.append(v)
            
            # Update lastVertexIndex for NEXT object
            lastVertexIndex = len(vertexArray)
            entry = grouped.get(export_id)
            if entry is None:
                entry = {"type": base_obj["type"], "attributes": base_obj.get("attributes", {}), "geometry": []}
                grouped[export_id] = entry
            else:
                # Update attributes and type from Blender
                entry["type"] = base_obj["type"]
                entry.setdefault("attributes", {}).update(base_obj.get("attributes", {}))
            
            # Merge geometries: replace existing LoD or append new one
            new_geoms = base_obj.get("geometry", [])
            if new_geoms:
                if "geometry" not in entry:
                    entry["geometry"] = []

                def _normalize_lod_val(val):
                    try:
                        return f"{float(val):g}"
                    except (ValueError, TypeError):
                        return str(val)

                for n_geo in new_geoms:
                    n_lod = _normalize_lod_val(n_geo.get("lod"))
                    # Remove all existing geometries with matching LoD to prevent duplication
                    entry["geometry"] = [
                        g for g in entry["geometry"] 
                        if _normalize_lod_val(g.get("lod")) != n_lod
                    ]
                    # Add current geometry from Blender
                    entry["geometry"].append(n_geo)
            print("lastVertexIndex "+str(lastVertexIndex))
        self.jsonExport['version'] = '2.0'
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

        # If nothing changed and a baseline exists, reuse it verbatim to guarantee round-trip equality.
        baseline_cityobjects = (self.baseline_data.get("CityObjects") or {}) if self.baseline_data else {}
        if self.export_changed_only and self.baseline_data:
            dirty = []
            for export_id, obj in self._gather_objects():
                if obj.get("cj_dirty", False) or export_id not in baseline_cityobjects:
                    dirty.append(export_id)
            if not dirty:
                self.jsonExport = copy.deepcopy(self.baseline_data)
                self.writeData()
                print("[CityJSONEditor] No dirty objects; wrote baseline without changes.")
                print('########################')
                print('### EXPORT FINISHED! ###')
                print('########################')
                return {'FINISHED'}

        self.createJSONStruct()
        self.getMetadata()
        self.getTransform()
        if self.textureSetting: 
            self.getTextures()
            self.getVerticesTexture()
        self.createCityObject()
        self._cleanup_vertices()
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
