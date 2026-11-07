"""Build and smoke-test Callnote on Windows x64 with Python 3.11."""
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import venv


def main():
    if sys.platform != 'win32' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise SystemExit('Run this script on Windows x64 with 64-bit Python 3.11.')
    if sys.version_info[:2] != (3, 11):
        raise SystemExit('Python 3.11 is required: py -3.11 desktop/build_windows.py')
    root = Path(__file__).resolve().parent.parent
    environment = root / '.venv-windows'
    python = environment / 'Scripts' / 'python.exe'
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(environment)
    def run(*args):
        subprocess.run([str(python), *args], cwd=root, check=True)
    run('-m', 'pip', 'install', '-r', 'desktop/requirements.txt')
    run('-c', 'import faster_whisper, ctranslate2, onnxruntime, sounddevice; from PySide6 import QtMultimedia, QtWidgets')
    run('desktop/build.py')
    executable = root / 'dist' / 'Callnote' / 'Callnote.exe'
    if not executable.is_file():
        raise SystemExit('Build failed: Callnote.exe was not produced.')
    run('desktop/check_package.py', str(executable))
    with (root/'dist'/'requirements-windows-resolved.txt').open('w', encoding='utf-8') as output:
        subprocess.run([str(python), '-m', 'pip', 'freeze'], stdout=output, check=True)
    archive = shutil.make_archive(str(root/'dist'/'Callnote-Windows-x64'), 'zip', root/'dist', 'Callnote')
    print('Ready:', archive)
    print('Unpack the entire archive, then run Callnote/Callnote.exe.')


if __name__ == '__main__':
    main()
