"""Windows CurrentUser DPAPI credentials; no plaintext credential files."""
import argparse
import ctypes
import getpass
import os
from pathlib import Path
import re
import sys


def credential_path(name):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', name):
        raise ValueError('Invalid credential name')
    return Path.home() / '.codex' / 'credentials' / 'long-video-subtitles' / (name + '.dpapi')


def dpapi(data, decrypt=False):
    if os.name != 'nt':
        raise ValueError('DPAPI is Windows-only; use the provider environment variable instead')
    class Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint32), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    backing = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = Blob(len(data), backing)
    output = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        func = crypt.CryptUnprotectData
        func.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob)]
        description = None
    else:
        func = crypt.CryptProtectData
        func.argtypes = [ctypes.POINTER(Blob), ctypes.c_wchar_p, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(Blob)]
        description = 'long-video-subtitles ASR credential'
    func.restype = ctypes.c_int
    # CRYPTPROTECT_UI_FORBIDDEN, CurrentUser scope (no LOCAL_MACHINE flag).
    if not func(ctypes.byref(source), description, None, None, None, 1, ctypes.byref(output)):
        raise ValueError('Windows credential encryption/decryption failed')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.memset(output.data, 0, output.size)
        kernel.LocalFree(output.data)
        ctypes.memset(backing, 0, len(data))


def read_key(config):
    value = os.environ.get(config['api_key_env'], '').strip()
    if not value:
        path = credential_path(config['credential_name'])
        if not path.exists():
            raise ValueError('Credential missing: configure environment variable or encrypted local credential')
        value = dpapi(path.read_bytes(), decrypt=True).decode('utf-8').strip()
    if not value or any(c.isspace() for c in value):
        raise ValueError('Credential is empty or contains whitespace')
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('name')
    parser.add_argument('--stdin', action='store_true', help='Read one line from a private pipe, not a shell argument')
    parser.add_argument('--replace', action='store_true', help='Explicitly rotate this credential')
    args = parser.parse_args()
    try:
        path = credential_path(args.name)
        if path.exists() and not args.replace:
            raise ValueError('Credential exists; use --replace only when rotating it')
        value = sys.stdin.readline().strip() if args.stdin else getpass.getpass('API key: ').strip()
        if not value or any(c.isspace() for c in value):
            raise ValueError('Credential is empty or contains whitespace')
        encrypted = dpapi(value.encode('utf-8'))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('wb' if args.replace else 'xb') as stream:
            stream.write(encrypted)
        print('Encrypted credential saved for the current Windows user. Secret not displayed.')
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
