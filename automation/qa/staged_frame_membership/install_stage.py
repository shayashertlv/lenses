"""Apply the reviewed staged files after the root agent lifts the source freeze."""
import hashlib
import json
from pathlib import Path


root = Path(__file__).resolve().parents[2]
staged = Path(__file__).resolve().parent
manifest = json.loads((staged/'stage-source.json').read_bytes())
source = Path(manifest['source'])
if hashlib.sha256(source.read_bytes()).hexdigest() != manifest['source_sha256']:
    raise ValueError('Production intrinsic-frame source changed since staging')
support = (staged/'frame_image_support.py').read_text(encoding='utf-8')
support = support.replace('Staged implementation: move to reconstruction only after the source freeze ends.\n','')
support = support.replace('from reconstruction.', 'from .')
(root/'reconstruction/frame_image_support.py').write_text(support,encoding='utf-8')
intrinsic = (staged/'intrinsic_frame_appearance.py').read_text(encoding='utf-8')
intrinsic = intrinsic.replace('from qa.staged_frame_membership.frame_image_support import','from .frame_image_support import')
intrinsic = intrinsic.replace('from reconstruction.', 'from .')
source.write_text(intrinsic,encoding='utf-8')
tests = (staged/'test_frame_image_support.py').read_text(encoding='utf-8')
tests = tests.replace('from qa.staged_frame_membership.frame_image_support import','from reconstruction.frame_image_support import')
tests = tests.replace('from qa.staged_frame_membership.intrinsic_frame_appearance import','from reconstruction.intrinsic_frame_appearance import')
tests = tests.replace("sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'tests'))",'')
(root/'tests/test_frame_image_support.py').write_text(tests,encoding='utf-8')
print('Installed frame support, intrinsic integration and focused tests')
