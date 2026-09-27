"""Freeze image-only optical eligibility before any material or mask fit."""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import zlib

import numpy as np


METHOD = 'frozen_image_fit_support_v1'


def freeze_grounded_fit_support(semantic, grounding):
    entries = {p['photo_id']:p for p in semantic['image_manifest']}
    rows = {}
    for photo, mask in grounding.get('fit_masks', {}).items():
        entry = entries[photo]
        mask = np.asarray(mask)
        if mask.dtype != bool or list(mask.shape[::-1]) != entry['image_size']:
            raise ValueError('Grounded fit support requires the bound boolean image grid')
        packed = np.packbits(mask).tobytes()
        rows[photo] = {'source_sha256':entry['sha256'], 'image_size':entry['image_size'],
            'eligible_pixels':int(mask.sum()), 'packed_mask_sha256':hashlib.sha256(packed).hexdigest(),
            'encoding':'zlib_base64_packbits_big_row_major',
            'mask':base64.b64encode(zlib.compress(packed,9)).decode('ascii')}
    return {'method':METHOD, 'grounding_request_sha256':grounding['report']['request_sha256'],
            'selection_basis':'image_aperture_consensus_and_edge_exclusion_before_any_optical_fit',
            'residual_selected':False, 'photos':rows,
            'limitations':['Image support remains a semantic hypothesis; frozen means independent of fitted optical residuals.',
                           'Photographic reflection regions remain eligible for the explicit reflection nuisance model.']}


def _decode(row):
    size = row.get('image_size')
    if (not isinstance(size,list) or len(size)!=2 or any(type(v)is not int or v<2 for v in size)
            or size[0]*size[1]>64_000_000 or row.get('encoding')!='zlib_base64_packbits_big_row_major'):
        raise ValueError('Unsupported grounded fit-support grid/encoding')
    n = (size[0]*size[1]+7)//8
    encoded = row.get('mask')
    if not isinstance(encoded,str) or len(encoded)>2*n+1024:
        raise ValueError('Grounded fit-support mask exceeds its grid budget')
    try:
        compressed = base64.b64decode(encoded,validate=True)
        decoder=zlib.decompressobj()
        packed=decoder.decompress(compressed,n+1)
        if len(packed)!=n or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('Grounded mask length differs from source grid')
    except (ValueError,zlib.error) as error:
        raise ValueError('Invalid packed grounded fit-support mask') from error
    if hashlib.sha256(packed).hexdigest()!=row.get('packed_mask_sha256'):
        raise ValueError('Grounded fit-support checksum changed')
    mask=np.unpackbits(np.frombuffer(packed,np.uint8),count=size[0]*size[1]).reshape(size[1],size[0]).astype(bool)
    if int(mask.sum())!=row.get('eligible_pixels'):
        raise ValueError('Grounded fit-support pixel count changed')
    return mask


def apply_grounded_fit_support(groups, priors):
    support=(priors or {}).get('frozen_image_fit_support')
    if support is None:
        return groups
    if (support.get('method')!=METHOD or support.get('residual_selected') is not False
            or not isinstance(support.get('photos'),dict)):
        raise ValueError('Fitter support must be frozen independently of optical residuals')
    result, decoded=[],{}
    for group in groups:
        observations=[]
        for original in group['observations']:
            photo=original['photo_id']; row=support['photos'].get(photo)
            if row is None:
                raise ValueError('A fitted photo has no frozen image-grounded eligibility support')
            if (row['source_sha256']!=original['source_sha256'] or row['image_size']!=original.get('image_size')
                    or (priors or {}).get('photos',{}).get(photo,{}).get('source_sha256')!=row['source_sha256']):
                raise ValueError('Grounded fit-support source/photo/grid differs from observations')
            if photo not in decoded:
                decoded[photo]=_decode(row)
            xy=np.asarray(original['xy'],float)
            if not np.isfinite(xy).all() or np.any(xy!=np.floor(xy)) or np.any(xy<0) or np.any(xy>=row['image_size']):
                raise ValueError('Grounded eligibility requires source-pixel sample coordinates')
            indices=xy.astype(int)
            eligible=decoded[photo][indices[:,1],indices[:,0]].astype(np.uint8)
            if 'image_eligible' in original and not np.array_equal(np.asarray(original['image_eligible']),eligible):
                raise ValueError('Existing image eligibility differs from its frozen source mask')
            observation=dict(original,image_eligible=eligible)
            observation['provenance']=deepcopy(original['provenance'])
            observation['provenance']['frozen_image_support']={'method':METHOD,
                'grounding_request_sha256':support['grounding_request_sha256'],
                'packed_mask_sha256':row['packed_mask_sha256'],'retained_samples':int(eligible.sum())}
            observations.append(observation)
        result.append({'surface_binding':deepcopy(group['surface_binding']),'observations':observations})
    return result
