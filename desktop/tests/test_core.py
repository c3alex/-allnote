import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
import wave
import numpy as np
from core import export_text,validate_project,mix_recording,atomic_json
from models import repo_id

class CoreTests(unittest.TestCase):
    def test_text_order_and_midnight(self):
        rows=[dict(start=65,text='Second',speaker='B'),dict(start=1,text='Первый',speaker='А')]
        self.assertEqual(export_text(rows,False,False,True),'Первый Second')
        self.assertIn('2026-11-10 00:00:00',export_text(rows,start=datetime(2026,11,9,23,59,59)))
    def test_reject_invalid_project(self):
        for value in [float('nan'),float('inf'),-1]:
            with self.assertRaises(ValueError):validate_project(dict(version=2,start='2026-11-09T12:00:00',segments=[dict(start=value,end=5,text='x')]))
    def test_repo_validation(self):
        self.assertEqual(repo_id('https://huggingface.co/Systran/faster-whisper-tiny/'),'Systran/faster-whisper-tiny')
        for value in ['https://evil.test/a/b','../../escape','https://huggingface.co/a/b?token=secret','a/b/tree/main']:
            with self.assertRaises(ValueError):repo_id(value)
    def test_tracks_keep_silence_and_channels(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);events=[]
            for name,offset,value in [('microphone',0,1000),('system',0,2000),('microphone',5,3000)]:
                path=folder/f'{name}{offset}.wav'
                with wave.open(str(path),'wb') as out:
                    out.setnchannels(1);out.setsampwidth(2);out.setframerate(16000);out.writeframes(np.full(16000,value,dtype='<i2').tobytes())
                events.append(dict(source=name,start=offset,path=str(path)))
            result=mix_recording(events,folder/'mixed.wav')
            with wave.open(result,'rb') as audio:
                self.assertEqual(audio.getnchannels(),2);data=np.frombuffer(audio.readframes(audio.getnframes()),dtype='<i2').reshape(-1,2)
            self.assertEqual(data.shape,(96000,2));self.assertEqual(data[100].tolist(),[1000,2000]);self.assertEqual(data[20000].tolist(),[0,0]);self.assertEqual(data[85000].tolist(),[3000,0])
    def test_atomic_unicode(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'project.json';atomic_json(path,{'text':'Привет'});self.assertEqual(json.loads(path.read_text())['text'],'Привет');self.assertFalse(path.with_suffix('.json.tmp').exists())

if __name__=='__main__':unittest.main()
