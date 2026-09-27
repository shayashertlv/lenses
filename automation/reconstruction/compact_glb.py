"""Lossless active-scene GLB storage compaction with optical-contract receipts.

No triangle decimation, quantization, texture resizing or image recompression is
performed. Unreferenced frame vertices may be removed; every per-corner stored
attribute and triangle order remains exact. Optical attribute cardinalities
remain fixed. Alternate scenes are preserved, not silently discarded.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import numpy as np

from .surface_transfer import _chunks, _pack, _rows
from .mesh import load_glb_bytes


METHOD = 'verified_active_storage_compaction_v2'


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash(value):
    return _sha(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode())


def _reachable(doc):
    if doc.get('skins') or doc.get('animations'):
        raise ValueError('Bake animation and skins before static compaction')
    reachable = set()
    def visit(index, ancestors):
        if type(index) is not int or not 0 <= index < len(doc.get('nodes',[])) or index in ancestors:
            raise ValueError('Invalid or cyclic GLB scene graph')
        node = doc['nodes'][index]
        if node.get('extensions'):
            raise ValueError('Node extensions with untracked index references cannot be compacted')
        reachable.add(index)
        for child in node.get('children',[]):
            visit(child,ancestors|{index})
    for scene in doc.get('scenes',[]):
        for index in scene.get('nodes',[]):
            visit(index,set())
    return sorted(reachable)


def _texture_infos(material, callback):
    def walk(value):
        if isinstance(value,dict):
            for key, item in list(value.items()):
                if key.endswith('Texture') and isinstance(item,dict) and 'index' in item:
                    value[key] = callback(item)
                else:
                    walk(item)
        elif isinstance(value,list):
            for item in value:
                walk(item)
    walk(material)
    return material


def _image_bytes(doc,binary,index):
    image = doc['images'][index]
    if 'bufferView' not in image or 'uri' in image:
        raise ValueError('Compact assets require embedded image buffer views')
    view = doc['bufferViews'][image['bufferView']]
    start = view.get('byteOffset',0); stop = start+view['byteLength']
    if start < 0 or stop > len(binary):
        raise ValueError('Image buffer range is invalid')
    return binary[start:stop]


def _normalized_groups(groups):
    groups = deepcopy(groups)
    for group in groups:
        group.pop('material_index',None)
        for member in group['members']:
            for name in ('node_index','mesh_index','material_index'):
                member.pop(name,None)
    return groups


def active_semantics_sha256(doc,binary):
    """Canonical active scenes: exact corner arrays, material/texture bytes and metadata.

    Storage indices, unreachable objects and accessor padding do not contribute.
    Every scene, node transform, optical descriptor, UV, normal, vertex color,
    triangle winding/order, sampler and encoded image payload does contribute.
    """
    _reachable(doc)
    def texture(index):
        row = deepcopy(doc['textures'][index])
        if row.get('extensions'):
            raise ValueError('Compressed/extended texture storage needs a separate verified adapter')
        source = row.pop('source'); sampler = row.pop('sampler',None)
        image = deepcopy(doc['images'][source]); image.pop('bufferView',None)
        return {'record':row,'sampler':deepcopy(doc.get('samplers',[])[sampler]) if sampler is not None else None,
                'image':image,'image_sha256':_sha(_image_bytes(doc,binary,source))}
    material_cache = {}
    def material(index):
        if index not in material_cache:
            material_cache[index] = _texture_infos(deepcopy(doc['materials'][index]),
                lambda info:{**{k:v for k,v in info.items() if k!='index'},'resolved_texture':texture(info['index'])})
        return material_cache[index]
    mesh_cache = {}
    def mesh(index):
        if index in mesh_cache:
            return mesh_cache[index]
        original = doc['meshes'][index]; result = {k:deepcopy(v) for k,v in original.items() if k!='primitives'}
        primitives = []
        for primitive in original['primitives']:
            if primitive.get('extensions') or primitive.get('targets') or primitive.get('mode',4)!=4:
                raise ValueError('Only explicit static uncompressed triangles can be compacted')
            position = _rows(doc,binary,primitive['attributes']['POSITION'])[1]
            ids = (_rows(doc,binary,primitive['indices'])[1].ravel() if 'indices' in primitive else np.arange(len(position)))
            if ids.dtype.kind not in 'ui' or len(ids)%3 or np.any(ids<0) or np.any(ids>=len(position)):
                raise ValueError('Invalid compactable triangle indices')
            attrs = {}
            for name, ai in sorted(primitive['attributes'].items()):
                acc,rows = _rows(doc,binary,ai)
                if len(rows)!=len(position):
                    raise ValueError('Primitive attributes must share their vertex count')
                attrs[name] = {'type':acc['type'],'componentType':acc['componentType'],'normalized':acc.get('normalized',False),
                               'extras':acc.get('extras'),'corner_sha256':_sha(np.ascontiguousarray(rows[ids]).tobytes())}
            record = {k:deepcopy(v) for k,v in primitive.items() if k not in ('attributes','indices','material','mode')}
            lower,upper=position.min(axis=0),position.max(axis=0)
            center=(lower.astype(float)+upper.astype(float))*.5
            radius_squared=float(np.max(np.sum((position-center)**2,axis=1)))
            record.update(mode=4,triangles=len(ids)//3,attributes=attrs,
                          position_bounds={'min':lower.tolist(),'max':upper.tolist(),'radius_squared':radius_squared},
                          material=material(primitive['material']) if 'material' in primitive else None)
            primitives.append(record)
        result['primitives']=primitives; mesh_cache[index]=result
        return result
    def node(index):
        original=doc['nodes'][index]
        result={k:deepcopy(v) for k,v in original.items() if k not in ('mesh','children')}
        if 'mesh' in original:
            result['resolved_mesh']=mesh(original['mesh'])
        if 'children' in original:
            result['resolved_children']=[node(i) for i in original['children']]
        return result
    result={k:deepcopy(v) for k,v in doc.items() if k not in
            ('nodes','meshes','accessors','bufferViews','buffers','materials','textures','images','samplers','scenes')}
    declaration=result.get('extras',{}).get('effectiveOpticalGroups')
    if declaration:
        declaration.pop('declaration_sha256',None)
        declaration['groups']=_normalized_groups(declaration['groups'])
    result['resolved_scenes']=[{**{k:deepcopy(v) for k,v in scene.items() if k!='nodes'},
                               'resolved_roots':[node(i) for i in scene.get('nodes',[])]} for scene in doc['scenes']]
    return _hash(result)


def _remap_groups(groups,maps):
    result=deepcopy(groups)
    for group in result:
        group['material_index']=maps['materials'][str(group['material_index'])]
        for member in group['members']:
            for field, table in (('node_index','nodes'),('mesh_index','meshes'),('material_index','materials')):
                member[field]=maps[table][str(member[field])]
    return result


def validate_compact_optical_storage(receipt,doc,binary):
    """Strict alternate storage check; called only by the optical asset reader."""
    from .optical_group_asset import _receipt
    proof=receipt.get('compact_storage')
    if not isinstance(proof,dict) or set(proof)!={'method','source_output_sha256','source_receipt','index_maps','active_semantics_sha256'}:
        raise ValueError('Invalid compact optical storage proof')
    if proof['method']!=METHOD:
        raise ValueError('Unknown compact optical storage method')
    parent=_receipt(proof['source_receipt'])
    if parent.get('compact_storage'):
        raise ValueError('Nested compact optical storage receipts are unsupported')
    if parent['output_sha256']!=proof['source_output_sha256']:
        raise ValueError('Compact source receipt does not bind its source output')
    for key in ('source_sha256','source_binary_prefix','provenance'):
        if receipt[key]!=parent[key]:
            raise ValueError('Compact optical source lineage differs')
    maps=proof['index_maps']
    if (not isinstance(maps,dict) or set(maps)!= {'nodes','meshes','materials'}
            or any(not isinstance(table,dict) or any(not str(k).isdigit() or type(v) is not int or v<0 for k,v in table.items())
                   or len(set(table.values()))!=len(table) for table in maps.values())):
        raise ValueError('Invalid compact optical index maps')
    try:
        groups=_remap_groups(parent['groups'],maps)
    except KeyError:
        raise ValueError('Missing compact optical identity mapping') from None
    if groups!=receipt['groups'] or active_semantics_sha256(doc,binary)!=proof['active_semantics_sha256']:
        raise ValueError('Compact active material/geometry semantics differ from the verified receipt')


def compact_glb_bytes(raw, *, optical_receipt=None):
    """Return lossless compact bytes, proof and optional resealed optical receipt."""
    doc,binary=_chunks(raw); original=deepcopy(doc)
    if optical_receipt is not None and optical_receipt.get('compact_storage'):
        raise ValueError('An already compact optical asset needs no repeated compaction')
    before=active_semantics_sha256(doc,binary)
    nodes=_reachable(doc); meshes=sorted({doc['nodes'][n]['mesh'] for n in nodes if 'mesh' in doc['nodes'][n]})
    materials=sorted({p['material'] for m in meshes for p in doc['meshes'][m]['primitives'] if 'material' in p})
    node_map={i:j for j,i in enumerate(nodes)}; mesh_map={i:j for j,i in enumerate(meshes)}; material_map={i:j for j,i in enumerate(materials)}
    maps={'nodes':{str(k):v for k,v in node_map.items()},'meshes':{str(k):v for k,v in mesh_map.items()},
          'materials':{str(k):v for k,v in material_map.items()}}
    out_bin=bytearray(); views=[]; accessors=[]; view_seen={}; accessor_seen={}
    def view(blob,target=None):
        key=(target,_sha(blob))
        if key not in view_seen:
            out_bin.extend(b'\0'*(-len(out_bin)%4))
            record={'buffer':0,'byteOffset':len(out_bin),'byteLength':len(blob)}
            if target is not None: record['target']=target
            views.append(record); out_bin.extend(blob); view_seen[key]=len(views)-1
        return view_seen[key]
    def accessor(old_index, selected=None, index_values=None):
        old,rows=_rows(original,binary,old_index)
        if selected is not None: rows=rows[selected]
        record={k:deepcopy(v) for k,v in old.items() if k not in ('bufferView','byteOffset','count','min','max')}
        target=34962
        if index_values is not None:
            values=np.asarray(index_values); dtype='<u2' if values.max(initial=0)<=65535 else '<u4'
            rows=values.astype(dtype).reshape(-1,1); record.update(type='SCALAR',componentType=5123 if dtype=='<u2' else 5125)
            target=34963
        rows=np.ascontiguousarray(rows); record['count']=len(rows)
        if 'min' in old: record['min']=rows.min(axis=0).tolist()
        if 'max' in old: record['max']=rows.max(axis=0).tolist()
        payload=rows.tobytes(); key=_hash(record)+_sha(payload)+str(target)
        if key not in accessor_seen:
            record['bufferView']=view(payload,target); accessors.append(record); accessor_seen[key]=len(accessors)-1
        return accessor_seen[key]
    output_meshes=[]; removed_vertices=0
    optical_meshes={member['mesh_index'] for group in (optical_receipt or {}).get('groups',[]) for member in group['members']}
    for mi in meshes:
        mesh=deepcopy(original['meshes'][mi])
        for primitive in mesh['primitives']:
            position=_rows(original,binary,primitive['attributes']['POSITION'])[1]
            ids=(_rows(original,binary,primitive['indices'])[1].ravel() if 'indices' in primitive else np.arange(len(position)))
            if mi in optical_meshes or 'indices' not in primitive:
                keep=np.arange(len(position))
            else:
                # AR bounds consumers examine every POSITION, including unused
                # LOD vertices. Retain extremal witnesses so lossless packaging
                # cannot change fitting, light bounds or bounding spheres.
                lower,upper=position.min(axis=0),position.max(axis=0)
                center=(lower.astype(float)+upper.astype(float))*.5
                witnesses=np.r_[np.argmin(position,axis=0),np.argmax(position,axis=0),
                                np.argmax(np.sum((position-center)**2,axis=1))]
                keep=np.unique(np.r_[ids,witnesses])
            remapped=np.searchsorted(keep,ids)
            removed_vertices+=len(position)-len(keep)
            primitive['attributes']={name:accessor(ai,keep) for name,ai in primitive['attributes'].items()}
            if 'indices' in primitive:
                primitive['indices']=accessor(primitive['indices'],index_values=remapped)
            if 'material' in primitive: primitive['material']=material_map[primitive['material']]
        output_meshes.append(mesh)
    texture_map={}; image_seen={}; textures=[]; images=[]
    def texture_info(info):
        ti=info['index']
        if ti not in texture_map:
            texture=deepcopy(original['textures'][ti]); source=texture['source']
            image=deepcopy(original['images'][source]); image.pop('bufferView',None)
            blob=_image_bytes(original,binary,source); key=_hash(image)+_sha(blob)
            if key not in image_seen:
                image['bufferView']=view(blob); images.append(image); image_seen[key]=len(images)-1
            texture['source']=image_seen[key]
            # Exact duplicate texture objects share one index as well.
            if texture in textures: texture_map[ti]=textures.index(texture)
            else: textures.append(texture); texture_map[ti]=len(textures)-1
        return {**info,'index':texture_map[ti]}
    doc['materials']=[_texture_infos(deepcopy(original['materials'][i]),texture_info) for i in materials]
    doc['nodes']=[deepcopy(original['nodes'][i]) for i in nodes]
    for node in doc['nodes']:
        if 'mesh' in node: node['mesh']=mesh_map[node['mesh']]
        if 'children' in node: node['children']=[node_map[i] for i in node['children']]
    for scene in doc['scenes']: scene['nodes']=[node_map[i] for i in scene.get('nodes',[])]
    doc.update(meshes=output_meshes,accessors=accessors,bufferViews=views,buffers=[{'byteLength':len(out_bin)}])
    if 'textures' in doc: doc['textures']=textures
    if 'images' in doc: doc['images']=images
    updated=None
    if optical_receipt is not None:
        from .optical_group_asset import _receipt, _hash as optical_hash
        parent=_receipt(optical_receipt)
        if parent['output_sha256']!=_sha(raw): raise ValueError('Optical compaction source hash differs')
        updated=deepcopy(parent); updated['groups']=_remap_groups(parent['groups'],maps)
        declaration=deepcopy(doc['extras']['effectiveOpticalGroups']); declaration.pop('declaration_sha256')
        # Preserve the document's JSON number representation. A Node GLB writer
        # serializes 1.0 as 1; replacing this with receipt floats would change
        # the exact active metadata hash despite equal optical values.
        declaration['groups']=_remap_groups(declaration['groups'],maps)
        updated['groups']=deepcopy(declaration['groups']); digest=optical_hash(declaration)
        doc['extras']['effectiveOpticalGroups']={'declaration_sha256':digest,**declaration}
        updated['declaration_sha256']=digest
        updated['compact_storage']={'method':METHOD,'source_output_sha256':_sha(raw),'source_receipt':parent,
                                    'index_maps':maps,'active_semantics_sha256':before}
    elif doc.get('extras',{}).get('effectiveOpticalGroups'):
        raise ValueError('Optical group assets require their trusted export receipt for compaction')
    compact=_pack(doc,out_bin); after_doc,after_bin=_chunks(compact)
    after=active_semantics_sha256(after_doc,after_bin)
    if before!=after: raise ValueError('Compaction changed active scene material or geometry semantics')
    if updated is not None:
        from .optical_group_asset import _seal
        updated.pop('receipt_sha256',None); updated['output_sha256']=_sha(compact); updated=_seal(updated)
    scene=load_glb_bytes(compact)
    proof={'schema_version':1,'method':METHOD,'source_sha256':_sha(raw),'output_sha256':_sha(compact),
        'active_semantics_sha256':before,'active_semantics_identical':True,'source_bytes':len(raw),'output_bytes':len(compact),
        'bytes_removed':len(raw)-len(compact),'triangles':len(scene.faces),'vertices':len(scene.vertices),
        'unreferenced_frame_vertices_removed':removed_vertices,'index_maps':maps,
        'records_before':{k:len(original.get(k,[])) for k in ('nodes','meshes','materials','accessors','bufferViews','images','textures')},
        'records_after':{k:len(after_doc.get(k,[])) for k in ('nodes','meshes','materials','accessors','bufferViews','images','textures')},
        'geometry_decimation':False,'texture_resampling':False,'optical_attribute_cardinalities_preserved':True,
        'accepted':False,'quality_verdict':'storage_equivalence_verified_not_mobile_performance'}
    return compact,proof,updated


def run_compact_asset(model,output,optical_receipt=None):
    """Write compact.glb, report.json and a portable resealed optical receipt."""
    model,output=Path(model).resolve(),Path(output).resolve()
    if output.exists() and any(output.iterdir()): raise ValueError('Use a fresh compact output directory')
    raw=model.read_bytes(); receipt=deepcopy(optical_receipt) if isinstance(optical_receipt,dict) else None
    if optical_receipt is not None and receipt is None: receipt=json.loads(Path(optical_receipt).read_bytes())
    if receipt is not None:
        from .optical_group_asset import read_optical_group_candidate
        read_optical_group_candidate(model,receipt,expected_sha256=_sha(raw))
    compact,report,updated=compact_glb_bytes(raw,optical_receipt=receipt)
    output.mkdir(parents=True,exist_ok=True); path=output/'compact.glb'; path.write_bytes(compact)
    report['model']={'path':str(path),'sha256':_sha(compact),'bytes':len(compact)}
    if updated is not None:
        from .optical_group_asset import _seal, read_optical_group_candidate
        updated.pop('receipt_sha256',None); updated['output']=str(path); updated=_seal(updated)
        read_optical_group_candidate(path,updated,expected_sha256=_sha(compact))
        receipt_path=output/'compact.export.json'; receipt_path.write_text(json.dumps(updated,indent=2,allow_nan=False)+'\n')
        report['export']={'path':str(receipt_path),'sha256':_sha(receipt_path.read_bytes())}
    if _sha(model.read_bytes())!=_sha(raw): raise ValueError('Compaction source changed during execution')
    (output/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    return report
