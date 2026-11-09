"""Build on the target OS. macOS bundles the native audio helper."""
import platform
import os
import subprocess
import sys
from pathlib import Path
root=Path(__file__).resolve().parent
os.environ.setdefault('PYINSTALLER_CONFIG_DIR',str(root.parent/'build'/'pyinstaller-cache'))
args=[sys.executable,'-m','PyInstaller','--noconfirm','--windowed','--name','Callnote',
      '--collect-all','faster_whisper','--collect-all','ctranslate2','--collect-all','tokenizers',
      '--collect-all','onnxruntime','--collect-all','huggingface_hub']
if sys.platform=='darwin':
    helper=root.parent/'build'/'native'/'AudioCapture';helper.parent.mkdir(parents=True,exist_ok=True)
    subprocess.run(['swiftc','-parse-as-library','-O','-target',platform.machine()+'-apple-macos14.4','-module-cache-path',str(root.parent/'build'/'swift-cache'),
                    str(root/'native'/'AudioCapture.swift'),'-o',str(helper)],check=True)
    args+=['--osx-bundle-identifier','com.callnote.desktop','--add-binary',str(helper)+':native']
args.append(str(root/'app.py'))
subprocess.run(args,cwd=root.parent,check=True)
if sys.platform=='darwin':
    import plistlib
    bundle=root.parent/'dist'/'Callnote.app';path=bundle/'Contents'/'Info.plist'
    data=plistlib.loads(path.read_bytes())
    data.update(CFBundleShortVersionString='0.2.0',CFBundleVersion='2',LSMinimumSystemVersion='14.4',
        NSMicrophoneUsageDescription='Callnote записывает ваш голос для локальной расшифровки встречи.',
        NSScreenCaptureUsageDescription='Callnote записывает системный звук звонков и приложений для локальной расшифровки.')
    path.write_bytes(plistlib.dumps(data))
    subprocess.run(['codesign','--force','--deep','--sign','-',str(bundle)],check=True)
    # Treat first rendered frame, not a living process, as successful startup.
    subprocess.run([sys.executable,str(root/'benchmark_startup.py'),str(bundle/'Contents'/'MacOS'/'Callnote'),
                    '--runs','1','--limit','15','--offscreen','--output',str(root.parent/'dist'/'startup-check.json')],check=True)
