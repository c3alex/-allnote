"""Check packaged GUI starts without immediate failure; does not record audio."""
import os
from pathlib import Path
import subprocess
import sys

if len(sys.argv) != 2:
    raise SystemExit('Usage: python desktop/check_package.py PATH_TO_EXECUTABLE')
executable = Path(sys.argv[1]).resolve()
if not executable.is_file():
    raise SystemExit('Executable not found: ' + str(executable))
# Offscreen only affects this child process; it does not change user settings.
process = subprocess.Popen([str(executable)], cwd=executable.parent,
    env=dict(os.environ, QT_QPA_PLATFORM='offscreen'),
    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
try:
    out, err = process.communicate(timeout=10)
    raise SystemExit(f'Application exited unexpectedly ({process.returncode}):\n{err.decode(errors="replace")}')
except subprocess.TimeoutExpired:
    process.terminate()
    try:
        out, err = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        out, err = process.communicate()
    print('PASS: packaged application started and remained running for 10 seconds.')
    print(err.decode(errors='replace'))
