"""Only this application's settings and local files are read."""
from dataclasses import dataclass
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'modeling-auto-20260914-bytearray-v7'

def local_keys():
    values = {}
    path = ROOT / '.env'
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                if key.strip() in {'OPENAI_API_KEY', 'MESHY_API_KEY'}:
                    values[key.strip()] = value.strip().strip('"').strip("'")
    # API setup persists here. A stale inherited key must not silently replace
    # the owner's saved correction on the next service restart.
    return {key: values.get(key, os.environ.get(key, '')).strip()
            for key in ('OPENAI_API_KEY', 'MESHY_API_KEY')}

def find_blender():
    candidates = [os.environ.get('MODELING_AUTO_BLENDER'), shutil.which('blender'),
                  r'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe']
    return next((str(Path(p).resolve()) for p in candidates if p and Path(p).is_file()), None)

@dataclass
class Settings:
    data_dir: Path = ROOT / 'data'
    openai_key: str = ''
    meshy_key: str = ''
    blender_path: str | None = None
    timeout: int = 1200
    resolution: int = 768
    samples: int = 24
    @classmethod
    def load(cls):
        keys = local_keys()
        return cls(openai_key=keys['OPENAI_API_KEY'], meshy_key=keys['MESHY_API_KEY'], blender_path=find_blender())
