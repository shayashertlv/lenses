"""Offline native checks; exclusively new synthetic assets and deterministic edits."""
from __future__ import annotations
import argparse
import asyncio
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.blender_runner import BlenderRunner, DEFAULT_BLENDER, sha256, BlenderError


def native(script, arguments, output):
    with output.open('wb') as log:
        result=subprocess.run([str(DEFAULT_BLENDER),'--background','--factory-startup','--disable-autoexec','--python',str(ROOT/'blender'/script),'--',*[str(v) for v in arguments]],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,timeout=300)
    if result.returncode: raise RuntimeError(f'Fixture failed; see {output}')


async def run(args):
    output=ROOT/'data'/'selftest'/('native-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    output.mkdir(parents=True)
    fixture=output/'fixture'
    native('synthetic_fixture.py',[fixture]+(['--fused'] if args.fused else []),output/'fixture.log')
    if args.fixture_only:
        print(json.dumps({'fixture':str(fixture),'output':str(output)})); return
    runner=BlenderRunner(timeout=600,resolution=args.resolution,samples=args.samples)
    results={}
    blank=await runner.run('import',fixture/'blank.glb',output/'blank')
    results['blank']=blank
    current=blank
    for stage in ('lenses','connections'):
        script=(ROOT/'scripts'/'fixtures'/f'{stage}.py').read_text(encoding='utf-8')
        if stage == 'lenses' and args.fused:
            script=(ROOT/'scripts'/'fixtures'/'fused_optics.py').read_text(encoding='utf-8')+'\n'+script
        current=await runner.run('edit',Path(current['blend_path']),output/stage,script=script,stage=stage)
        results[stage]=current
    assert current['inspection']['closeup']['tagged_lens']
    returned=output/'fake_provider'/'textured.glb'
    native('synthetic_texture.py',[current['model_path'],returned],output/'texture-fixture.log')
    current=await runner.run('import',returned,output/'texture')
    results['texture']=current
    current=await runner.run('edit',Path(current['blend_path']),output/'finish',script=(ROOT/'scripts'/'fixtures'/'finish.py').read_text(encoding='utf-8'),stage='finish')
    results['finish']=current
    assert current['inspection']['geometry_sha256']==results['texture']['inspection']['geometry_sha256']
    errors={}
    bad_scripts={'finish_geometry':('finish',"import bpy\nobj=next(o for o in bpy.context.scene.objects if o.type=='MESH')\nobj.data.vertices[0].co.x += .001\n"),
                 'connections_frame':('connections',"import bpy\nobj=next(o for o in bpy.context.scene.objects if o.type=='MESH' and not o.get('auto_role'))\nobj.data.vertices[0].co.x += .001\n"),
                 'lenses_rebuild':('lenses',"import bpy\nobj=next(o for o in bpy.context.scene.objects if o.type=='MESH')\nobj.scale.x=1.1\n")}
    for name,(stage,script) in bad_scripts.items():
        source=results['connections']['blend_path']
        try:
            await runner.run('edit',Path(source),output/name,script=script,stage=stage)
        except (BlenderError,ValueError) as exc:
            errors[name]=str(exc)
        else: raise AssertionError(f'{name} unexpectedly passed')
    assert results['texture']['inspection']['images']
    assert results['texture']['inspection']['images']==results['finish']['inspection']['images']
    report={'ok':True,'synthetic':True,'fused_optics':args.fused,'paid_calls':0,'results':results,'rejected':errors,
            'quality_claim':'Software validation only; no real product or provider quality measurement.'}
    (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'report':str(output/'report.json')}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--resolution',type=int,default=192); parser.add_argument('--samples',type=int,default=4); parser.add_argument('--fixture-only',action='store_true'); parser.add_argument('--fused',action='store_true')
    asyncio.run(run(parser.parse_args()))
