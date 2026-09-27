"""Prepare/test the pending production edit without changing frozen sources."""
import hashlib
import json
from pathlib import Path


def build():
    root = Path(__file__).resolve().parents[2]
    source = root/'reconstruction/intrinsic_frame_appearance.py'
    raw = source.read_bytes(); code = raw.decode('utf-8')
    replacements = [
        ("from .", "from reconstruction."),
        ("METHOD = 'source_bound_multiview_frame_lighting_v1'", "METHOD = 'source_bound_multiview_frame_lighting_v2'"),
        ("def _photo_rasters(mesh, regions, source_sha, pins):", "def _photo_rasters(mesh, regions, source_sha, pins, *, region_directory, support_output):"),
        ("        size = projection['working_size']", "        from qa.staged_frame_membership.frame_image_support import build_frame_image_support\n        support = build_frame_image_support(photo,region_directory,support_output/photo['id'],pins=pins)\n        size = projection['working_size']"),
        ("'first':first,'interior':interior,'mesh':normalized", "'first':first,'interior':interior,'image_support':support,'mesh':normalized"),
        ("    directions = np.zeros((len(photos),count,3))", "    directions = np.zeros((len(photos),count,3))\n    geometric_eligible = np.zeros((len(photos),count),bool)\n    membership = np.zeros((len(photos),count),np.uint8)\n    alpha_codes = np.zeros((len(photos),count),np.uint8)"),
        ("        valid[vi,ids] = opaque\n    return samples,valid,directions", "        geometric_eligible[vi,ids] = True\n        membership[vi,ids] = photo['image_support']['membership'][py,px]\n        alpha_codes[vi,ids] = rgba[:,3]\n        valid[vi,ids] = opaque & (membership[vi,ids] == 1)\n    audit = {'geometric_eligible':geometric_eligible,'image_membership':membership,'source_alpha':alpha_codes,\n        'photos':[{'photo_id':photo['id'],'geometrically_eligible':int(geometric_eligible[i].sum()),\n                   'supported':int(valid[i].sum()),\n                   'unknown_image_membership':int((geometric_eligible[i] & (membership[i] == 0)).sum()),\n                   'authored_exterior':int((geometric_eligible[i] & (membership[i] == 2)).sum()),\n                   'nonopaque_alpha':int((geometric_eligible[i] & (alpha_codes[i] != 255)).sum())}\n                  for i,photo in enumerate(photos)]}\n    return samples,valid,directions,audit"),
        ("    photos = _photo_rasters(mesh,regions,source_sha,pins)", "    photos = _photo_rasters(mesh,regions,source_sha,pins,region_directory=region_report.parent,\n                            support_output=output/'photo-support')"),
        ("        samples,valid,directions = _observe_tracks(tracks,photos)", "        samples,valid,directions,membership_audit = _observe_tracks(tracks,photos)"),
        ("baseline_linear_rgb=tracks['baseline'],ratio_rgb=separated['ratio_rgb']", "geometric_eligible=membership_audit['geometric_eligible'],image_membership=membership_audit['image_membership'],\n            source_alpha=membership_audit['source_alpha'],\n            baseline_linear_rgb=tracks['baseline'],ratio_rgb=separated['ratio_rgb']"),
        ("'corrected_texture_pixels':int(np.count_nonzero", "'image_membership_exclusions':membership_audit['photos'],\n            'corrected_texture_pixels':int(np.count_nonzero"),
        ("'surface_coverage':surface_coverage,", "'surface_coverage':surface_coverage,\n        'photo_image_support':{p['id']:p['image_support']['report'] for p in photos},"),
    ]
    for old,new in replacements:
        count = code.count(old)
        if (old != 'from .' and count != 1) or (old == 'from .' and count < 1):
            raise ValueError(f'Staged production source changed: replacement count {count} for {old!r}')
        code = code.replace(old,new)
    folder = Path(__file__).resolve().parent
    (folder/'intrinsic_frame_appearance.py').write_text(code,encoding='utf-8')
    manifest = {'source':str(source),'source_sha256':hashlib.sha256(raw).hexdigest(),
                'staged_module':'qa.staged_frame_membership.intrinsic_frame_appearance',
                'production_changes_applied':False}
    (folder/'stage-source.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return manifest


if __name__ == '__main__':
    print(json.dumps(build(),indent=2))
