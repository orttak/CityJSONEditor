"""
CityJSON import pipeline used by the Blender operator.
Runs validation/prep, sets world transforms, and constructs Blender meshes/objects.
"""

import bpy
from .CityObject import ImportCityObject, ExportCityObject
import time
import sys
from pathlib import Path
from .validation import prepare_cityjson_for_import

class ImportProcess:
    """Handles reading/preparing CityJSON and instantiating Blender objects."""

    def __init__(self, filepath, textureSetting, lod_filter="", lod_strategy="ALL"):
        # File to be imported
        self.filepath = filepath
        # Content of imported file
        self.data = []
        # Vertices of imported files geometry for further use in blenders objects
        self.vertices = []
        # Translation parameters / world origin
        self.worldOrigin = []
        # Scale parameters
        self.scaleParam = []
        # Import-setting which lets the user choose if textures present in the CityJSON should be imported
        # True - import textures
        # False - do not import textures
        self.textureSetting = textureSetting
        # vertices before scaling
        self.unScaledVertices = []
        # LoD filter set (floats) if provided
        self.lod_filter = self._parse_lod_filter(lod_filter)
        self.lod_strategy = lod_strategy

    def _parse_lod_filter(self, lod_filter: str):
        vals = set()
        if not lod_filter:
            return vals
        for part in lod_filter.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                vals.add(float(part))
            except ValueError:
                continue
        return vals

    def getTransformationParameters(self):

        try: 
            # check if the transform property exists
            transformProperty = self.data['transform']

        except:
            # if it does not exist, create it
            print('The files does not have the transform property, therefore it will now be created and applied to all vertices!')
            bbox = (self.data.get('metadata') or {}).get('geographicalExtent')
            if bbox and len(bbox) >= 6:
                bboxXmin = bbox[0]
                bboxYmin = bbox[1]
                bboxZmin = min([bbox[2], bbox[5]])
            else:
                bboxXmin = bboxYmin = bboxZmin = 0
            translate = [bboxXmin, bboxYmin, bboxZmin]
            self.worldOrigin = translate 
            # scale factor is 1 since the values are in meters (with decimals)
            scale = [1, 1, 1]
            self.scaleParam = scale
            
            # apply transform values to all vertices
            for vertex in self.data.get('vertices', []):
                x = vertex[0]-bboxXmin
                y = vertex[1]-bboxYmin
                z = vertex[2]-bboxZmin
                self.unScaledVertices.append([x,y,z])


        else:
            # if it exists, use it
            print('The file has the transform property!')
            # extract coordinates of CityJSON world origin / real world offset parameters
            for param in self.data['transform']['translate']:
                self.worldOrigin.append(param)
            # extract scale factor for coordinate values of vertices
            for param in self.data['transform']['scale']:
                self.scaleParam.append(param)
            # no need for processing of the vertices so they are just send along "as is "
            for vertex in self.data['vertices']:
                x = vertex[0]
                y = vertex[1]
                z = vertex[2]
                self.unScaledVertices.append([x,y,z])           
            

    def scaleVertexCoordinates(self):
        # apply scale factor to vertices
        for vertex in self.unScaledVertices:
            x = round(vertex[0]*self.scaleParam[0],3)
            y = round(vertex[1]*self.scaleParam[1],3)
            z = round(vertex[2]*self.scaleParam[2],3)
            self.vertices.append([x,y,z])

    def checkImport(self):
        # checks if this is the first imported CityJSON file
        # if the custom property "X_Origin" exists there has already been an import
        try: 
            test = bpy.context.scene.world['X_Origin']    
        except: 
            print('This is the first file!')
            return True
        else: 
            print('This is NOT the first file!')
            # load the x-origin set in the project
            establishedX = bpy.context.scene.world['X_Origin']
            # load the x-origin of the import file
            currentX = self.worldOrigin[0]
            # calculate the difference
            deltaX = currentX - establishedX
            # apply the difference to all coordinates
            for vertex in self.vertices:
                vertex[0] = vertex[0] + deltaX
            
            # load the y-origin set in the project
            establishedY = bpy.context.scene.world['Y_Origin']
            # load the y-origin of the import file
            currentY = self.worldOrigin[1]
            # calculate the difference
            deltaY = currentY - establishedY
            # apply the difference to all coordinates
            for vertex in self.vertices:
                vertex[1] = vertex[1] + deltaY

            # load the z-origin set in the project
            establishedZ = bpy.context.scene.world['Z_Origin']
            # load the z-origin of the import file
            currentZ = self.worldOrigin[2]
            # calculate the difference
            deltaZ = currentZ - establishedZ
            # apply the difference to all coordinates
            for vertex in self.vertices:
                vertex[2] = vertex[2] + deltaZ
            return False

    def createWorldProperties(self):
        metadata = self.data.get('metadata') or {}
        # Use empty string instead of 'undefined' to avoid schema validation errors
        bpy.context.scene.world['CRS'] = metadata.get('referenceSystem', '')
        bpy.context.scene.world['X_Origin'] = self.worldOrigin[0]
        bpy.context.scene.world['Y_Origin'] = self.worldOrigin[1]
        bpy.context.scene.world['Z_Origin'] = self.worldOrigin[2]
        bpy.context.scene.world['Scale_X'] = self.scaleParam[0] if len(self.scaleParam) > 0 else 0.001
        bpy.context.scene.world['Scale_Y'] = self.scaleParam[1] if len(self.scaleParam) > 1 else 0.001
        bpy.context.scene.world['Scale_Z'] = self.scaleParam[2] if len(self.scaleParam) > 2 else 0.001
        print("World parameters have been set!")

    def createCityObjects(self):
        # create the CityObjects with coresponding meshesS
        cityobjects = self.data.get('CityObjects') or {}
        objs_count = len(cityobjects)
        for i, (objID, object) in enumerate(cityobjects.items()):
            if i % 50 == 0:
                print(f'Creating object {i+1}/{objs_count}: {objID}')
            base_attrs = object.get("attributes") or {}
            geoms = (object.get("geometry") or [])
            if self.lod_strategy == "HIGHEST" and geoms:
                max_lod = max(float(g.get("lod", 0.0)) for g in geoms)
                geoms = [g for g in geoms if float(g.get("lod", 0.0)) == max_lod]
            elif self.lod_strategy == "FILTER" and self.lod_filter:
                geoms = [g for g in geoms if float(g.get("lod", 0.0)) in self.lod_filter]
            if not geoms:
                # User wants to preserve structural/placeholder objects.
                # Create an object with no LoD geometry.
                obj_name = f"{objID}__placeholder"
                cityobj = ImportCityObject(object, self.vertices, obj_name, self.textureSetting, self.data, self.filepath, source_id=objID, geom_index=-1)
                try:
                    cityobj.execute()
                except RuntimeError as exc:
                    print(f"[CityJSONEditor] Warning: failed to import placeholder '{objID}': {exc}")
                continue
            import copy as _copy
            for g_idx, geom in enumerate(geoms):
                filtered = _copy.deepcopy(object)
                filtered["geometry"] = [geom]
                geom_lod = geom.get("lod", 0)
                obj_name = f"{objID}__lod{geom_lod}__g{g_idx}"
                cityobj = ImportCityObject(filtered, self.vertices, obj_name, self.textureSetting, self.data, self.filepath, source_id=objID, geom_index=g_idx)
                try:
                    cityobj.execute()
                except RuntimeError as exc:
                    raise RuntimeError(f"Failed to import CityObject '{objID}' geometry {g_idx}: {exc}") from exc
        print('All CityObjects have been created!')

    def execute(self):
        time_start = time.time()
        
        # Console toggle only works on Windows
        if sys.platform == 'win32':
            try:
                bpy.ops.wm.console_toggle()
            except:
                pass  # Silently ignore if console toggle fails
        print('##########################')
        print('### STARTING IMPORT... ###')
        print('##########################')

        # clean up unused objects
        bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=False, do_recursive=True)

        ok, msg, data, _ = prepare_cityjson_for_import(Path(self.filepath), self.textureSetting)
        if not ok:
            raise RuntimeError(f"CityJSON validation failed: {msg}")
        self.data = data
        # expose metadata/version for editing and export
        try:
            bpy.context.scene["cj_metadata"] = self.data.get("metadata", {})
        except Exception as exc:
            print(f"[CityJSONEditor] Warning: failed to store metadata on scene: {exc}")
        try:
            bpy.context.scene["cj_version"] = self.data.get("version", "2.0")
        except Exception:
            bpy.context.scene["cj_version"] = "2.0"
        try:
            bpy.context.scene["cj_has_transform"] = bool(self.data.get("transform"))
        except Exception:
            bpy.context.scene["cj_has_transform"] = True
        self.getTransformationParameters()
        self.scaleVertexCoordinates()
        status = self.checkImport()
        # only set the world parameters if the file is the first CityJSON file to be imported
        if status is True:
            self.createWorldProperties()                         
        self.createCityObjects()
        # store baseline CityJSON for delta exports
        try:
            from pathlib import Path as _Path
            baseline_text = _Path(self.filepath).read_text(encoding="utf-8")
            txt = bpy.data.texts.get("CJE_BASELINE") or bpy.data.texts.new("CJE_BASELINE")
            txt.clear()
            txt.write(baseline_text)
        except Exception as exc:
            print(f"[CityJSONEditor] Warning: failed to store baseline: {exc}")

        print('########################')
        print('### IMPORT FINISHED! ###')
        print('########################')
        print("Time needed: %.4f sec" % (time.time() - time_start))
        return {'FINISHED'}
