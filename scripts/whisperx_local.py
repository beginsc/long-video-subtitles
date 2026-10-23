"""Run the separately installed local WhisperX environment; no server connection."""
import json
from pathlib import Path
import subprocess
import sys

def main():
    config=Path(__file__).resolve().parent.parent/'assets/whisperx-local.json'
    root=Path(json.loads(config.read_text(encoding='utf-8-sig'))['runtime_root'])
    python=root/'venv/Scripts/python.exe'
    runner=root/'run_local.py'
    if not python.is_file() or not runner.is_file():
        print('Local WhisperX runtime missing; inspect assets/whisperx-local.json',file=sys.stderr)
        return 1
    return subprocess.call([str(python),'-B','-X','utf8',str(runner),*sys.argv[1:]])

if __name__=='__main__':sys.exit(main())
