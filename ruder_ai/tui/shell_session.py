"""Shell session backends for the RuderAI Studio integrated terminal.

Three interchangeable backends sit behind one small interface:

* :class:`PosixPtySession` — a real PTY (``pty``/``fcntl``/``termios``), so
  full-screen programs work. POSIX only.
* :class:`WindowsConPTYSession` — a real pseudoconsole via the Win32 ConPTY
  API. This is a genuine TTY: ``vim``, ``htop``, ``less`` and Python's REPL all
  behave, and the child emits real VT sequences.
* :class:`WindowsPipeSession` — a fallback for Windows builds without ConPTY
  (pre-1809). Ordinary shell commands work; curses-style programs will complain
  that there is no console.

The module deliberately has no GUI-toolkit dependency, so it is directly
unit-testable, and every backend exposes the same verbs: ``start``, ``send``,
``pump``, ``resize``, ``close`` and ``fileno`` (PTY only).
"""
from __future__ import annotations

import os
import re
import subprocess
import threading

IS_WINDOWS = os.name == "nt"

# Full escape-sequence coverage.  The CSI-only pattern this replaced leaked
# raw ``]0;title`` into the terminal whenever a shell emitted a window-title
# update, because ']' also falls inside the single-character escape range and so
# has to be matched by a dedicated OSC branch *before* the generic one.
_ANSI_ESCAPE = re.compile(
    r"\x1B(?:"
    r"\][^\x07\x1B]*(?:\x07|\x1B\\)"   # OSC ... BEL | ST
    r"|\[[0-?]*[ -/]*[@-~]"            # CSI
    r"|[()#][0-9A-Za-z]"               # charset selection
    r"|[PX^_][^\x1B]*(?:\x1B\\)"       # DCS / SOS / PM / APC ... ST
    r"|[@-Z\\-_]"                      # single-character escapes
    r")"
)


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from *text*."""
    return _ANSI_ESCAPE.sub("", text)


class _BaseSession:
    """Common surface so callers never branch on the backend."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen | None = None
        self.reader_finished = threading.Event()
        self._closed = False

    def start(self, cwd: str, env: dict[str, str]) -> None:  # pragma: no cover
        raise NotImplementedError

    def send(self, data: bytes) -> None:  # pragma: no cover
        raise NotImplementedError

    def pump(self, size: int = 4096) -> bytes:  # pragma: no cover
        raise NotImplementedError

    def resize(self, cols: int, rows: int) -> None:
        """Best-effort window size update; a no-op when unsupported."""

    def fileno(self) -> int | None:
        """Raw fd for a select()-based reader, or None when unavailable."""
        return None

    def close(self) -> None:
        self._closed = True


# ----------------------------------------------------------------------
# POSIX: real PTY
# ----------------------------------------------------------------------

if not IS_WINDOWS:

    import fcntl
    import pty
    import struct
    import termios

    class PosixPtySession(_BaseSession):
        """Real PTY backend. Fully interactive, POSIX only."""

        def __init__(self) -> None:
            super().__init__()
            self.master_fd: int | None = None
            self.slave_fd: int | None = None

        def start(self, cwd: str, env: dict[str, str]) -> None:
            self.master_fd, self.slave_fd = pty.openpty()
            self.resize(24, 80)

            shell = env.get("SHELL") or "/bin/sh"
            if not os.path.exists(shell):
                shell = "/bin/bash"
            if not os.path.exists(shell):
                shell = "/bin/sh"

            self.proc = subprocess.Popen(
                [shell],
                stdin=self.slave_fd,
                stdout=self.slave_fd,
                stderr=self.slave_fd,
                cwd=cwd,
                env=env,
                preexec_fn=os.setsid,
                close_fds=True,
            )
            # Mirror the child's liveness into .proc for the shared interface.
            self._pid = self.proc.pid

        def resize(self, cols: int, rows: int) -> None:
            if self.slave_fd is None:
                return
            try:
                winsize = struct.pack("HHHH", max(1, rows), max(1, cols), 0, 0)
                fcntl.ioctl(self.slave_fd, termios.TIOCSWINSZ, winsize)
            except OSError:
                pass

        def send(self, data: bytes) -> None:
            if self.master_fd is None or self._closed:
                return
            try:
                os.write(self.master_fd, data)
            except OSError:
                pass

        def pump(self, size: int = 4096) -> bytes:
            if self.master_fd is None or self._closed:
                return b""
            try:
                return os.read(self.master_fd, size)
            except OSError:
                return b""

        def fileno(self) -> int | None:
            return self.master_fd

        def close(self) -> None:
            super().close()
            if self.proc is not None and self.proc.poll() is None:
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=2)
                except Exception:
                    try:
                        self.proc.kill()
                    except Exception:
                        pass
            for fd in (self.master_fd, self.slave_fd):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            self.master_fd = self.slave_fd = None


# ----------------------------------------------------------------------
# Windows: ConPTY (real TTY) and the piped fallback
# ----------------------------------------------------------------------

else:  # pragma: no branch - selected by platform

    import ctypes
    from ctypes import wintypes
    import queue as _queue

    # --- Win32 constants / structures -----------------------------------
    PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
    EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    ERROR_BROKEN_PIPE = 109
    STILL_ACTIVE = 259
    WAIT_TIMEOUT = 258
    INFINITE = 0xFFFFFFFF

    class _COORD(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("lpReserved", wintypes.LPWSTR),
            ("lpDesktop", wintypes.LPWSTR),
            ("lpTitle", wintypes.LPWSTR),
            ("dwX", wintypes.DWORD),
            ("dwY", wintypes.DWORD),
            ("dwXSize", wintypes.DWORD),
            ("dwYSize", wintypes.DWORD),
            ("dwXCountChars", wintypes.DWORD),
            ("dwYCountChars", wintypes.DWORD),
            ("dwFillAttribute", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("wShowWindow", wintypes.WORD),
            ("cbReserved2", wintypes.WORD),
            ("lpReserved2", ctypes.c_void_p),
            ("hStdInput", wintypes.HANDLE),
            ("hStdOutput", wintypes.HANDLE),
            ("hStdError", wintypes.HANDLE),
        ]

    class _STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", _STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("hProcess", wintypes.HANDLE),
            ("hThread", wintypes.HANDLE),
            ("dwProcessId", wintypes.DWORD),
            ("dwThreadId", wintypes.DWORD),
        ]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def _bind(name, argtypes, restype):
        fn = getattr(_k32, name)
        fn.argtypes = argtypes
        fn.restype = restype
        return fn

    _H = wintypes.HANDLE
    _CreatePipe = _bind(
        "CreatePipe",
        [ctypes.POINTER(_H), ctypes.POINTER(_H), ctypes.c_void_p, wintypes.DWORD],
        wintypes.BOOL,
    )
    _CreatePseudoConsole = _bind(
        "CreatePseudoConsole",
        [_COORD, _H, _H, wintypes.DWORD, ctypes.POINTER(_H)],
        wintypes.BOOL,
    )
    _ResizePseudoConsole = _bind("ResizePseudoConsole", [_H, _COORD], wintypes.BOOL)
    _ClosePseudoConsole = _bind("ClosePseudoConsole", [_H], wintypes.BOOL)
    _InitAttrList = _bind(
        "InitializeProcThreadAttributeList",
        [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)],
        wintypes.BOOL,
    )
    _UpdateAttr = _bind(
        "UpdateProcThreadAttribute",
        [
            ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p,
            ctypes.c_size_t, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t),
        ],
        wintypes.BOOL,
    )
    _DeleteAttrList = _bind("DeleteProcThreadAttributeList", [ctypes.c_void_p], None)
    _CreateProcessW = _bind(
        "CreateProcessW",
        [
            wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
            wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
            ctypes.POINTER(_STARTUPINFOEXW), ctypes.POINTER(_PROCESS_INFORMATION),
        ],
        wintypes.BOOL,
    )
    _ReadFile = _bind(
        "ReadFile", [_H, ctypes.c_void_p, wintypes.DWORD,
                     ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p], wintypes.BOOL,
    )
    _WriteFile = _bind(
        "WriteFile", [_H, ctypes.c_void_p, wintypes.DWORD,
                      ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p], wintypes.BOOL,
    )
    _CloseHandle = _bind("CloseHandle", [_H], wintypes.BOOL)
    _TerminateProcess = _bind("TerminateProcess", [_H, wintypes.UINT], wintypes.BOOL)
    _GetExitCode = _bind(
        "GetExitCodeProcess", [_H, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL
    )
    _WaitForSingleObject = _bind("WaitForSingleObject", [_H, wintypes.DWORD], wintypes.DWORD)

    def conpty_available() -> bool:
        """True when this Windows build exposes the pseudoconsole API."""
        try:
            return all(
                hasattr(_k32, fn)
                for fn in ("CreatePseudoConsole", "ResizePseudoConsole", "ClosePseudoConsole")
            )
        except Exception:
            return False

    class ConPTYUnavailable(OSError):
        """Raised when a pseudoconsole cannot be created in this environment.

        Containers, session-0 services and some CI runners have the API but no
        console host to attach to. Callers fall back to piped stdio.
        """

    def _winerror() -> int:
        return ctypes.get_last_error()

    def _env_block(env: dict[str, str]) -> ctypes.Array:
        # A CreateProcess environment block is NUL-separated KEY=VALUE pairs
        # terminated by an extra NUL. Keys containing '=' are malformed and are
        # dropped rather than silently corrupting the block.
        parts = [f"{k}={v}" for k, v in env.items() if k and "=" not in k]
        return ctypes.create_unicode_buffer("\0".join(parts) + "\0\0")

    class WindowsConPTYSession(_BaseSession):
        """Real pseudoconsole backend (Windows 10 1809+).

        ``CreatePseudoConsole`` + ``PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE`` gives
        the child a genuine console, which is what makes ``vim``/``htop``/REPL
        work instead of erroring with "not a console".
        """

        def __init__(self) -> None:
            super().__init__()
            self._hpc: _H | None = None
            self._in_write: _H | None = None
            self._out_read: _H | None = None
            self._pi: _PROCESS_INFORMATION | None = None

        def start(self, cwd: str, env: dict[str, str], cols: int = 120, rows: int = 30) -> None:
            if not conpty_available():
                raise OSError("ConPTY is not available on this Windows version")

            in_read, in_write = _H(), _H()
            out_read, out_write = _H(), _H()
            if not _CreatePipe(ctypes.byref(in_read), ctypes.byref(in_write), None, 0):
                raise ctypes.WinError(_winerror())
            if not _CreatePipe(ctypes.byref(out_read), ctypes.byref(out_write), None, 0):
                err = _winerror()  # capture before cleanup clobbers it
                _CloseHandle(in_read)
                _CloseHandle(in_write)
                raise ctypes.WinError(err)

            hpc = _H()
            if not _CreatePseudoConsole(
                _COORD(max(1, cols), max(1, rows)), in_read, out_write, 0, ctypes.byref(hpc)
            ):
                err = _winerror()  # capture before cleanup clobbers it
                _CloseHandle(in_read)
                _CloseHandle(in_write)
                _CloseHandle(out_read)
                _CloseHandle(out_write)
                raise ConPTYUnavailable(ctypes.WinError(err))
            # The pseudoconsole owns its ends now; the parent must drop them so
            # EOF propagates when the session ends.
            _CloseHandle(in_read)
            _CloseHandle(out_write)
            self._hpc, self._in_write, self._out_read = hpc, in_write, out_read

            size = ctypes.c_size_t(0)
            _InitAttrList(None, 1, 0, ctypes.byref(size))  # expected to fail; sizes it
            attr_buf = ctypes.create_string_buffer(size.value)
            if not _InitAttrList(attr_buf, 1, 0, ctypes.byref(size)):
                raise ctypes.WinError(_winerror())
            try:
                ok = _UpdateAttr(
                    attr_buf, 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE,
                    ctypes.cast(hpc, ctypes.c_void_p), ctypes.sizeof(_H), None, None,
                )
                if not ok:
                    raise ctypes.WinError(_winerror())

                si = _STARTUPINFOEXW()
                si.StartupInfo.cb = ctypes.sizeof(si)
                si.lpAttributeList = ctypes.cast(attr_buf, ctypes.c_void_p)
                pi = _PROCESS_INFORMATION()

                shell = env.get("COMSPEC") or env.get("SHELL") or "cmd.exe"
                cmd = ctypes.create_unicode_buffer(f'"{shell}"')
                # c_void_p argtype will not auto-convert an Array, so cast the
                # environment block explicitly or CreateProcessW raises
                # ArgumentError instead of being called.
                env_buf = _env_block(env)
                created = _CreateProcessW(
                    None, cmd, None, None, False, EXTENDED_STARTUPINFO_PRESENT,
                    ctypes.cast(env_buf, ctypes.c_void_p), cwd,
                    ctypes.byref(si), ctypes.byref(pi),
                )
                if not created:
                    raise ctypes.WinError(_winerror())
            finally:
                _DeleteAttrList(attr_buf)

            self._pi = pi
            self.proc = _ProcHandleProxy(pi)

        def resize(self, cols: int, rows: int) -> None:
            if self._hpc is not None:
                _ResizePseudoConsole(self._hpc, _COORD(max(1, cols), max(1, rows)))

        def send(self, data: bytes) -> None:
            if self._in_write is None or self._closed or not data:
                return
            buf = ctypes.create_string_buffer(data, len(data))
            written = wintypes.DWORD(0)
            if not _WriteFile(self._in_write, buf, len(data), ctypes.byref(written), None):
                if _winerror() != ERROR_BROKEN_PIPE:
                    pass  # child gone; drop the keystroke

        def pump(self, size: int = 4096) -> bytes:
            if self._out_read is None or self._closed:
                return b""
            buf = ctypes.create_string_buffer(size)
            read = wintypes.DWORD(0)
            if not _ReadFile(self._out_read, buf, size, ctypes.byref(read), None):
                return b""
            if read.value == 0:
                return b""
            return buf.raw[: read.value]

        def close(self) -> None:
            super().close()
            # ClosePseudoConsole first so a reader blocked in ReadFile sees EOF.
            if self._hpc is not None:
                _ClosePseudoConsole(self._hpc)
                self._hpc = None
            if self._pi is not None:
                if self._pi.hProcess:
                    # WAIT_OBJECT_0 (0) means already exited; only terminate a
                    # shell that is still running.
                    if _WaitForSingleObject(self._pi.hProcess, 0) == WAIT_TIMEOUT and self.proc:
                        try:
                            self.proc.terminate()
                        except Exception:
                            pass
                    _CloseHandle(self._pi.hThread)
                    _CloseHandle(self._pi.hProcess)
                self._pi = None
            for handle in (self._in_write, self._out_read):
                if handle:
                    _CloseHandle(handle)
            self._in_write = self._out_read = None

    class _ProcHandleProxy:
        """Minimal Popen-like facade so callers can use .proc uniformly."""

        def __init__(self, pi):
            self._pi = pi
            self.pid = pi.dwProcessId

        def poll(self) -> int | None:
            code = wintypes.DWORD(0)
            if not _GetExitCode(self._pi.hProcess, ctypes.byref(code)):
                return None
            return None if code.value == STILL_ACTIVE else code.value

        def terminate(self) -> None:
            _TerminateProcess(self._pi.hProcess, 0)

        def kill(self) -> None:
            _TerminateProcess(self._pi.hProcess, 1)

        def wait(self, timeout: float | None = None) -> int:
            ms = INFINITE if timeout is None else int(timeout * 1000)
            _WaitForSingleObject(self._pi.hProcess, ms)
            return self.poll() or 0

    class WindowsPipeSession(_BaseSession):
        """Piped-stdio fallback for Windows builds without ConPTY."""

        def __init__(self) -> None:
            super().__init__()
            self._handle = None

        def start(self, cwd: str, env: dict[str, str]) -> None:
            shell = env.get("COMSPEC") or env.get("SHELL") or "cmd.exe"
            if not os.path.exists(shell):
                shell = "cmd.exe"
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            self.proc = subprocess.Popen(
                [shell],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                env=env,
                shell=False,
                bufsize=0,
                creationflags=creationflags,
            )
            self._handle = self.proc.stdout.fileno()

        def send(self, data: bytes) -> None:
            if self.proc is None or self._closed or self.proc.stdin is None:
                return
            try:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
            except (OSError, ValueError):
                pass

        def pump(self, size: int = 4096) -> bytes:
            if self.proc is None or self.proc.stdout is None or self._closed:
                return b""
            try:
                return os.read(self.proc.stdout.fileno(), size) or b""
            except (OSError, ValueError):
                return b""

        def close(self) -> None:
            super().close()
            if self.proc is not None and self.proc.poll() is None:
                try:
                    self.proc.terminate()
                    self.proc.wait(timeout=2)
                except Exception:
                    try:
                        self.proc.kill()
                    except Exception:
                        pass
            if self.proc is not None:
                for stream in (self.proc.stdin, self.proc.stdout):
                    try:
                        if stream is not None:
                            stream.close()
                    except OSError:
                        pass


    class PipeReaderThread(threading.Thread):
        """Drains any non-fileno backend into a queue for the UI thread."""

        def __init__(self, session, sink: "_queue.Queue"):
            super().__init__(daemon=True)
            self.session = session
            self.sink = sink

        def run(self) -> None:
            try:
                while not self.session._closed:
                    try:
                        data = self.session.pump()
                    except Exception:
                        break
                    if not data:
                        break  # EOF: the shell exited
                    self.sink.put(data)
                self.sink.put(b"")
            finally:
                self.session.reader_finished.set()


def make_shell(platform: str | None = None, prefer_conpty: bool = True) -> _BaseSession:
    """Return the best available backend for the current platform.

    ``conpty_available()`` only proves the API is *present*. Some hosts (a
    non-interactive session, a container, CI runners) expose ConPTY but refuse
    to allocate a console host — ``CreatePseudoConsole`` then fails with
    ACCESS_DENIED. :class:`ConPTYUnavailable` is raised in that case so the
    caller can retry with piped stdio; see :func:`start_shell`.
    """
    plat = platform or ("windows" if IS_WINDOWS else "posix")
    if plat == "posix":
        return PosixPtySession()
    if prefer_conpty and conpty_available():
        return WindowsConPTYSession()
    return WindowsPipeSession()


def start_shell(cwd: str, env: dict[str, str], prefer_conpty: bool = True) -> tuple[_BaseSession, str | None]:
    """Start the best available backend, falling back to pipes on Windows.

    Returns ``(session, error)``. ``error`` is a human-readable note about a
    failed ConPTY attempt, so the UI can say why it degraded instead of
    silently giving the user a less capable terminal.
    """
    session = make_shell(prefer_conpty=prefer_conpty)
    try:
        session.start(cwd=cwd, env=env)
        return session, None
    except Exception as exc:
        # Only Windows has a fallback backend; on POSIX a PTY failure is real.
        if not IS_WINDOWS or not isinstance(session, WindowsConPTYSession):
            raise
        fallback = WindowsPipeSession()
        fallback.start(cwd=cwd, env=env)
        reason = (
            f"ConPTY 사용 불가 ({exc})" if isinstance(exc, ConPTYUnavailable)
            else f"ConPTY 초기화 실패 ({exc})"
        )
        return fallback, f"{reason} — 파이프 셸로 대체됨"
