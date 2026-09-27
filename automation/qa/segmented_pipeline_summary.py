"""Build a pinned, local-only overview of completed segmented corpus jobs."""
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageOps
from scipy import ndimage

from qa.provider_comparison import font
from reconstruction.segmented_providers import pin, verified


def build(root=Path('data/segmented-pipeline-v1')):
    root = root.resolve()
    output = root / 'final-review'; output.mkdir(parents=True, exist_ok=True)
    names = ['miu', 'oakley', 'rayban', 'vb', 'invu']
    sheet = Image.new('RGB', (1330, 95+len(names)*245), '#eef1f5')
    draw = ImageDraw.Draw(sheet)
    draw.text((18, 14), 'Existing photos to AR candidates', font=font(29), fill='#172b40')
    draw.text((18, 51), 'Different cameras and lighting. Source-informed candidates; visual correctness is not certified.', font=font(18), fill='#465b70')
    for x, label in [(175, 'Product photograph'), (555, 'Actual AR / broad light'), (935, 'Actual AR / angled')]:
        draw.text((x, 79), label, font=font(17), fill='#172b40')
    products=[]
    for i, name in enumerate(names):
        job = root / 'jobs' / name
        result = json.loads((job/'report.json').read_bytes())
        selected = result['selection']['selected_candidate']
        verified(result['candidate'])
        render_path = verified(result['rendering']['report'])
        render = json.loads(render_path.read_bytes())
        case = next(c for c in render['cases'] if c['id']==selected['candidate_id'])
        if case['model_sha256'] != result['candidate']['sha256']:
            raise ValueError('Selected candidate differs from its rendered model')
        request = json.loads((job/'request.json').read_bytes())
        photo = next(p for p in request['photos'] if p['view']=='front')
        images=[('source',verified(photo))]
        for view in ('front','angled'):
            r=next(r for r in case['renders'] if r['view']==view and r['environment']=='broad')
            images.append((view,verified({'path':str(render_path.parent/r['filename']),'sha256':r['sha256']})))
        y=110+i*245
        draw.text((18,y+32),name.upper(),font=font(23),fill='#172b40')
        draw.text((18,y+69),f"{result['delivery']['triangles']:,} tris",font=font(16),fill='#465b70')
        draw.text((18,y+95),f"{result['candidate']['bytes']/1e6:.2f} MB",font=font(16),fill='#465b70')
        draw.text((18,y+123),'Review required',font=font(15),fill='#865117')
        receipts=[]
        for col,(kind,path) in enumerate(images):
            with Image.open(path) as original:
                image=ImageOps.exif_transpose(original).convert('RGB')
                if kind=='source':
                    mask=ndimage.binary_opening(np.asarray(image).min(axis=2)<230,iterations=1)
                    yy,xx=np.where(mask)
                    box=(max(0,int(xx.min())-15),max(0,int(yy.min())-15),min(image.width,int(xx.max())+16),min(image.height,int(yy.max())+16))
                else:
                    box=(145,45,575,300)
                cropped=ImageOps.contain(image.crop(box),(370,215))
                x=175+col*380
                sheet.paste(cropped,(x+(370-cropped.width)//2,y+(215-cropped.height)//2))
                receipts.append(dict(kind=kind,**pin(path),crop_xyxy=box))
        products.append(dict(product=name,status=result['status'],candidate=result['candidate'],
            triangles=result['delivery']['triangles'],material_family=selected['family'],
            material_review=result['selection']['status'],role_groups=result['role_inference']['primary_groups'],
            role_selection_required=result['role_selection_required'],review_reasons=result['review_reasons'],
            source_bijection=result['segmentation_correspondence']['complete_bijection'],
            runtime=case['status'],images=receipts,accepted=False))
    path=output/'five-products-overview.png'; sheet.save(path)
    semantic=[]
    roots=[root/'jobs',root.parent/'segmented-appearance-v1',root.parent/'semantic-schema-adaptation-v1']
    for base in roots:
        for budget in base.rglob('budget.json'):
            for receipt in budget.parent.glob('*.json'):
                row=json.loads(receipt.read_bytes())
                if str(row.get('model','')).startswith('gemini-') and 'provider_response' in row:
                    semantic.append(dict(receipt=pin(receipt),status=row['status'],http=row.get('http_status'),
                        usage=row.get('usage') or row.get('provider_response',{}).get('usageMetadata')))
    tripo=[]
    for path in (root/'jobs').glob('*/providers/*/artifacts.json'):
        row=json.loads(path.read_bytes())
        if row.get('provider')=='tripo':tripo.append(dict(receipt=pin(path),credits=row.get('credits_consumed')))
    masks=[pin(p) for p in (root/'jobs').glob('*/providers/masks/*/submission.json')]
    report=dict(schema_version=1,accepted=False,overview=pin(output/'five-products-overview.png'),products=products,
        calls=dict(tripo=tripo,tripo_credits=sum(r['credits'] or 0 for r in tripo),sam_submissions=masks,
                   semantic_attempts=semantic,semantic_completed=sum(r['status']=='complete' for r in semantic)),
        limitations=['Product photographs are not held-out validation.','Crops and display scaling do not register source and AR cameras.',
            'Runtime compatibility, conditional vision-model preference and source geometry preservation are separate from product fidelity.'])
    (root/'summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    return report


if __name__=='__main__':
    report=build()
    print(json.dumps({'products':len(report['products']),'overview':report['overview']['path']}))
