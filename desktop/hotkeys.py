"""macOS Carbon global hotkeys: no keyboard event logging or monitoring."""
import ctypes as C
import sys
from PySide6.QtCore import QObject,Signal,Qt
from PySide6.QtGui import QKeySequence,QShortcut

DEFAULTS={'record':'Ctrl+Alt+R','copy':'Ctrl+Alt+C','floating':'Ctrl+Alt+M'}
# Keep a restricted, explicit letter mapping for predictable registrations.
KEYCODES={'A':0,'S':1,'D':2,'F':3,'H':4,'G':5,'Z':6,'X':7,'C':8,'V':9,'B':11,'Q':12,'W':13,'E':14,'R':15,'Y':16,'T':17,'O':31,'U':32,'I':34,'P':35,'L':37,'J':38,'K':40,'N':45,'M':46}
class EventType(C.Structure):_fields_=[('eventClass',C.c_uint32),('eventKind',C.c_uint32)]
class HotkeyID(C.Structure):_fields_=[('signature',C.c_uint32),('id',C.c_uint32)]
CALLBACK=C.CFUNCTYPE(C.c_int32,C.c_void_p,C.c_void_p,C.c_void_p)

class Hotkeys(QObject):
    activated=Signal(str)
    def __init__(self,parent):
        super().__init__(parent);self.parent_widget=parent;self.refs=[];self.local=[];self.names=[];self.carbon=None;self.handler=C.c_void_p()
        if sys.platform=='darwin':
            self.carbon=C.CDLL('/System/Library/Frameworks/Carbon.framework/Carbon')
            c=self.carbon
            c.GetApplicationEventTarget.restype=C.c_void_p
            c.InstallEventHandler.argtypes=[C.c_void_p,CALLBACK,C.c_uint32,C.POINTER(EventType),C.c_void_p,C.POINTER(C.c_void_p)]
            c.RegisterEventHotKey.argtypes=[C.c_uint32,C.c_uint32,HotkeyID,C.c_void_p,C.c_uint32,C.POINTER(C.c_void_p)]
            c.UnregisterEventHotKey.argtypes=[C.c_void_p]
            c.GetEventParameter.argtypes=[C.c_void_p,C.c_uint32,C.c_uint32,C.c_void_p,C.c_uint32,C.c_void_p,C.c_void_p]
            c.RemoveEventHandler.argtypes=[C.c_void_p]
            self.callback=CALLBACK(self._event)
            event=EventType(int.from_bytes(b'keyb','big'),6)
            result=c.InstallEventHandler(c.GetApplicationEventTarget(),self.callback,1,C.byref(event),None,C.byref(self.handler))
            if result:raise RuntimeError('Не удалось установить обработчик горячих клавиш')
    def _event(self,handler,event,data):
        ident=HotkeyID()
        result=self.carbon.GetEventParameter(event,int.from_bytes(b'----','big'),int.from_bytes(b'hkid','big'),None,C.sizeof(ident),None,C.byref(ident))
        if not result and ident.signature==int.from_bytes(b'CLNT','big') and ident.id<len(self.names):self.activated.emit(self.names[ident.id]);return 0
        return -9874
    def clear(self):
        for ref in self.refs:self.carbon.UnregisterEventHotKey(ref)
        self.refs=[]
        for shortcut in self.local:shortcut.setEnabled(False);shortcut.deleteLater()
        self.local=[]
    def register(self,mapping):
        parsed=[]
        for name,text in mapping.items():
            seq=QKeySequence(text)
            if seq.count()!=1:raise ValueError('Используйте одно сочетание с буквой A–Z')
            combo=seq[0];key=chr(combo.key()) if 65<=combo.key()<=90 else ''
            mods=combo.keyboardModifiers()
            if key not in KEYCODES or not (mods&(Qt.ControlModifier|Qt.AltModifier|Qt.MetaModifier)):
                raise ValueError('Сочетание должно содержать букву A–Z и ⌘, ⌥ или Ctrl')
            carbon_mods=(256 if mods&Qt.ControlModifier else 0)|(512 if mods&Qt.ShiftModifier else 0)|(2048 if mods&Qt.AltModifier else 0)|(4096 if mods&Qt.MetaModifier else 0)
            parsed.append((name,text,KEYCODES[key],carbon_mods))
        if len({(p[2],p[3]) for p in parsed})!=len(parsed):raise ValueError('Сочетания должны различаться')
        self.clear();self.names=[p[0] for p in parsed]
        for index,(name,text,key,mods) in enumerate(parsed):
            if self.carbon:
                ref=C.c_void_p();code=self.carbon.RegisterEventHotKey(key,mods,HotkeyID(int.from_bytes(b'CLNT','big'),index),self.carbon.GetApplicationEventTarget(),0,C.byref(ref))
                if code:self.clear();raise ValueError('Не удалось зарегистрировать '+QKeySequence(text).toString(QKeySequence.NativeText)+f' (macOS: {code}). Выберите другое сочетание.')
                self.refs.append(ref)
            else:
                shortcut=QShortcut(QKeySequence(text),self.parent_widget);shortcut.activated.connect(lambda n=name:self.activated.emit(n));self.local.append(shortcut)
    def close(self):
        self.clear()
        if self.carbon and self.handler:self.carbon.RemoveEventHandler(self.handler);self.handler=C.c_void_p()
