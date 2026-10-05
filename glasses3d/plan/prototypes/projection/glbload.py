import json, struct, numpy as np
CT={5120:np.int8,5121:np.uint8,5122:np.int16,5123:np.uint16,5125:np.uint32,5126:np.float32}
NC={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4,'MAT4':16}
def split(path):
    b=open(path,'rb').read()
    assert b[:4]==b'glTF'
    off=12; doc=None; binc=b''
    while off<len(b):
        ln,typ=struct.unpack('<II',b[off:off+8]); chunk=b[off+8:off+8+ln]
        if typ==0x4E4F534A: doc=json.loads(chunk)
        else: binc=chunk
        off+=8+ln
    return doc,binc
def acc(doc,binc,i):
    a=doc['accessors'][i]; bv=doc['bufferViews'][a['bufferView']]
    n=NC[a['type']]; dt=CT[a['componentType']]
    start=bv.get('byteOffset',0)+a.get('byteOffset',0)
    stride=bv.get('byteStride',0)
    itemsize=np.dtype(dt).itemsize*n
    if stride and stride!=itemsize:
        raw=np.frombuffer(binc,dtype=np.uint8,count=stride*a['count'],offset=start).reshape(a['count'],stride)[:,:itemsize]
        arr=np.frombuffer(raw.tobytes(),dtype=dt).reshape(a['count'],n)
    else:
        arr=np.frombuffer(binc,dtype=dt,count=a['count']*n,offset=start).reshape(a['count'],n)
    if a.get('normalized'):
        arr=arr.astype(np.float64)/np.iinfo(dt).max
    return arr
def node_mat(n):
    if 'matrix' in n: return np.array(n['matrix'],float).reshape(4,4).T
    T=np.eye(4); t=n.get('translation',[0,0,0]); r=n.get('rotation',[0,0,0,1]); s=n.get('scale',[1,1,1])
    x,y,z,w=r
    R=np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],[2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],[2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
    T[:3,:3]=R*np.array(s)[None,:]; T[:3,3]=t; return T
def load(path):
    doc,binc=split(path)
    prims=[]
    def walk(ni,M):
        n=doc['nodes'][ni]; M2=M@node_mat(n)
        if 'mesh' in n:
            for pi,p in enumerate(doc['meshes'][n['mesh']]['primitives']):
                V=acc(doc,binc,p['attributes']['POSITION']).astype(float)
                V=(np.c_[V,np.ones(len(V))]@M2.T)[:,:3]
                F=acc(doc,binc,p['indices']).reshape(-1,3).astype(np.int64) if 'indices' in p else np.arange(len(V)).reshape(-1,3)
                UV=acc(doc,binc,p['attributes']['TEXCOORD_0']).astype(float) if 'TEXCOORD_0' in p['attributes'] else None
                N=acc(doc,binc,p['attributes']['NORMAL']).astype(float) if 'NORMAL' in p['attributes'] else None
                mat=doc['materials'][p['material']] if 'material' in p else {}
                prims.append(dict(node=n.get('name'),mesh=doc['meshes'][n['mesh']].get('name'),material=mat.get('name'),V=V,F=F,UV=UV,N=N,matdef=mat,M=M2))
        for c in n.get('children',[]): walk(c,M2)
    for ni in doc['scenes'][doc.get('scene',0)]['nodes']: walk(ni,np.eye(4))
    return doc,binc,prims
