"""Package local proof images byte-for-byte into one owner-readable review page.

The manifest supplies plain captions and existing images; this does not render or
edit a model, judge likeness, create approval, or call an external service.
"""
import hashlib
import html
import json
import os
from pathlib import Path
import shutil
import sys
from urllib.parse import quote


def build(manifest, destination):
    manifest = Path(manifest).resolve(strict=True)
    doc = json.loads(manifest.read_text(encoding='utf-8'))
    out = Path(destination).resolve()
    if out.exists() or any(p in out.parts for p in ('deliveries', 'deliverable', 'checkpoints')):
        raise ValueError('choose a fresh scratch review folder')
    sources = [Path(item['path']).resolve(strict=True)
               for section in doc['sections'] for item in section.get('images', [])]
    if any(not p.is_file() for p in sources):
        raise ValueError('every review image must be an existing file')
    out.mkdir(parents=True)
    (out/'images').mkdir()
    body = ['<!doctype html><meta charset="utf-8"><title>M7 / M8 copy review</title>',
            '<style>body{font:18px/1.5 Arial,sans-serif;margin:30px auto;max-width:1500px;padding:0 20px;background:#f4f5f7;color:#17212d}h1{font-size:32px}h2{font-size:25px}section{background:white;padding:24px;margin:26px 0;border-radius:8px}img{max-width:100%;height:auto}figure{margin:20px 0}figcaption{font-size:15px;color:#425466}p{max-width:1050px}a{color:#1559a1}nav{display:flex;gap:15px;flex-wrap:wrap}</style>',
            '<h1>'+html.escape(doc['title'])+'</h1>']
    for paragraph in doc['introduction']:
        body.append('<p>'+html.escape(paragraph)+'</p>')
    body.append('<nav>'+''.join('<a href="#s'+str(i)+'">'+html.escape(s['nav'])+'</a>'
                              for i,s in enumerate(doc['sections']))+'</nav>')
    records=[]
    for index,section in enumerate(doc['sections']):
        body.append('<section id="s'+str(index)+'"><h2>'+html.escape(section['title'])+'</h2>')
        for paragraph in section.get('paragraphs',[]):
            body.append('<p>'+html.escape(paragraph)+'</p>')
        for link in section.get('links',[]):
            path=Path(link['path']).resolve(strict=True)
            href=quote(Path(os.path.relpath(path,out)).as_posix(),safe='/.:')
            body.append('<p><a href="'+html.escape(href,quote=True)+'">'+html.escape(link['label'])+'</a></p>')
        for number,item in enumerate(section.get('images',[])):
            source=Path(item['path']).resolve(strict=True)
            relative=Path('images')/f'{index:02d}-{number:02d}{source.suffix}'
            target=out/relative
            digest=hashlib.sha256(source.read_bytes()).hexdigest()
            shutil.copyfile(source,target)
            assert hashlib.sha256(target.read_bytes()).hexdigest()==digest
            records.append({'source':str(source),'file':relative.as_posix(),'sha256':digest})
            body.append('<figure><img loading="lazy" src="'+relative.as_posix()+'" alt="'+
                        html.escape(item['caption'],quote=True)+'"><figcaption>'+html.escape(item['caption'])+'</figcaption></figure>')
        body.append('</section>')
    (out/'index.html').write_text('\n'.join(body),encoding='utf-8')
    (out/'manifest.json').write_text(json.dumps({'source_manifest':str(manifest),'cost_usd':0,
        'api_calls':0,'owner_confirmation':False,'images':records,'content':doc},indent=2),encoding='utf-8')
    print(out/'index.html')


if __name__=='__main__':
    build(sys.argv[1],sys.argv[2])
