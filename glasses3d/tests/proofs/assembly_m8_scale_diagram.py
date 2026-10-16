"""Plot selected-tip X scaling from the native snapshots of an existing proof.

Run with a host Python containing matplotlib: ``this.py SCALE_PROOF_DIRECTORY``.
Inputs are read-only. This writes a separate measured diagram and its coordinate
evidence; it does not edit models, render photographs or imply owner acceptance.
"""
from pathlib import Path
import argparse
import hashlib
import json

import numpy as np


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def section_width(vertices, polygons, z):
    """Intersect actual polygon edges with a Z plane; report its full X extent."""
    points = []
    for polygon in polygons:
        for a, b in zip(polygon, polygon[1:] + polygon[:1]):
            va, vb = vertices[a], vertices[b]
            if va[2] == z:
                points.append(float(va[0]))
            if (va[2] - z) * (vb[2] - z) < 0:
                t = (z - va[2]) / (vb[2] - va[2])
                points.append(float(va[0] + t * (vb[0] - va[0])))
    if not points:
        raise ValueError('chosen section does not intersect the saved mesh')
    return {'z_mm': z, 'minimum_x_mm': min(points), 'maximum_x_mm': max(points),
            'width_x_mm': max(points) - min(points)}


def build(root, section_z=-130.):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection, LineCollection

    root = Path(root).resolve()
    output = root / 'isolated-tip-scale-before-preview-after.png'
    data_path = root / 'tip-scale-diagram.json'
    if output.exists() or data_path.exists():
        raise ValueError('choose a proof directory without an existing scale diagram')
    proof_path = root.parent / 'proof.json'
    proof = json.loads(proof_path.read_text())
    example = next(row for row in proof['examples'] if row['kind'] == 'scale_region')
    selected_path = root / 'selection.json'
    selected = json.loads(selected_path.read_text())
    object_id = selected['scope']['object_id']
    paths = {'before': root / 'work/model_maps/baseline/snapshot.json',
             'preview': root / 'preview/work/model_maps/after/snapshot.json',
             'after': root / 'after/work/model_maps/after/snapshot.json'}
    blend_paths = [root / name / 'model-copy.blend' for name in ('preview', 'after')]
    inputs = [proof_path, selected_path, *paths.values(), *blend_paths]
    hashes = {str(path): sha(path) for path in inputs}
    rows = {}
    for name, path in paths.items():
        state = json.loads(path.read_text())
        obj = next(row for row in state['objects'] if row['id'] == object_id)
        vertices = np.asarray(obj['vertices_world_mm'], dtype=float)
        rows[name] = {'snapshot': str(path), 'object': obj['name'], 'vertices_world_mm': vertices.tolist(),
                      'polygons': obj['polygons'], 'triangles': obj['triangles'],
                      'section': section_width(vertices, obj['polygons'], section_z)}
    operations = {row['name']: row['operations'][0] for row in example['variants']}
    anchor = np.asarray(operations['after']['params']['anchor_mm'], dtype=float)
    factors = np.asarray(operations['after']['params']['factors'], dtype=float)
    if not np.array_equal(factors[1:], [1., 1.]):
        raise ValueError('this diagram is for X-only scaling')
    weights = {row['vertex_id']: row['weight'] for row in selected['scope']['vertex_weights']}
    source = np.asarray(rows['before']['vertices_world_mm'])
    for name in ('preview', 'after'):
        vertices = np.asarray(rows[name]['vertices_world_mm'])
        if (rows[name]['polygons'] != rows['before']['polygons'] or
                not np.array_equal(source[:, 1:], vertices[:, 1:])):
            raise ValueError('saved proof is not an X-only edit with unchanged topology')
        rows[name]['maximum_local_x_scale'] = 1 + operations[name]['params']['strength'] * (factors[0] - 1)
    rows['before']['maximum_local_x_scale'] = 1.

    # Show the actual triangle projection in X/Z, including the zero-weight
    # transition. All panels use identical limits and one-to-one mm aspect.
    selected_ids = sorted(weights)
    plotted = np.concatenate([np.asarray(row['vertices_world_mm'])[selected_ids] for row in rows.values()])
    xlim = (float(plotted[:, 0].min()) - 2.3, float(plotted[:, 0].max()) + 2.3)
    depth = (float(-plotted[:, 2].max()) - 4.0, float(-plotted[:, 2].min()) + 4.0)
    fig, axes = plt.subplots(1, 3, figsize=(12.4, 9.2), sharex=True, sharey=True)
    fig.suptitle('Selected arm-tip scale — measured top-view diagram\nActual saved mesh coordinates; all panels share the same physical scale', fontsize=15)
    for ax, (name, row) in zip(axes, rows.items()):
        vertices = np.asarray(row['vertices_world_mm'])
        triangles = np.asarray(row['triangles'], dtype=int)
        triangles = triangles[np.all(vertices[triangles, 2] <= anchor[2] + .001, axis=1)]
        projection = vertices[:, [0, 2]] * [1, -1]
        ax.add_collection(PolyCollection(projection[triangles], facecolors='#63a8cc', edgecolors='none', alpha=.45))
        edges = sorted({tuple(sorted((a, b))) for tri in triangles for a, b in zip(tri, np.roll(tri, -1))})
        ax.add_collection(LineCollection([projection[list(edge)] for edge in edges], colors='#224a64', linewidths=.25, alpha=.18))
        ax.axvline(anchor[0], color='#555', linestyle=':', linewidth=1)
        ax.scatter([anchor[0]], [-anchor[2]], marker='+', color='black', s=80, zorder=5)
        ax.annotate('fixed anchor', (anchor[0], -anchor[2]), xytext=(-56, 13), textcoords='offset points', fontsize=9)
        section = row['section']
        left, right = section['minimum_x_mm'], section['maximum_x_mm']
        y = -section_z
        ax.plot([left, right], [y, y], color='#b33f26', linewidth=2)
        ax.annotate('', (left, y + 3.4), (right, y + 3.4), arrowprops={'arrowstyle': '<->', 'color': '#b33f26', 'lw': 1.5})
        ax.text((left + right) / 2, y + 5.8, f"X width {section['width_x_mm']:.3f} mm", color='#8e2e19', ha='center', fontsize=10)
        change = (row['maximum_local_x_scale'] - 1) * 100
        amount = 'original' if name == 'before' else f'+{change:.0f}% maximum local X scale'
        ax.set_title(name.upper() + '\n' + amount, fontsize=11)
        ax.set_xlim(xlim); ax.set_ylim(depth[::-1]); ax.set_aspect('equal', adjustable='box')
        ax.set_xlabel('Asset X (mm)'); ax.grid(alpha=.15)
    axes[0].set_ylabel('Distance behind origin, -Z (mm)')
    preview_percent = (rows['preview']['maximum_local_x_scale'] - 1) * 100
    after_percent = (rows['after']['maximum_local_x_scale'] - 1) * 100
    fig.text(.5, .065,
             f'Fixed anchor: X={anchor[0]:.3f}, Y={anchor[1]:.3f}, Z={anchor[2]:.3f} mm. '
             f'Width measured at Z={section_z:g} mm.\n'
             f'Selected weights taper to zero at the join; {preview_percent:+g}% and {after_percent:+g}% apply where weight is one.\n'
             'Y, Z and topology stay unchanged. This coordinate diagram accompanies the photo-first AR gallery; owner confirmation is pending.',
             ha='center', fontsize=10)
    fig.subplots_adjust(top=.88, bottom=.16, left=.07, right=.98, wspace=.16)
    fig.savefig(output, dpi=170, facecolor='white'); plt.close(fig)
    if hashes != {str(path): sha(path) for path in inputs}:
        raise ValueError('an input changed during diagram generation')
    data = {'read_only': True, 'cost_usd': 0, 'api_calls': 0, 'owner_confirmation': False,
            'source_unchanged': True, 'input_sha256': hashes, 'image': str(output),
            'anchor_mm': anchor.tolist(), 'section_z_mm': section_z,
            'selection_weights': weights, 'same_axis_limits_mm': {'x': xlim, 'minus_z': depth}, 'variants': rows}
    data_path.write_text(json.dumps(data, indent=2))
    print(json.dumps({'image': str(output), 'evidence': str(data_path),
                      'widths_mm': {name: row['section']['width_x_mm'] for name, row in rows.items()},
                      'source_unchanged': True}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--section-z', type=float, default=-130.)
    args = parser.parse_args()
    build(args.root, args.section_z)
