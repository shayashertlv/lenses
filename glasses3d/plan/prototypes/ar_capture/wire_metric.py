import json, os, numpy as np
from PIL import Image
from band_metrics import lens_key
d=json.load(open('ab4/report.json'))
print('visible eyewire (green) pixels per column in the band above the lens top (rows y0-12..y0+12), median / p90, and % of columns with any; plus eyewire px visible in whole crop')
for c in d['cases']:
    print('--',c['id'])
    for r in c['renders']:
        v=r['view']['id']; which='right' if v in ('yawL35','yawL25above15') else 'left'
        x0,y0,x1,y1=r['boxes'][lens_key(r['boxes'],which)]; w=x1-x0; xa,xb=int(x0+.12*w),int(x1-.12*w)
        A=np.asarray(Image.open(os.path.join('ab4',r['filename'])).convert('RGB')).astype(int)
        band=A[max(0,y0-12):y0+12, xa:xb]; g=(band[...,1]>200)&(band[...,0]<80)&(band[...,2]<80)
        per=g.sum(0)
        print(f'  {v:16s} median {np.median(per):.0f}px p90 {np.percentile(per,90):.0f}px cols-with-wire {100*(per>0).mean():.0f}%')
