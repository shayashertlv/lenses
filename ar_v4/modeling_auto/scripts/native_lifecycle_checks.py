"""Cancellation and timeout of owned Blender processes; no remote calls."""
from __future__ import annotations
import asyncio
from datetime import datetime
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.blender_runner import BlenderRunner, BlenderError, sha256


async def main():
    source=Path(sys.argv[1]).resolve()
    output=ROOT/'data'/'selftest'/('native-lifecycle-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    output.mkdir(parents=True)
    digest=sha256(source)
    report={'paid_calls':0,'input_sha256':digest}
    cancel=asyncio.Event()
    async def trigger():
        await asyncio.sleep(3)
        cancel.set()
    timer=asyncio.create_task(trigger())
    try:
        await BlenderRunner(timeout=60).run('edit',source,output/'cancel',script='while True:\n    pass\n',stage='finish',cancel=cancel)
    except asyncio.CancelledError:
        report['cancellation']='owned process terminated; no revision produced'
    else: raise AssertionError('Infinite native edit was not cancelled')
    await timer
    try:
        await BlenderRunner(timeout=3).run('edit',source,output/'timeout',script='while True:\n    pass\n',stage='finish')
    except BlenderError as exc:
        assert 'exceeded' in str(exc)
        report['timeout']=str(exc)
    else: raise AssertionError('Infinite native edit did not time out')
    assert sha256(source)==digest
    assert not (output/'cancel'/'master.blend').exists()
    assert not (output/'timeout'/'master.blend').exists()
    report['ok']=True
    (output/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'report':str(output/'report.json')}))


if __name__=='__main__': asyncio.run(main())
