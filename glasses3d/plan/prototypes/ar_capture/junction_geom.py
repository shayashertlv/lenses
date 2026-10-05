import sys, numpy as np
from glbmesh import meshes
p=sys.argv[1]; lensname=sys.argv[2]; browname=sys.argv[3]; rimname=sys.argv[4] if len(sys.argv)>4 else None
M=meshes(p)
def get(name): 
    r=[m for m in M if m['node']==name or m['mesh']==name]
    assert r, name; return r[0]
L=get(lensname); B=get(browname); E=get(rimname) if rimname else None
def tris(m): return m['P'][m['I']]
# per x-bin: lens max y; brow min y (lower surface) among brow vertices in front zone; z ranges
xs=np.linspace(L['P'][:,0].min()+0.001, L['P'][:,0].max()-0.001, 13)
print('x(mm) | lens_top_y z@top | brow_bottom_y (z range at bottom) | eyewire top_y/inner-bottom | brow covers lens? overlap mm')
for x in xs:
    w=0.0008
    lp=L['P'][np.abs(L['P'][:,0]-x)<w]
    bp=B['P'][np.abs(B['P'][:,0]-x)<w]
    if len(lp)==0 or len(bp)==0: continue
    ly=lp[:,1].max(); lz=lp[lp[:,1]>ly-0.0003][:,2]
    by=bp[:,1].min(); bz=bp[bp[:,1]<by+0.0003][:,2]
    s=f'{x*1000:7.1f} | {ly*1000:6.2f} z[{lz.min()*1000:5.2f},{lz.max()*1000:5.2f}] | {by*1000:6.2f} z[{bz.min()*1000:5.2f},{bz.max()*1000:5.2f}]'
    if E is not None:
        ep=E['P'][np.abs(E['P'][:,0]-x)<w]
        # upper part of eyewire: vertices with y > lens center
        eu=ep[ep[:,1]>0]
        if len(eu): s+=f' | ew y[{eu[:,1].min()*1000:6.2f},{eu[:,1].max()*1000:6.2f}] z[{eu[:,2].min()*1000:5.2f},{eu[:,2].max()*1000:5.2f}]'
    s+=f' | overlap {(ly-by)*1000:+.2f}'
    print(s)
