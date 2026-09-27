"""Image-only lens aperture proposals from pinned local weights.

The engine runs the public Glasses Detector v1 medium lens head on a normalized
product photo at two fixed crop policies (full image, contrast crop) and
returns the positive union per policy with its known prediction domain. No
mesh, camera, part name or optical role enters inference, no network is
touched, and a positive pixel is a proposal, never a lens identity. Callers
that want a controlled stand-in supply any object with ``describe()`` and
``propose(rgb)`` returning the same structure.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re

import numpy as np
from PIL import Image

from .observations import observe_image
from .region_engine import _offline


METHOD = 'offline_glasses_detector_lens_apertures_v1'
VARIANTS = ('full', 'contrast_crop')
MEAN, STD = (.485, .456, .406), (.229, .224, .225)
LENS_WEIGHTS = 'segmentation_lenses_lraspp_mobilenet_v3_large.pth'
POLICY = {'architecture': 'torchvision.lraspp_mobilenet_v3_large; one binary lens logit',
          'input_size': [256, 256], 'resize': 'PIL RGB bicubic', 'mean': MEAN, 'std': STD,
          'threshold': 'logit > 0; uncalibrated proposal', 'variants': list(VARIANTS),
          'contrast_margin_fraction_per_axis': .15, 'contrast_crop_fallback': 'full image when no measured contrast box',
          'native_logit_interpolation': 'bilinear within crop only; outside crop is unknown',
          'device': 'cpu', 'torch_threads': 4, 'deterministic_algorithms': True}
MAXIMUM_NATIVE_PIXELS = 16_000_000


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def native_logits(logits, box, size):
    """Bilinear native-resolution logits inside the crop; NaN outside (unknown)."""
    x0, y0, x1, y1 = box
    result = np.full((size[1], size[0]), np.nan, dtype=np.float32)
    result[y0:y1, x0:x1] = np.asarray(Image.fromarray(np.asarray(logits, np.float32)).resize((x1-x0, y1-y0), Image.Resampling.BILINEAR))
    return result


def contrast_crop_box(image):
    """The fixed contrast crop policy shared with the frozen baseline experiment."""
    width, height = image.size
    contrast = observe_image(image)
    box = (0, 0, width, height)
    if contrast.status == 'measured' and contrast.bbox_xyxy is not None:
        x0, y0, x1, y1 = contrast.bbox_xyxy
        dx, dy = int(np.ceil(.15*(x1-x0))), int(np.ceil(.15*(y1-y0)))
        box = (max(0, x0-dx), max(0, y0-dy), min(width, x1+dx), min(height, y1+dy))
    return box, contrast.to_report()


class OfflineLensApertureEngine:
    """Pinned local lens head; weights are verified against the download receipt."""

    def __init__(self, weights_folder: Path, *, runtime_dir: Path | None = None):
        if not isinstance(weights_folder, Path):
            raise TypeError('weights_folder must be an explicit local pathlib.Path')
        self.folder = weights_folder.resolve(strict=True)
        receipt_path = self.folder/'download-receipt.json'
        receipt = json.loads(receipt_path.read_bytes())
        artifacts = {entry['path']: entry for entry in receipt.get('artifacts', [])}
        if LENS_WEIGHTS not in artifacts or not re.fullmatch(r'[0-9a-f]{64}', str(artifacts[LENS_WEIGHTS].get('sha256'))):
            raise ValueError('Download receipt must pin the lens segmentation weights')
        raw = (self.folder/LENS_WEIGHTS).read_bytes()
        if _sha(raw) != artifacts[LENS_WEIGHTS]['sha256']:
            raise ValueError('Lens segmentation weights differ from their pinned receipt')
        self._weights = raw
        self._description = {'kind': 'OfflineLensApertureEngine', 'method': METHOD,
                             'weights': {'path': str(self.folder/LENS_WEIGHTS), 'sha256': artifacts[LENS_WEIGHTS]['sha256'],
                                         'bytes': len(raw)},
                             'receipt_sha256': _sha(receipt_path.read_bytes()),
                             'source_commit': receipt.get('source_commit'), 'release': receipt.get('release'),
                             'policy': POLICY}
        self.runtime_dir = runtime_dir
        self._model = None

    def describe(self):
        return json.loads(json.dumps(self._description, allow_nan=False))

    def _load(self):
        if self._model is None:
            import torch
            from torchvision.models.segmentation import lraspp_mobilenet_v3_large
            torch.set_num_threads(POLICY['torch_threads'])
            torch.use_deterministic_algorithms(True)
            model = lraspp_mobilenet_v3_large(weights=None, weights_backbone=None, num_classes=1)
            state = torch.load(io.BytesIO(self._weights), map_location='cpu', weights_only=True)
            model.load_state_dict(state, strict=True); model.eval()
            self._model = model
        return self._model

    def propose(self, rgb):
        """Return native lens aperture unions per crop policy for one RGB photo."""
        pixels = np.asarray(rgb)
        if pixels.ndim != 3 or pixels.shape[2] != 3 or pixels.dtype != np.uint8 or pixels.shape[0]*pixels.shape[1] > MAXIMUM_NATIVE_PIXELS:
            raise ValueError('propose requires an 8-bit RGB array within the native pixel capacity')
        import torch
        from torchvision.transforms.v2.functional import normalize, to_dtype, to_image
        image = Image.fromarray(pixels, 'RGB')
        width, height = image.size
        crop, contrast = contrast_crop_box(image)
        runtime = Path(self.runtime_dir) if self.runtime_dir is not None else self.folder/'.runtime'
        runtime.mkdir(parents=True, exist_ok=True)
        variants = {}
        with _offline(runtime):
            model = self._load()
            for variant, box in (('full', (0, 0, width, height)), ('contrast_crop', crop)):
                cropped = image.crop(box)
                x = normalize(to_dtype(to_image(cropped.resize((256, 256))), torch.float32, scale=True), MEAN, STD)
                with torch.inference_mode():
                    logits = model(x[None])['out'][0, 0].cpu().numpy()
                if logits.shape != (256, 256) or not np.isfinite(logits).all():
                    raise ValueError('Invalid lens segmentation logits')
                native = native_logits(logits, box, (width, height))
                known = np.zeros((height, width), bool); known[box[1]:box[3], box[0]:box[2]] = True
                mask = np.zeros((height, width), bool); mask[known] = native[known] > 0
                variants[variant] = {'crop_xyxy_exclusive': list(box), 'mask': mask, 'known_domain': known,
                                     'grid_positive_pixels': int(np.count_nonzero(logits > 0)),
                                     'native_positive_pixels': int(mask.sum()),
                                     'logit_range': [float(logits.min()), float(logits.max())]}
        return {'method': METHOD, 'size_xy': [width, height], 'contrast': contrast, 'variants': variants,
                'semantic_identity': 'unverified_photo_proposal'}
