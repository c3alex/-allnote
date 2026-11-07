"""Run on the target OS using the application's virtual environment."""
import os
import subprocess
import sys
from pathlib import Path
root = Path(__file__).resolve().parent
os.environ.setdefault('PYINSTALLER_CONFIG_DIR', str(root.parent/'build'/'pyinstaller-cache'))
args = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--windowed',
        '--name', 'Callnote', '--collect-all', 'faster_whisper',
        '--collect-all', 'ctranslate2', '--collect-all', 'tokenizers',
        '--collect-all', 'sounddevice', '--collect-all', 'onnxruntime', str(root/'app.py')]
subprocess.run(args, cwd=root.parent, check=True)
if sys.platform == 'darwin':
    import plistlib
    path = root.parent/'dist/Callnote.app/Contents/Info.plist'
    data = plistlib.loads(path.read_bytes())
    data['NSMicrophoneUsageDescription'] = 'Callnote записывает микрофон для локальной расшифровки.'
    path.write_bytes(plistlib.dumps(data))
    subprocess.run(['codesign', '--force', '--deep', '--sign', '-', str(root.parent/'dist/Callnote.app')],check=True)
