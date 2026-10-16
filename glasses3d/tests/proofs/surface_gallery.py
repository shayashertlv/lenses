"""Offline AR evidence, photo first, for immutable source and scratch model variants.

This is a tool-proof utility, not a delivery generator. It never invokes Astra,
creates a run request or estimates one. The selected delivery is read-only.
"""
from __future__ import annotations
import argparse
import hashlib
import html
import json
from pathlib import Path
import shutil
import subprocess
import time
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[3]
ARVIEW = ROOT/'glasses3d/arview/bin/arview.mjs'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def strip(paths, labels, destination, box=None):
    images = [Image.open(path).convert('RGB') for path in paths]
    if len({im.size for im in images}) != 1:
        raise ValueError('Every proof panel must share the frozen camera pixel grid')
    if box:
        images = [im.crop(tuple(box)) for im in images]
    w,h = images[0].size
    out = Image.new('RGB',(w*len(images),h+42),'white')
    d = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf',20)
    except OSError:
        font = ImageFont.load_default()
    for i,(label,im) in enumerate(zip(labels,images)):
        d.text((i*w+10,10), label,fill='black',font=font)
        out.paste(im,(i*w,42))
    out.save(destination)
    return {'path':str(destination),'sha256':sha(destination),'box':box,'labels':labels}


def append_worn(out):
    """Append the normal test-face capture for every already copied variant."""
    from glasses3d.agent.tools import arview
    out=Path(out).resolve();records=json.loads((out/'report.json').read_text())
    work=out/'work';work.mkdir(exist_ok=True)
    rows=[];paths=[];labels=[]
    for index,variant in enumerate(records['variants']):
        source=out/f'{index:02d}.glb'
        if sha(source)!=variant['sha256']:raise ValueError('proof model bytes changed')
        target=work/f'{index:02d}.glb';shutil.copyfile(source,target)
        result=arview.capture(out,{'status':'exported','path':str(target),'sha256':sha(target),
                                  'checkpoint':'offline-copy'},f'worn-{index}')
        if result['status']!='ok':raise RuntimeError(result)
        report_path=Path(result['report']);capture=json.loads(report_path.read_text())
        portrait=next(r for r in capture['images'] if r.get('kind')=='portrait' and
                      r.get('variant')=='condition-a' and r.get('region')=='eyes')
        paths.append(report_path.parent/portrait['file']);labels.append(variant['label'].upper())
        rows.append(result)
    panel=strip(paths,labels,out/'worn-before-preview-after.png')
    records['worn']={'captures':rows,'panel':panel,'note':'Test face; product photos precede this separate worn view.'}
    (out/'report.json').write_text(json.dumps(records,indent=2))
    with (out/'index.html').open('a',encoding='utf-8') as page:
        page.write('<section><h2>Worn on the test face</h2><img src="worn-before-preview-after.png"></section>')
    return records['worn']


def refresh_selection(out, selection, snapshot):
    """Refresh only labelled scratch overlays and their hashes; reuse exact renders."""
    from glasses3d.agent.tools import model_map as maps, model_map_geometry as G, look_panels
    from glasses3d.g3d import owner_mesh as M
    out = Path(out).resolve()
    records = json.loads((out/'report.json').read_text())
    if sha(out/'00.glb') != records['variants'][0]['sha256']:
        raise ValueError('proof source model bytes changed')
    chosen = json.loads(Path(selection).read_text())
    state = json.loads(Path(snapshot).read_text())
    geometry = look_panels.load_geometry(out/'00.glb')
    source_obj, source_poly, correspondence = G.export_correspondence(state, geometry)
    obj, scope, label = chosen['object'], chosen['scope'], chosen['label']
    target = {'id': label, 'kind': 'face_region', 'object_id': obj['id'], 'aliases': [],
              'polygon_ids': scope['affected_polygon_ids'], 'source_editable': True}
    support = {**scope, 'target_ids': [label],
               'normal_affected_polygon_ids': sorted(M.normal_faces(obj, scope['affected_polygon_ids']))}
    labels = {label: {'label': label, 'target_id': label, 'description': chosen['legend']}}
    rows = []
    for row in records['labelled_selection']['rows']:
        name = row['camera']['id']
        mapped = {**row, 'photo': str(Path(records['delivery'])/'look'/(name+'-photo.png')),
                  'drawing': str(out/'room-0'/(name+'__beauty.png')),
                  'part_id': str(out/'room-0'/(name+'__part-id.png'))}
        rows.append(maps._annotate(mapped, state, [target], geometry, source_obj, source_poly,
            [support], out/'selection', 0, display_labels=labels, descriptions={label: chosen['legend']}))
    records['labelled_selection'].update(rows=rows, correspondence=correspondence)
    (out/'report.json').write_text(json.dumps(records, indent=2))
    return records['labelled_selection']


def build(delivery, variants, out, refs=None, selection=None, snapshot=None):
    delivery,out = Path(delivery).resolve(),Path(out).resolve()
    if out.is_relative_to(delivery):
        raise ValueError('Proof outputs must be outside the immutable delivery')
    source = {str(p):sha(p) for p in delivery.rglob('*') if p.is_file()}
    out.mkdir(parents=True,exist_ok=False)
    report = json.loads((delivery/'look/comparison-report.json').read_text(encoding='utf-8'))
    rows = [r for r in report['views'] if refs is None or r['ref'] in refs]
    cameras = [r['camera'] for r in rows]
    records = {'schema':'g3d.surface_gallery/1','cost_usd':0,'api_calls':0,
               'label':'Offline tool proof on copies; scripted by Codex, not Astra; no owner acceptance',
               'delivery':str(delivery),'variants':[],'images':[]}
    copies = []
    for label,path in variants:
        path = Path(path).resolve()
        target = out/f'{len(copies):02d}.glb';shutil.copyfile(path,target)
        copies.append((label,target))
        records['variants'].append({'label':label,'source':str(path),'sha256':sha(target)})
    for light in ('room','side_studio'):
        cams = [dict(cam) for cam in cameras]
        if light != 'room':
            cams = [{**cam,'lighting':{'preset':light,'intensity':.8}} for cam in cams]
        camera_file = out/f'{light}-cameras.json'
        camera_file.write_text(json.dumps({'schema':'arview.cameras/1','cameras':cams},indent=2))
        captures = []
        for index,(label,path) in enumerate(copies):
            folder = out/f'{light}-{index}'
            command = [shutil.which('node'),str(ARVIEW),'capture',str(path),'--cameras',str(camera_file),
                       '--no-face','--fixed-scale','--lensenv','1','--out',str(folder)]
            print(time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),label,light,flush=True)
            started = time.time()
            with (out/f'{light}-{index}.log').open('w',encoding='utf-8') as log:
                process = subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT)
                record = {'command':command,'pid':process.pid,'started_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
                (out/f'{light}-{index}-process.json').write_text(json.dumps(record,indent=2))
                code = process.wait()
            record.update(exit_code=code,seconds=time.time()-started)
            (out/f'{light}-{index}-process.json').write_text(json.dumps(record,indent=2))
            if code not in (0,3):
                raise RuntimeError(f'AR proof failed {code}; see {light}-{index}.log')
            captures.append(folder)
        for row in rows:
            name = row['camera']['id']
            photo = delivery/'look'/f'{name}-photo.png'
            if not photo.is_file():
                photo = Path(row['photo_path'])
            paths = [photo,*[folder/f'{name}__beauty.png' for folder in captures]]
            labels = ['PHOTO',*[label.upper() for label,_ in copies]]
            image = strip(paths,labels,out/f"{row['ref']}-{light}-full.png",row.get('crop_box'))
            records['images'].append({**image,'ref':row['ref'],'light':light,'kind':'full'})
            for detail in row.get('details',[]):
                for index,im in enumerate(detail.get('images',[])):
                    box = im.get('crop_box')
                    if box:
                        image=strip(paths,labels,out/f"{row['ref']}-{light}-{detail['kind']}-{index}.png",box)
                        records['images'].append({**image,'ref':row['ref'],'light':light,'kind':detail['kind']})
    if selection is not None:
        from glasses3d.agent.tools import model_map as maps, model_map_geometry as G, look_panels
        from glasses3d.g3d import owner_mesh as M
        chosen=json.loads(Path(selection).read_text())
        state=json.loads(Path(snapshot).read_text())
        geometry=look_panels.load_geometry(copies[0][1])
        source_obj,source_poly,correspondence=G.export_correspondence(state,geometry)
        obj=chosen['object'];scope=chosen['scope'];label=chosen['label']
        target={'id':label,'kind':'face_region','object_id':obj['id'],'aliases':[],
                'polygon_ids':scope['affected_polygon_ids'],'source_editable':True}
        support={**scope,'target_ids':[label],
                 'normal_affected_polygon_ids':sorted(M.normal_faces(obj,scope['affected_polygon_ids']))}
        labels={label:{'label':label,'target_id':label,'description':chosen['legend']}}
        folder=out/'selection';folder.mkdir()
        selection_rows=[]
        for row in rows:
            name=row['camera']['id']
            photo=delivery/'look'/f'{name}-photo.png'
            if not photo.is_file():photo=Path(row['photo_path'])
            mapped={**row,'photo':str(photo),'drawing':str(out/'room-0'/f'{name}__beauty.png'),
                    'part_id':str(out/'room-0'/f'{name}__part-id.png')}
            selection_rows.append(maps._annotate(mapped,state,[target],geometry,source_obj,source_poly,
                [support],folder,0,display_labels=labels,descriptions={label:chosen['legend']}))
        records['labelled_selection']={'rows':selection_rows,'correspondence':correspondence,
            'note':'Real map renderer on the exact scratch source; demonstration only, no owner confirmation receipt.'}
    assert source == {str(p):sha(p) for p in delivery.rglob('*') if p.is_file()}, 'Immutable delivery changed'
    records['source_unchanged'] = True
    (out/'report.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    body = '<!doctype html><meta charset="utf-8"><title>Surface tool proof</title><style>body{font:18px Arial;margin:28px;background:#eee}img{max-width:100%;background:white}section{margin:24px 0}p{max-width:1000px}</style>'
    body += '<h1>Surface tool proof</h1><p>'+html.escape(records['label'])+'</p><p>Photo first. Review room and side light use identical model bytes, frozen cameras and material settings. $0; no API calls.</p>'
    for row in records.get('labelled_selection',{}).get('rows',[]):
        body += '<section><h2>Labelled selection / '+html.escape(row['ref'])+'</h2><img src="selection/'+html.escape(Path(row['full_pair']).name)+'"></section>'
    for rec in records['images']:
        if rec['kind']=='full':
            body += '<section><h2>'+html.escape(rec['ref']+' / '+rec['light'])+'</h2><img src="'+html.escape(Path(rec['path']).name)+'"></section>'
    (out/'index.html').write_text(body,encoding='utf-8')
    return records


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--delivery',required=True);parser.add_argument('--out',required=True)
    parser.add_argument('--variant',action='append',required=True,help='LABEL=path to a copied GLB')
    parser.add_argument('--refs',nargs='*')
    parser.add_argument('--selection',help='selection.json emitted by the copy proof')
    parser.add_argument('--snapshot',help='the matching source snapshot.json; required with --selection')
    args=parser.parse_args()
    if bool(args.selection)!=bool(args.snapshot):parser.error('--selection and --snapshot are required together')
    build(args.delivery,[entry.split('=',1) for entry in args.variant],args.out,args.refs,args.selection,args.snapshot)
