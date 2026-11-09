import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from model_job import transfer

class Response(io.BytesIO):
    def __init__(self,data,status=200,headers=None):super().__init__(data);self.status=status;self.headers=headers or {}
    def getcode(self):return self.status

class DownloadTests(unittest.TestCase):
    def test_resume_after_interrupted_body(self):
        data=b'abcdef'*100;requests=[]
        def opener(request):
            requests.append(request)
            if len(requests)==1:return Response(data[:150])
            return Response(data[150:],206,{'Content-Range':f'bytes 150-{len(data)-1}/{len(data)}'})
        with tempfile.TemporaryDirectory() as d,patch('model_job.time.sleep'):
            target=Path(d)/'model.bin';transfer('https://example.test/model',target,len(data),hashlib.sha256(data).hexdigest(),opener=opener)
            self.assertEqual(target.read_bytes(),data);self.assertEqual(requests[1].get_header('Range'),'bytes=150-')
    def test_server_ignoring_range_restarts_safely(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'model.bin';target.with_name('model.bin.part').write_bytes(b'abc')
            transfer('https://example.test/model',target,6,opener=lambda request:Response(b'abcdef'))
            self.assertEqual(target.read_bytes(),b'abcdef')
    def test_wrong_hash_never_installs_model(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'model.bin'
            with self.assertRaises(RuntimeError):transfer('https://example.test/model',target,6,'0'*64,opener=lambda request:Response(b'abcdef'),retries=1)
            self.assertFalse(target.exists())
    def test_bad_resume_range_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'model.bin';target.with_name('model.bin.part').write_bytes(b'abc')
            with self.assertRaises(RuntimeError):transfer('https://example.test/model',target,6,opener=lambda request:Response(b'def',206,{'Content-Range':'bytes 1-3/6'}),retries=1)
            self.assertFalse(target.exists())
    def test_completed_files_are_not_downloaded_again(self):
        with tempfile.TemporaryDirectory() as d:
            target=Path(d)/'model.bin';target.write_bytes(b'abcdef')
            def fail(request):raise AssertionError('network should not be called')
            transfer('https://example.test/model',target,6,hashlib.sha256(b'abcdef').hexdigest(),opener=fail)
