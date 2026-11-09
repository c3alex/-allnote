"""Regression: a blocked transfer must never block the Qt event loop."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from PySide6.QtCore import QCoreApplication,QEventLoop,QTimer
from models import ModelTask

class ProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QCoreApplication.instance() or QCoreApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.previous=os.environ.get('CALLNOTE_DATA_DIR');os.environ['CALLNOTE_DATA_DIR']=self.temp.name
    def tearDown(self):
        if self.previous is None:os.environ.pop('CALLNOTE_DATA_DIR',None)
        else:os.environ['CALLNOTE_DATA_DIR']=self.previous
        self.temp.cleanup()
    def blocked_task(self):
        task=ModelTask('inspect','Systran/faster-whisper-tiny');start=task.process.start
        task.process.start=lambda *args:start(sys.executable,['-c','import time; time.sleep(60)'])
        return task
    def test_cancel_keeps_gui_responsive(self):
        task=self.blocked_task();loop=QEventLoop();ticks=[];errors=[]
        heartbeat=QTimer();heartbeat.setInterval(20);heartbeat.timeout.connect(lambda:ticks.append(1));heartbeat.start()
        task.failed.connect(errors.append);task.finished.connect(loop.quit)
        task.start();begin=time.monotonic();QTimer.singleShot(200,task.cancel);timeout=QTimer();timeout.setSingleShot(True);timeout.timeout.connect(loop.quit);timeout.start(3000);loop.exec();timeout.stop();heartbeat.stop()
        self.assertFalse(task.isRunning());self.assertLess(time.monotonic()-begin,2);self.assertGreater(len(ticks),3);self.assertIn('отменена',errors[-1])
    def test_watchdog_terminates_stalled_child(self):
        task=self.blocked_task();loop=QEventLoop();errors=[];task.failed.connect(errors.append);task.finished.connect(loop.quit)
        task.start();task.last_activity=time.monotonic()-40;QTimer.singleShot(100,task.check_timeout)
        timeout=QTimer();timeout.setSingleShot(True);timeout.timeout.connect(loop.quit);timeout.start(3000);loop.exec();timeout.stop()
        self.assertFalse(task.isRunning());self.assertIn('35 секунд',errors[-1])
