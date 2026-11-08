"""Keep unattended Windows execution awake while the worker owns its main thread."""
import ctypes
import logging
import sys
from contextlib import contextmanager

CONTINUOUS=0x80000000
SYSTEM_REQUIRED=0x00000001
log=logging.getLogger(__name__)


def windows_setter():
    if sys.platform!='win32':return None
    setter=ctypes.windll.kernel32.SetThreadExecutionState
    setter.argtypes=[ctypes.c_uint32];setter.restype=ctypes.c_uint32
    return setter


@contextmanager
def prevent_idle_sleep(enabled=True):
    setter=windows_setter() if enabled else None
    previous=setter(CONTINUOUS|SYSTEM_REQUIRED) if setter else None
    if setter and not previous:log.warning('idle_sleep_prevention_unavailable')
    try:yield
    finally:
        if setter and previous:setter(CONTINUOUS|previous)
