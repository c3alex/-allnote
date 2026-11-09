"""Measure process launch -> first window paint. Fails if any run exceeds budget."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

parser=argparse.ArgumentParser()
parser.add_argument('executable',type=Path)
parser.add_argument('--runs',type=int,default=3)
parser.add_argument('--limit',type=float,default=15)
parser.add_argument('--output',type=Path,required=True)
parser.add_argument('--offscreen',action='store_true')
args=parser.parse_args()
results=[]
for index in range(args.runs):
    with tempfile.TemporaryDirectory(prefix='callnote-startup-') as folder:
        folder=Path(folder);report=folder/'ready.json'
        env=dict(os.environ,CALLNOTE_DATA_DIR=str(folder/'profile'),CALLNOTE_STARTUP_REPORT=str(report),CALLNOTE_STARTUP_EXIT='1')
        if args.offscreen:env['QT_QPA_PLATFORM']='offscreen'
        env['CALLNOTE_STARTUP_BEGIN_NS']=str(time.perf_counter_ns())
        try:
            process=subprocess.run([str(args.executable.resolve())],env=env,capture_output=True,text=True,timeout=args.limit+5)
        except subprocess.TimeoutExpired:
            raise SystemExit('FAIL: startup exceeded budget; no responsive first frame')
        if process.returncode or not report.exists():
            raise SystemExit('FAIL: startup crashed or first frame absent\n'+process.stderr[-3000:])
        result=json.loads(report.read_text());result['run']=index+1;results.append(result)
        print(f"Run {index+1}: {result['total_ms']/1000:.3f}s to first frame")
summary={'budget_seconds':args.limit,'mode':'offscreen' if args.offscreen else 'native',
         'fresh_profile_each_run':True,'os_disk_cache_cleared':False,'runs':results,
         'passed':all(r['total_ms']<=args.limit*1000 and not r['heavy_modules'] for r in results)}
args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(summary,indent=2)+'\n')
if not summary['passed']:raise SystemExit('FAIL: startup budget exceeded or heavy inference/media libraries loaded before first frame')
print('PASS: all launches meet the budget; inference and media libraries deferred')
