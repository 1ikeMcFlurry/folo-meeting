"""Read only the selected CLI application's own Windows credential.

No user access/refresh tokens are read. No credential is printed or written.
Storage format: official larksuite/cli internal/keychain at commit
7a6a4dbdf510689dd00e841ae79f6138e7f70cbe (MIT).
"""
import base64
import ctypes
from ctypes import wintypes
import winreg


def app_secret(profile):
    reference=profile.get("appSecret")
    if isinstance(reference,str): return reference
    account="appsecret:"+profile.get("appId","")
    if not isinstance(reference,dict) or reference.get("source")!="keychain" or reference.get("id")!=account:
        raise RuntimeError("The configured application secret is not a supported Windows reference")
    name=base64.urlsafe_b64encode(account.encode()).decode().rstrip("=")
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r"Software\LarkCli\keychain\lark-cli",0,winreg.KEY_READ) as key:
        encoded,_=winreg.QueryValueEx(key,name)
    cipher=base64.b64decode(encoded,validate=True)
    entropy=("lark-cli\0"+account).encode()
    class Blob(ctypes.Structure):
        _fields_=[("size",wintypes.DWORD),("data",ctypes.POINTER(ctypes.c_ubyte))]
    cipher_buffer=ctypes.create_string_buffer(cipher)
    entropy_buffer=ctypes.create_string_buffer(entropy)
    source=Blob(len(cipher),ctypes.cast(cipher_buffer,ctypes.POINTER(ctypes.c_ubyte)))
    additional=Blob(len(entropy),ctypes.cast(entropy_buffer,ctypes.POINTER(ctypes.c_ubyte)))
    output=Blob()
    api=ctypes.WinDLL("crypt32",use_last_error=True).CryptUnprotectData
    api.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    api.restype=wintypes.BOOL
    if not api(ctypes.byref(source),None,ctypes.byref(additional),None,None,1,ctypes.byref(output)):
        raise RuntimeError("Windows did not unlock the selected application credential")
    try:
        if not output.data or not 1<=output.size<=4096: raise RuntimeError("Application credential size is invalid")
        return ctypes.string_at(output.data,output.size).decode("utf-8")
    finally:
        if output.data:
            ctypes.memset(output.data,0,output.size)
            free=ctypes.WinDLL("kernel32").LocalFree
            free.argtypes=[ctypes.c_void_p]; free.restype=ctypes.c_void_p
            free(output.data)
