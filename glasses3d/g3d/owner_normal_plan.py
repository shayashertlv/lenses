"""Deterministic independent face-normal spaces inside shown normal influence."""
from __future__ import annotations


def sharp_edges(obj):
    if '_surface_sharp_edges' in obj:return {tuple(sorted(e)) for e in obj['_surface_sharp_edges']}
    values=next((a['values'] for a in obj.get('attributes',[]) if a['name']=='sharp_edge'),None)
    return {tuple(sorted(e)) for i,e in enumerate(obj.get('edges',[])) if values is not None and values[i][0]}


def plan(source,target,operations):
    from . import owner_mesh as M,owner_surface as S
    old=M.objects(source);current=M.objects(target);allowed={}
    for operation in operations:
        oid=operation['scope']['object_id']
        allowed.setdefault(oid,set()).update(M.normal_faces(old[oid],operation['scope']['affected_polygon_ids']))
        for attachment in operation.get('marking_attachments',[]):
            mid=attachment['object_id'];ids={b['vertex_id'] for b in attachment['bindings']}
            faces={i for i,p in enumerate(old[mid]['polygons']) if ids.intersection(p)}
            allowed.setdefault(mid,set()).update(M.normal_faces(old[mid],faces))
    reports=[]
    for oid,scope in allowed.items():
        before,after=old[oid],current[oid]
        if all(before.get(k)==after.get(k) for k in ('vertices_local','polygons','corner_normals','polygon_smooth')):continue
        edges={};cursor=0
        for i,poly in enumerate(after['polygons']):
            corners={v:cursor+j for j,v in enumerate(poly)};cursor+=len(poly)
            for a,b in zip(poly,poly[1:]+poly[:1]):
                edges.setdefault(tuple(sorted((a,b))),[]).append((i,corners))
        wanted=sharp_edges(after)
        if '_surface_sharp_edges' not in after:
            mapped=S.split_edge_values({e:True for e in wanted},after.get('_surface_edge_splits',[]))
            remap=after.get('_surface_vertex_remap')
            wanted={tuple(sorted((remap[a],remap[b]) if remap is not None else (a,b)))
                    for a,b in mapped if remap is None or remap[a] is not None and remap[b] is not None}
        wanted.intersection_update(edges)
        parents=after.get('_surface_polygon_parents',list(range(len(after['polygons']))))
        added=[]
        for edge,rows in edges.items():
            if len(rows)!=2 or edge in wanted:continue
            if not any(parents[face] in scope for face,_ in rows):continue
            # A long curved fan can have a badly conditioned encoding basis even
            # when every target direction agrees. Independent face spaces avoid
            # that averaging; equal custom directions remain visually continuous.
            wanted.add(edge);added.append(list(edge))
        after['_surface_sharp_edges']=[list(e) for e in sorted(wanted)]
        # Pure snapshots must carry the same planned attribute as native capture.
        ordered=after.get('edges')
        if ordered is None or {tuple(sorted(e)) for e in ordered}!=set(edges):
            ordered=[list(e) for e in sorted(edges)];after['edges']=ordered
        attributes=[a for a in after.get('attributes',[]) if a['name']!='sharp_edge']
        if wanted or any(a['name']=='sharp_edge' for a in before.get('attributes',[])):
            attributes.append({'name':'sharp_edge','domain':'EDGE','data_type':'BOOLEAN',
                               'values':[[tuple(sorted(e)) in wanted] for e in ordered]})
        after['attributes']=attributes
        reports.append({'object_id':oid,'added_sharp_edges':added,
                        'method':'independent face normal spaces inside shown influence; intended directions retained',
                        'scope':'edges incident to the shown normal-influence faces; geometry unchanged by fan splitting'})
    return reports
