"""Contém subprocessos de captura e de render mesmo se o processo pai for encerrado."""
import os
import subprocess
import sys

_JOB_HANDLE = None


def conter_processos():
    """No Windows, o sistema mata os descendentes quando este processo fecha o Job."""
    global _JOB_HANDLE
    if os.name != "nt" or _JOB_HANDLE is not None:
        return
    import ctypes as c
    from ctypes import wintypes as w

    class Limites(c.Structure):
        _fields_ = [
            ("tempo_processo", c.c_longlong), ("tempo_job", c.c_longlong),
            ("flags", w.DWORD), ("mem_min", c.c_size_t), ("mem_max", c.c_size_t),
            ("processos", w.DWORD), ("afinidade", c.c_size_t),
            ("prioridade", w.DWORD), ("classe", w.DWORD),
        ]

    class LimitesEstendidos(c.Structure):
        _fields_ = [
            ("basicos", Limites), ("contadores_io", c.c_ulonglong * 6),
            ("mem_processo", c.c_size_t), ("mem_job", c.c_size_t),
            ("pico_processo", c.c_size_t), ("pico_job", c.c_size_t),
        ]

    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = (c.c_void_p, w.LPCWSTR)
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = (w.HANDLE, c.c_int, c.c_void_p, w.DWORD)
    kernel.SetInformationJobObject.restype = w.BOOL
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.AssignProcessToJobObject.argtypes = (w.HANDLE, w.HANDLE)
    kernel.AssignProcessToJobObject.restype = w.BOOL
    kernel.CloseHandle.argtypes = (w.HANDLE,)
    kernel.CloseHandle.restype = w.BOOL

    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise c.WinError(c.get_last_error())
    limites = LimitesEstendidos()
    limites.basicos.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(job, 9, c.byref(limites), c.sizeof(limites)) or not (
        kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess())
    ):
        erro = c.WinError(c.get_last_error())
        kernel.CloseHandle(job)
        raise erro
    # O handle não é herdável e fica retido em _JOB_HANDLE até o processo sair.
    _JOB_HANDLE = job


if __name__ == "__main__":
    conter_processos()
    # stdin/stdout/stderr precisam ir explícitos: com CREATE_NO_WINDOW o filho ganharia um console novo
    # e o vídeo da live nunca chegaria ao pipe do ffmpeg (captura ficava em "sem vídeo novo").
    sys.exit(subprocess.call(
        sys.argv[1:],
        stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ))
