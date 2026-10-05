import json, struct, numpy as np
CT={5126:np.float32,5125:np.uint32,5123:np.uint16,5121:np.uint8}
NC={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4}
def load(p):
    b=open(p,'rb').read()
    jl=struct.unpack('<I',b[12:16])[0]; d=json.loads(b[20:20+jl])
    off=20+jl; bl=struct.unpack('<I',b[off:off+4])[0]; bin_=b[off+8:off+8+bl]
    return d,bin_
def acc(d,bin_,i):
    a=d['accessors'][i]; bv=d['bufferViews'][a['bufferView']]
    n=NC[a['type']]; dt=np.dtype(CT[a['componentType']])
    start=bv.get('byteOffset',0)+a.get('byteOffset',0); stride=bv.get('byteStride',0)
    if stride and stride!=n*dt.itemsize:
        raw=np.frombuffer(bin_,dtype=np.uint8,count=stride*a['count'],offset=start).reshape(a['count'],stride)
        arr=raw[:,:n*dt.itemsize].copy().view(dt).reshape(a['count'],n)
    else:
        arr=np.frombuffer(bin_,dtype=dt,count=a['count']*n,offset=start).reshape(a['count'],n)
    return arr.astype(np.float64) if dt==np.float32 else arr.astype(np.int64)
def meshes(p):
    d,b=load(p); out=[]
    for ni,n in enumerate(d['nodes']):
        if 'mesh' not in n: continue
        m=d['meshes'][n['mesh']]
        for j,pr in enumerate(m['primitives']):
            P=acc(d,b,pr['attributes']['POSITION']); N=acc(d,b,pr['attributes']['NORMAL']) if 'NORMAL' in pr['attributes'] else None
            I=acc(d,b,pr['indices']).reshape(-1,3) if 'indices' in pr else np.arange(len(P)).reshape(-1,3)
            mat=d['materials'][pr['material']]['name'] if 'material' in pr else None
            out.append(dict(node=n.get('name'),mesh=m.get('name'),prim=j,mat=mat,P=P,N=N,I=I))
    return out
