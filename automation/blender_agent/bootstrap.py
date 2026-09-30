"""Register the packaged upstream MCP addon in this Blender process only."""
import importlib.util
import argparse
import json
from pathlib import Path
import sys

import bpy

def initialize_scene(mode, working, receipt_path, port=None):
    """Save the initial editable scene before importing or starting any addon."""
    if mode not in {"empty", "seed"}:
        raise ValueError("Scene mode must be empty or seed")
    working, receipt_path = Path(working).resolve(), Path(receipt_path).resolve()
    if mode == "empty":
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
        scene.unit_settings.system = 'METRIC'
        scene.unit_settings.scale_length = 0.001
        scene.unit_settings.length_unit = 'MILLIMETERS'
        scene['mdl_bridge_underside'] = [0.0, 0.0, 0.0]
        scene['blender_agent_axes'] = '+Y up; +Z front'
    scene = bpy.context.scene
    if port is not None:
        # Persist the dedicated port even when the agent reopens this checkpoint.
        scene['blendermcp_port'] = int(port)
    names = ('objects', 'meshes', 'materials', 'curves', 'metaballs', 'cameras', 'lights',
             'armatures', 'lattices', 'node_groups', 'volumes', 'pointclouds', 'grease_pencils')
    counts = {name: len(getattr(bpy.data, name)) for name in names}
    if mode == "empty" and any(counts.values()):
        raise RuntimeError('Factory-empty initialization retained objects or geometry: ' + str(counts))
    result = bpy.ops.wm.save_as_mainfile(filepath=str(working))
    if result != {'FINISHED'} or not working.is_file():
        raise RuntimeError('Blender failed to save its initial checkpoint')
    receipt = {'mode': mode, 'working_copy': str(working), 'checkpoint_saved': True,
               'initial_counts': counts, 'unit_scale': scene.unit_settings.scale_length,
               'length_unit': scene.unit_settings.length_unit,
               'axes': scene.get('blender_agent_axes'),
               'bridge_origin': list(scene['mdl_bridge_underside']) if 'mdl_bridge_underside' in scene else None}
    temporary = receipt_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    temporary.replace(receipt_path)
    print('LIVE_BLENDER_INITIALIZED ' + json.dumps(receipt), flush=True)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--addon', required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--mode', choices=('empty', 'seed'), required=True)
    parser.add_argument('--working', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    initialize_scene(args.mode, args.working, args.receipt, args.port)
    spec = importlib.util.spec_from_file_location('blender_agent_session_addon', args.addon)
    addon = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = addon
    spec.loader.exec_module(addon)
    addon.register()
    bpy.context.scene.blendermcp_port = args.port
    print('LIVE_BLENDER_MCP_READY', args.port, flush=True)


if __name__ == '__main__':
    main()
