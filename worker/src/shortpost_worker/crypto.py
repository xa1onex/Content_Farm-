"""User-scoped DPAPI. No plaintext fallback is permitted in production."""
import base64,ctypes,os
from ctypes import wintypes
from pathlib import Path

class Blob(ctypes.Structure):
    _fields_=[('cbData',wintypes.DWORD),('pbData',ctypes.POINTER(ctypes.c_ubyte))]

class DPAPI:
    def __init__(self):
        if os.name!='nt':raise RuntimeError('DPAPI requires Windows; Linux is supported only for tests with an injected cipher')
        self.crypt=ctypes.WinDLL('crypt32',use_last_error=True)
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.crypt.CryptProtectData.argtypes=[ctypes.POINTER(Blob),wintypes.LPCWSTR,ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        self.crypt.CryptUnprotectData.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
        self.crypt.CryptProtectData.restype=wintypes.BOOL
        self.crypt.CryptUnprotectData.restype=wintypes.BOOL
        self.kernel.LocalFree.argtypes=[ctypes.c_void_p];self.kernel.LocalFree.restype=ctypes.c_void_p
    def _call(self,data,protect):
        buffer=ctypes.create_string_buffer(data)
        inp=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));out=Blob()
        if protect:ok=self.crypt.CryptProtectData(ctypes.byref(inp),'ShortPost user secret',None,None,None,1,ctypes.byref(out))
        else:ok=self.crypt.CryptUnprotectData(ctypes.byref(inp),None,None,None,None,1,ctypes.byref(out))
        if not ok:raise OSError(ctypes.get_last_error(),'DPAPI operation failed')
        try:return ctypes.string_at(out.pbData,out.cbData)
        finally:self.kernel.LocalFree(out.pbData)
    def encrypt(self,value):return base64.b64encode(self._call(value.encode(),True)).decode()
    def decrypt(self,value):return self._call(base64.b64decode(value),False).decode()

class TokenStore:
    def __init__(self,path,cipher):self.path=Path(path);self.cipher=cipher
    def save(self,value):
        if len(value)<20:raise ValueError('Worker bearer token is too short')
        temporary=self.path.with_suffix('.tmp');temporary.write_text(self.cipher.encrypt(value));os.replace(temporary,self.path)
    def read(self):
        if not self.path.is_file():raise RuntimeError('Set a worker token using shortpost-worker token set')
        return self.cipher.decrypt(self.path.read_text())
