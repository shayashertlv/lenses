import json, os, sys, numpy as np
from PIL import Image
def lens_key(boxes, which):
    if 'TomFordClearMesh_1' in boxes: return 'TomFordClearMesh_1'
    return [k for k in boxes if which in k.lower() and 'lens' in k.lower() and 'pad' not in k.lower()][0]
def metrics(rep, case, variants, views=None):
    d=json.load(open(os.path.join(rep,'report.json'))); c=[c for c in d['cases'] if c['id']==case][0]
    rows={}
    for r in c['renders']:
        v=r['view']['id']
        if views and v not in views: continue
        if r['variant'] not in variants: continue
        which='right' if v in ('yawL35','yawL25above15') else 'left'
        x0,y0,x1,y1=r['boxes'][lens_key(r['boxes'],which)]
        if case=='live':
            mid=(x0+x1)//2; x0,x1=(x0,mid-30) if which=='left' else (mid+30,x1)
        w=x1-x0; xa,xb=int(x0+.12*w),int(x1-.12*w)
        A=np.asarray(Image.open(os.path.join(rep,r['filename'])).convert('RGB')).astype(float)
        band=A[max(0,y0-8):y0+20, xa:xb]
        L=band.mean(2); sat=band.max(2)-band.min(2)
        white=((L>215)&(sat<40)).mean()
        # vertical edge count per column: strong luminance steps (>18 levels over 1px) counted as separate runs
        g=np.abs(np.diff(L,axis=0))>18
        edges=[]
        for col in g.T:
            runs=np.sum(col[1:] & ~col[:-1]) + int(col[0]); edges.append(runs)
        rows.setdefault(v,{})[r['variant']]=(round(white*100,1), float(np.median(edges)))
    return rows
if __name__=='__main__':
    rep,case=sys.argv[1],sys.argv[2]; variants=sys.argv[3].split(',')
    rows=metrics(rep,case,variants)
    print(f'{case}: cell = white% in junction band / median vertical edge count per column')
    print('view'.ljust(16)+''.join(v.ljust(16) for v in variants))
    for v,row in rows.items(): print(v.ljust(16)+''.join((f'{row[k][0]}%/{row[k][1]:.0f}' if k in row else '-').ljust(16) for k in variants))
