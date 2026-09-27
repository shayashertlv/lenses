"""Rebuild the local experiment contact sheets; no provider requests."""
from pathlib import Path
import hashlib
import json

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data' / 'part-probes-v1'


def font(size):
    return ImageFont.truetype('C:/Windows/Fonts/arial.ttf', size)


def panel(canvas, path, box):
    image = Image.open(path).convert('RGB')
    contained = ImageOps.contain(image, (box[2] - box[0], box[3] - box[1]))
    x = box[0] + (box[2] - box[0] - contained.width) // 2
    y = box[1] + (box[3] - box[1] - contained.height) // 2
    canvas.paste(contained, (x, y))


def main():
    output = DATA / 'summary'
    output.mkdir(exist_ok=True)
    canvas = Image.new('RGB', (1440, 900), '#eef2f6')
    draw = ImageDraw.Draw(canvas)
    draw.text((24, 18), 'Lens separation: actual experiment results', font=font(29), fill='#17283c')
    draw.text((24, 58), 'Same segmented geometry before / after. Clear materials isolate separation; Oakley coating is not recovered here.',
              font=font(19), fill='#42516a')
    for col, title in enumerate(('Original product photo', 'Opaque provider appearance', 'Selected lenses made transparent')):
        draw.text((24 + col * 480, 103), title, font=font(22), fill='#17283c')
    for row, product in enumerate(('miu', 'oakley')):
        top = 143 + row * 360
        draw.text((24, top), 'Miu: rimless clear lenses' if product == 'miu' else 'Oakley: continuous sport shield',
                  font=font(22), fill='#17283c')
        paths = [ROOT / 'data' / 'provider-comparison-v1' / 'inputs' / product / 'angled.jpg',
                 DATA / 'standard-optics-renders' / f'{product}-segmented__raw__checker__angled.png',
                 DATA / 'standard-optics-renders' / f'{product}-neutral__raw__checker__angled.png']
        for col, path in enumerate(paths):
            panel(canvas, path, (col * 480 + 16, top + 34, col * 480 + 464, top + 344))
    draw.text((24, 870), 'Standard glTF diagnostic renders. Photo and render cameras differ. Frame accuracy and final lens appearance remain unvalidated.',
              font=font(16), fill='#42516a')
    canvas.save(output / 'lens-separation.png')

    # These are matched actual-AR frames, without selective repainting or retouching.
    cases = [('oakley-neutral', 'Normal reflection'), ('oakley-zero-reflection', 'Reflection disabled'),
             ('oakley-rough-reflection', 'Rougher reflection')]
    canvas = Image.new('RGB', (1440, 500), '#eef2f6')
    draw = ImageDraw.Draw(canvas)
    draw.text((24, 20), 'What causes the Oakley white patch?', font=font(29), fill='#17283c')
    draw.text((24, 60), 'Actual AR renderer; identical geometry and frame materials. Only the optical reflection descriptor changes.',
              font=font(19), fill='#42516a')
    rows = []
    for col, (case, title) in enumerate(cases):
        path = DATA / 'reflection-ar-check' / f'{case}__actual-ar__angled.png'
        original = Image.open(path).convert('RGB')
        crop = original.crop((180, 88, 510, 254))
        crop.thumbnail((448, 280))
        crop = crop.resize((448, round(crop.height * 448 / crop.width)), Image.Resampling.NEAREST)
        canvas.paste(crop, (col * 480 + 16, 157))
        draw.text((24 + col * 480, 115), title, font=font(23), fill='#17283c')
        values = np.asarray(original)[128:179, 325:370]
        rows.append({'case': case, 'render': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                     'reviewed_roi_xyxy': [325, 128, 370, 179],
                     'near_white_pixels_rgb_all_at_least245': int(np.all(values >= 245, axis=2).sum())})
    draw.text((24, 415), 'The patch disappears when reflection is disabled. Blackening rear hardware did not remove it.',
              font=font(23), fill='#17283c')
    draw.text((24, 455), 'Zero reflection is a causal control, not the final material for this mirrored product.',
              font=font(20), fill='#42516a')
    canvas.save(output / 'reflection-cause.png')
    (output / 'reflection-roi.json').write_text(json.dumps({'scope': 'Manual diagnostic ROI, not a product acceptance metric',
                                                          'cases': rows}, indent=2) + '\n')
    print(json.dumps({'output': str(output), 'reflection_roi': rows}))


if __name__ == '__main__':
    main()
