"""
Mesh builder: converts CityJSON geometry boundaries into Blender meshes.
"""

import bpy

class Mesh:
    """Builds a Blender mesh object from CityJSON geometry boundaries."""

    def __init__(self, object, vertices, oid):
        # entire data of the object
        self.object = object
        # list of all vertices
        self.vertices = vertices
        # list which describes the faces mapped to the vertex indices
        self.vertexMaps = []
        # name/id of the object
        self.name = oid
        

    def extractVertexMapping(self):
        # create and store a list of the vertex mapping (faces)
        geometries = self.object.get('geometry', []) if isinstance(self.object, dict) else []
        for geom in geometries:
            boundaries = geom.get('boundaries') or []
            gtype = geom.get('type')
            if gtype == 'Solid':
                for shell in boundaries:
                    for face in shell:
                        if not face:
                            continue
                        # Only use the outer ring; holes are ignored to keep face counts aligned with semantics.
                        outer = face[0] if isinstance(face[0], list) else face
                        if outer:
                            self.vertexMaps.append(outer)
            elif gtype == 'MultiSurface':
                for face in boundaries:
                    if not face:
                        continue
                    outer = face[0] if isinstance(face[0], list) else face
                    if outer:
                        self.vertexMaps.append(outer)
            else:
                for face in boundaries:
                    if not face:
                        continue
                    if isinstance(face[0], list):
                        for ring in face:
                            if ring:
                                self.vertexMaps.append(ring)
                    else:
                        self.vertexMaps.append(face)
    
    def createBlenderMesh(self):
        # vertices used for defining blender meshes
        vertices = []
        # edges defined by vertex indices (not required if faces are made)
        edges = []
        # faces defindes by vertex indides
        faces = []
        # only the vertices that are actually part of the mesh
        meshVertices = []
        # new face mapping values
        newFaces = []
        
        # vertices from cityJSON file
        vertices = self.vertices
        # facemapping from cityJSON file
        faces = self.vertexMaps
        
        # only use vertices, that are part of the mesh
        for face in faces:
            # create new face array
            newFace = []
            # check vertex coordinate in face
            for value in face:
                vertexCoords = vertices[value]
                # if the coordinate used in the mesh already exists get its index
                if vertexCoords in meshVertices:
                    newFace.append(meshVertices.index(vertexCoords))
                # if the coordinate does not jet exist add it to the mesh and get its index
                else:
                    meshVertices.append(vertexCoords)
                    newFace.append(meshVertices.index(vertexCoords))
            # add the newly mapped face to the list of faces for the mesh
            newFaces.append(newFace)

        # creating a new mesh with the name of the object
        newMesh = bpy.data.meshes.new(self.name)
        # build the mesh from vertices and faces (edges not required)
        newMesh.from_pydata(meshVertices, edges, newFaces)
        # return the mesh so it can be handed over to the object  
        return newMesh    
        
    def execute(self):
        self.extractVertexMapping()
        mesh = self.createBlenderMesh()
        return mesh
