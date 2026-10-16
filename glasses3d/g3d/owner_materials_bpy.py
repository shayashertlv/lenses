"""Capture and apply isolated Principled material edits, preserving the shader graph."""
from . import checkpoint, export_glb, rpc, owner_extended


def capture(material):
    if material is None:return None
    tree=material.node_tree
    shaders=[n for n in tree.nodes if n.type=='BSDF_PRINCIPLED'] if tree else []
    principled=shaders[0] if len(shaders)==1 else None
    parameters={};linked=[];nodes=[]
    if principled:
        for key,name in owner_extended.MATERIAL_SOCKETS.items():
            socket=principled.inputs[name]
            parameters[key]=rpc.to_jsonable(export_glb._plain(socket.default_value))
            if socket.is_linked:linked.append(key)
    if tree:
        editable=set(owner_extended.MATERIAL_SOCKETS.values())
        for n in tree.nodes:
            defaults=[('<owner-adjustable>' if n==principled and s.name in editable else export_glb._plain(getattr(s,'default_value',None))) for s in n.inputs]
            nodes.append((n.name,n.bl_idname,[(k,v) for k,v in checkpoint._rna(n) if k!='id_data'],defaults,
                          [export_glb._plain(getattr(s,'default_value',None)) for s in n.outputs],
                          checkpoint._tree_state(n.node_tree,set()) if n.type=='GROUP' else None))
    protected=([(k,v) for k,v in checkpoint._rna(material) if k not in ('name','node_tree','id_data')],
               export_glb._custom_props(material),nodes,
               sorted((l.from_node.name,l.from_socket.identifier,l.to_node.name,l.to_socket.identifier) for l in tree.links) if tree else [])
    return {'name':material.name,'principled_node':principled.name if principled else None,
            'parameters':parameters,'linked_parameters':linked,'protected_sha256':checkpoint._h(protected)}


def apply(obj,source,target):
    import bpy
    from .owner_mesh import OwnerMeshError
    edits=target.get('_owner_material_edits')
    if not edits:return False
    for edit in edits:
        index=edit['index'];old=obj.data.materials[index]
        if capture(old)!=source['material_definitions'][index]:raise OwnerMeshError('E_OWNER_STALE','captured material changed')
        if obj.material_slots[index].link!='DATA':raise OwnerMeshError('E_OWNER_MATERIAL','object-linked materials require explicit rebinding')
        if bpy.data.materials.get(edit['name']):raise OwnerMeshError('E_OWNER_MATERIAL','isolated material name already exists')
        material=old.copy();material.name=edit['name']
        definition=target['material_definitions'][index];shader=material.node_tree.nodes[definition['principled_node']]
        for key,value in definition['parameters'].items():shader.inputs[owner_extended.MATERIAL_SOCKETS[key]].default_value=value
        obj.data.materials[index]=material
    return True
