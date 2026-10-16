"""Data-derived selected-mesh diagrams, separate from the photo-first AR beauty.

Capture actual mesh data read-only in Blender: --python this.py -- PROOF_ROOT --capture.
Then run this file with a host Python containing matplotlib: PROOF_ROOT.
Only the already-disclosed copied-leaf health, radius or M7 surface fixtures are shown. Source blends
are hash checked and never saved. Diagrams are labelled as isolated mesh views.
"""
from pathlib import Path
import hashlib
import json
import sys
import numpy as np

KINDS=('mesh_cleanup','consistent_normals','symmetrize','controlled_decimate')
VARIANTS=('before','half','full')


def kinds(root):
    if (root/'edge_round').is_dir():return ('edge_round','edge_sharpen')
    return ('flatten_face','repair_shading') if (root/'flatten_face').is_dir() else KINDS


def capture(root):
    import bpy
    for kind in kinds(root):
        folder=root/kind;records={}
        for name in VARIANTS:
            filename=({'before':'before.blend','half':'preview/model-copy.blend','full':'after/model-copy.blend'}[name]
                      if kind in ('flatten_face','repair_shading','edge_round','edge_sharpen') else f'{name}.blend')
            path=folder/filename;sha=hashlib.sha256(path.read_bytes()).hexdigest()
            bpy.ops.wm.open_mainfile(filepath=str(path));obj=bpy.data.objects['frame leaf1'];me=obj.data
            matrix=np.asarray(obj.matrix_world);origin=np.asarray(bpy.context.scene.get('mdl_bridge_underside',[0,0,0]))
            vertices=np.asarray([p.co[:] for p in me.vertices])@matrix[:3,:3].T+matrix[:3,3]-origin
            normals=np.asarray([n.vector[:] for n in me.corner_normals])@np.linalg.inv(matrix[:3,:3])
            normals/=np.maximum(np.linalg.norm(normals,axis=1)[:,None],1e-30)
            me.calc_loop_triangles()
            records[name]={'source_copy':str(path),'sha256':sha,'object':obj.name,
                'vertices_world_mm':vertices.tolist(),'polygons':[list(p.vertices) for p in me.polygons],
                'triangles':[list(t.vertices) for t in me.loop_triangles],
                'corner_normals_world':normals.tolist()}
            if kind in ('edge_round','edge_sharpen'):
                recipe=json.loads(me.get('g3d_owner_edge_recipe','null'))
                records[name]['authored_radius_mm']=0. if recipe is None else recipe['radius_mm']
                records[name]['segments']=0 if recipe is None else recipe['segments']
            assert hashlib.sha256(path.read_bytes()).hexdigest()==sha
        target=folder/'diagnostic-mesh-snapshots.json'
        if target.exists():raise ValueError('choose fresh diagnostic output')
        target.write_text(json.dumps(records,indent=2))


def metrics(record):
    v=np.asarray(record['vertices_world_mm']);f=np.asarray(record['triangles']);q=v[f]
    area=np.linalg.norm(np.cross(q[:,1]-q[:,0],q[:,2]-q[:,0]),axis=1)/2
    return {'vertices':len(v),'triangles':len(f),'duplicates':len(v)-len(np.unique(v,axis=0)),
            'degenerate_triangles':int(sum(area<=1e-12))}


def plot(root):
    if (root/'edge_round').is_dir():return plot_radius(root)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Line3DCollection
    descriptions={'mesh_cleanup':'Exact duplicate and degenerate-triangle cleanup',
                  'consistent_normals':'Shading-normal direction correction; geometry stays fixed',
                  'symmetrize':'X=0 symmetry: reflected negative-X leaf compared with the source',
                  'controlled_decimate':'Planar fan reduction; the physical surface stays fixed',
                  'flatten_face':'Flatten the selected face; its boundary stays fixed',
                  'repair_shading':'Repair shading to match the plane; all geometry stays fixed'}
    all_metrics={}
    for kind in kinds(root):
        folder=root/kind;records=json.loads((folder/'diagnostic-mesh-snapshots.json').read_text())
        fig=plt.figure(figsize=(15,6.2));fig.suptitle(descriptions[kind]+'\nIsolated selected-mesh DIAGRAM from saved copies — not AR beauty',fontsize=14)
        summary={};base=np.asarray(records['before']['vertices_world_mm']);center=base.mean(0)
        if kind=='symmetrize':center=base[base[:,0]>0].mean(0)
        for column,name in enumerate(VARIANTS,1):
            row=records[name];v=np.asarray(row['vertices_world_mm']);polys=row['polygons'];m=metrics(row);summary[name]=m
            ax=fig.add_subplot(1,3,column,projection='3d');edges={tuple(sorted((a,b))) for p in polys for a,b in zip(p,p[1:]+p[:1])}
            if kind=='symmetrize':
                shown=v.copy();shown[:,0]=abs(shown[:,0]);shown-=center
                positive=v[:,0]>0
                for flag,color,label in ((True,'#1976d2','positive-X source'),(False,'#d45b26','reflected negative-X destination')):
                    lines=[shown[list(e)] for e in edges if bool(positive[e[0]])==flag]
                    ax.add_collection3d(Line3DCollection(lines,colors=color,linewidths=1.7,label=label))
                source=v[positive];reflected=v[~positive]*[-1,1,1]
                mismatch=max(min(np.linalg.norm(source-p,axis=1)) for p in reflected)
                m['maximum_mirror_mismatch_mm']=float(mismatch)
                caption=f'maximum mirror mismatch: {mismatch:.6f} mm'
            else:
                shown=v-center
                ax.add_collection3d(Line3DCollection([shown[list(e)] for e in edges],colors='#52606d',linewidths=1.1))
                ax.scatter(*shown.T,s=14,c='#263238')
                caption=f"{m['vertices']} vertices; {m['triangles']} triangles"
                if kind=='mesh_cleanup':
                    _,inverse,counts=np.unique(v,axis=0,return_inverse=True,return_counts=True)
                    duplicate=counts[inverse]>1
                    ax.scatter(*shown[duplicate].T,s=60,facecolors='none',edgecolors='#c62828',linewidths=1.7)
                    caption+=f"\n{m['duplicates']} duplicate vertices; {m['degenerate_triangles']} zero-area triangles"
                if kind in ('consistent_normals','repair_shading'):
                    normals=np.asarray(row['corner_normals_world']);start=0;backward=0;tangent=0;maximum_angle=0.
                    for poly in polys:
                        p=shown[poly];field=normals[start:start+len(poly)];start+=len(poly)
                        geometric=np.sum(np.cross(p,np.roll(p,-1,axis=0)),axis=0);geometric/=np.linalg.norm(geometric)
                        dots=field@geometric;backward+=int(sum(dots < -1e-7));tangent+=int(sum(abs(dots)<=1e-7))
                        maximum_angle=max(maximum_angle,float(np.degrees(np.arccos(np.clip(dots,-1,1))).max()))
                        n=field.mean(0);n/=np.linalg.norm(n)
                        color='#c62828' if n@geometric < -1e-7 else '#e69100' if abs(n@geometric)<=1e-7 else '#2e7d32'
                        c=p.mean(0);ax.quiver(*c,*n,length=1.2,color=color,linewidth=1.3,arrow_length_ratio=.22)
                    m['backward_corner_normals']=backward
                    m['tangent_corner_normals']=tangent
                    m['maximum_shading_angle_from_face_deg']=maximum_angle
                    caption+=(f'\n{backward} backward; {tangent} tangent corner normals' if kind=='consistent_normals'
                              else f'\nmaximum shading error from plane: {maximum_angle:.2f} degrees')
                if kind=='flatten_face':
                    selection=json.loads((folder/'selection.json').read_text());support={w['vertex_id'] for w in selection['scope']['vertex_weights']}
                    selected=[p for p in polys if set(p)<=support];counts={}
                    for p in selected:
                        for a,b in zip(p,p[1:]+p[:1]):
                            edge=tuple(sorted((a,b)));counts[edge]=counts.get(edge,0)+1
                    border=sorted({i for edge,count in counts.items() if count==1 for i in edge});c=v[border].mean(0)
                    _,_,axes=np.linalg.svd(v[border]-c);residual=float(abs((v[sorted(support)]-c)@axes[-1]).max())
                    m['maximum_plane_residual_mm']=residual
                    caption+=f'\nmaximum height above boundary plane: {residual:.6f} mm'
            extent=max(np.ptp(shown,axis=0).max(),6.);half=extent*.64
            for setter in (ax.set_xlim,ax.set_ylim,ax.set_zlim):setter(-half,half)
            ax.set_box_aspect((1,1,1));ax.view_init(elev=24,azim=-42)
            ax.set_xlabel('X offset (mm)');ax.set_ylabel('Y offset (mm)');ax.set_zlabel('Z offset (mm)')
            ax.set_title({'before':'BEFORE','half':'PREVIEW — half amount','full':'AFTER — full amount'}[name],fontsize=12)
            fig.text((column-.5)/3,.13,caption,ha='center',va='center',fontsize=10)
        if kind=='symmetrize':fig.text(.5,.025,'Blue: unchanged source leaf. Orange: destination reflected about X=0 for this diagnostic overlay only.',ha='center',fontsize=10)
        else:fig.text(.5,.025,'The photo-first AR gallery supplies full-product context. Deliberate fixture faults are not defects attributed to the delivered model.',ha='center',fontsize=10)
        fig.subplots_adjust(left=.02,right=.98,top=.8,bottom=.28,wspace=.02)
        fig.savefig(folder/'isolated-mesh-before-preview-after.png',dpi=150);plt.close(fig);all_metrics[kind]=summary
    (root/'diagnostic-metrics.json').write_text(json.dumps(all_metrics,indent=2))


def plot_radius(root):
    """Actual end-cap boundary viewed along the straight selected edge, no resampling."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.patches import Rectangle
    original=json.loads((root/'edge_round/diagnostic-mesh-snapshots.json').read_text())['before']
    source=np.asarray(original['vertices_world_mm']);origin=source.min(0);summary={}
    for kind in kinds(root):
        folder=root/kind;records=json.loads((folder/'diagnostic-mesh-snapshots.json').read_text());summary[kind]={}
        fig,axes=plt.subplots(1,3,figsize=(15,6.2))
        fig.suptitle(('Round the selected straight edge' if kind=='edge_round' else 'Sharpen the saved circular fillet')+
            '\nIsolated selected-mesh DIAGRAM from saved copies — not AR beauty',fontsize=14)
        for column,(name,ax) in enumerate(zip(VARIANTS,axes),1):
            row=records[name];v=np.asarray(row['vertices_world_mm'])-origin;polys=row['polygons'];m=metrics(row)
            edges={tuple(sorted((a,b))) for p in polys for a,b in zip(p,p[1:]+p[:1])}
            end=[e for e in edges if max(abs(v[list(e),2]))<1e-5]
            lines=[v[list(e),:2] for e in end]
            ax.plot([0,0,.36],[.36,0,0],color='#9aa2aa',ls='--',lw=1,label='original sharp corner')
            ax.add_collection(LineCollection(lines,colors='#1565c0',linewidths=2.2,label='actual saved mesh'))
            ids=sorted({i for e in end for i in e});xy=v[ids,:2]
            ax.scatter(*xy.T,s=28,c='#123a65',zorder=3)
            radius=row['authored_radius_mm'];m['authored_radius_mm']=radius
            if radius:
                arc=xy[np.max(xy,axis=1)<radius+1e-5]
                assert len(arc)==row['segments']+1,(kind,name,arc)
                design=np.column_stack((2*arc,np.ones(len(arc))));rhs=(arc*arc).sum(1)
                fit=np.linalg.lstsq(design,rhs,rcond=None)[0];center=fit[:2]
                measured=float(np.sqrt(fit[2]+center@center));residual=float(abs(np.linalg.norm(arc-center,axis=1)-measured).max())
                m.update(measured_radius_mm=measured,maximum_radial_residual_mm=residual,arc_vertices=len(arc))
                caption=f'Authored radius: {radius:.3f} mm\nMeasured vertex radius: {measured:.6f} mm\n{row["segments"]} straight mesh segments approximate the arc'
            else:
                restored=np.array_equal(np.asarray(row['vertices_world_mm']),source) and polys==original['polygons']
                m['matches_original_sharp_mesh_exactly']=bool(restored)
                caption='Radius: 0 mm — original sharp corner\n'+('Exact original vertex positions and faces' if restored else 'Sharp source')
            summary[kind][name]=m
            ax.set(xlim=(-.025,.35),ylim=(-.025,.35),xlabel='X from original corner (mm)',ylabel='Y from original corner (mm)')
            ax.set_aspect('equal');ax.grid(alpha=.2)
            ax.set_title({'before':'BEFORE','half':'PREVIEW — half amount','full':'AFTER — full amount'}[name],fontsize=12)
            inset=ax.inset_axes([.66,.57,.28,.39]);inset.add_collection(LineCollection(lines,colors='#52606d',linewidths=.8))
            inset.add_patch(Rectangle((0,0),.35,.35,fill=False,edgecolor='#1565c0',lw=1.1))
            inset.set(xlim=(-.07,np.ptp(source[:,0])+.07),ylim=(-.07,np.ptp(source[:,1])+.07))
            inset.set_aspect('equal');inset.set_xticks([]);inset.set_yticks([]);inset.set_title('whole end profile',fontsize=8)
            fig.text((column-.5)/3,.13,caption,ha='center',va='center',fontsize=10)
        fig.text(.5,.035,'Blue: actual saved end-cap boundary and vertices. Dashed grey: original sharp corner. All three panels use the same physical scale.',ha='center',fontsize=10)
        fig.text(.5,.009,'The selected leaf is a disclosed coarse fixture inside the copied original leaf bounds; the photo-first AR gallery supplies full-product context.',ha='center',fontsize=9)
        fig.subplots_adjust(left=.06,right=.98,top=.8,bottom=.28,wspace=.28)
        fig.savefig(folder/'isolated-mesh-before-preview-after.png',dpi=150);plt.close(fig)
    (root/'diagnostic-metrics.json').write_text(json.dumps(summary,indent=2))


if __name__=='__main__':
    args=sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else sys.argv[1:]
    root=Path(args[0]).resolve()
    if '--capture' in args:capture(root)
    else:plot(root)
