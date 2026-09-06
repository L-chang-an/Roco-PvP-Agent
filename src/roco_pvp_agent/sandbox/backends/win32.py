"""Small, typed Win32 boundary. Imported only by Windows code; no shell commands.

All handles, attribute buffers and SIDs are owned explicitly. Constants follow the
Windows SDK (processthreadsapi.h, winnt.h). This is also copied into the trusted
standalone runtime so the runner can verify its OS restrictions before execution.
"""

from __future__ import annotations

import ctypes as C
import os
import subprocess
from contextlib import ExitStack, contextmanager
from functools import lru_cache
from pathlib import Path

D = C.c_uint32
B = C.c_int32
P = C.c_void_p
Z = C.c_size_t
W = C.c_wchar_p
Q = C.c_uint64
INVALID_HANDLE = C.c_void_p(-1).value
WRITE_RIGHTS = 0x000D0156  # delete, write DAC/owner/data/EA/attributes, delete child


class SecurityAttributes(C.Structure):
    _fields_ = [("length", D), ("descriptor", P), ("inherit", B)]


class SidAttributes(C.Structure):
    _fields_ = [("sid", P), ("attributes", D)]


class SecurityCapabilities(C.Structure):
    _fields_ = [("sid", P), ("capabilities", C.POINTER(SidAttributes)),
                ("count", D), ("reserved", D)]


class UnicodeString(C.Structure):
    _fields_ = [("length", C.c_uint16), ("maximum", C.c_uint16), ("buffer", P)]


class TokenSecurityAttribute(C.Structure):
    _fields_ = [("name", UnicodeString), ("type", C.c_uint16), ("reserved", C.c_uint16),
                ("flags", D), ("count", D), ("values", C.POINTER(Q))]


class TokenSecurityAttributes(C.Structure):
    _fields_ = [("version", C.c_uint16), ("reserved", C.c_uint16), ("count", D),
                ("attributes", C.POINTER(TokenSecurityAttribute))]


class StartupInfo(C.Structure):
    _fields_ = [("cb", D), ("reserved", W), ("desktop", W), ("title", W),
                ("x", D), ("y", D), ("xsize", D), ("ysize", D),
                ("xchars", D), ("ychars", D), ("fill", D), ("flags", D),
                ("show", C.c_uint16), ("reserved_size", C.c_uint16),
                ("reserved_bytes", P), ("stdin", P), ("stdout", P), ("stderr", P)]


class StartupInfoEx(C.Structure):
    _fields_ = [("startup", StartupInfo), ("attributes", P)]


class ProcessInfo(C.Structure):
    _fields_ = [("process", P), ("thread", P), ("pid", D), ("tid", D)]


class BasicLimits(C.Structure):
    _fields_ = [("process_time", C.c_int64), ("job_time", C.c_int64),
                ("flags", D), ("min_working_set", Z), ("max_working_set", Z),
                ("active_processes", D), ("affinity", Z), ("priority", D),
                ("scheduling", D)]


class IoCounters(C.Structure):
    _fields_ = [(name, Q) for name in ("read_ops", "write_ops", "other_ops",
                                      "read_bytes", "write_bytes", "other_bytes")]


class ExtendedLimits(C.Structure):
    _fields_ = [("basic", BasicLimits), ("io", IoCounters), ("process_memory", Z),
                ("job_memory", Z), ("peak_process_memory", Z), ("peak_job_memory", Z)]


class Accounting(C.Structure):
    _fields_ = [("user_time", C.c_int64), ("kernel_time", C.c_int64),
                ("period_user", C.c_int64), ("period_kernel", C.c_int64),
                ("faults", D), ("total_processes", D), ("active_processes", D),
                ("terminated_processes", D)]


class FileInfo(C.Structure):
    _fields_ = [("attributes", D), ("creation", Q), ("access", Q), ("write", Q),
                ("volume", D), ("size_high", D), ("size_low", D),
                ("links", D), ("index_high", D), ("index_low", D)]
    # FILETIME has DWORD alignment, not ULONGLONG alignment.
    _pack_ = 4


class Win32Error(OSError):
    def __init__(self, operation: str, code: int):
        super().__init__(code, operation)
        self.operation = operation
        self.winerror = code


class Handle:
    def __init__(self, api, value):
        self.api, self.value = api, value

    def close(self):
        if self.value not in (None, INVALID_HANDLE):
            self.api.CloseHandle(self.value)
            self.value = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class Win32:
    def __init__(self):
        if os.name != "nt":
            raise OSError("Windows APIs unavailable")
        kernel = C.WinDLL("kernel32", use_last_error=True)
        advapi = C.WinDLL("advapi32", use_last_error=True)
        userenv = C.WinDLL("userenv", use_last_error=True)
        base = C.WinDLL("kernelbase", use_last_error=True)
        native = C.WinDLL("ntdll", use_last_error=True)
        user = C.WinDLL("user32", use_last_error=True)

        def bind(lib, name, result, *args):
            fn = getattr(lib, name)
            fn.restype, fn.argtypes = result, args
            setattr(self, name, fn)

        bind(kernel, "CloseHandle", B, P)
        bind(kernel, "LocalFree", P, P)
        bind(kernel, "GetCurrentProcess", P)
        bind(kernel, "GetWindowsDirectoryW", D, W, D)
        bind(kernel, "GetSystemDirectoryW", D, W, D)
        bind(user, "CreateDesktopW", P, W, W, P, D, D, P)
        bind(user, "CloseDesktop", B, P)
        bind(kernel, "CreateJobObjectW", P, P, W)
        bind(kernel, "SetInformationJobObject", B, P, C.c_int, P, D)
        bind(kernel, "QueryInformationJobObject", B, P, C.c_int, P, D, P)
        bind(kernel, "TerminateJobObject", B, P, D)
        bind(kernel, "IsProcessInJob", B, P, P, C.POINTER(B))
        bind(kernel, "InitializeProcThreadAttributeList", B, P, D, D, C.POINTER(Z))
        bind(kernel, "UpdateProcThreadAttribute", B, P, D, Z, P, Z, P, P)
        bind(kernel, "DeleteProcThreadAttributeList", None, P)
        bind(advapi, "CreateProcessAsUserW", B, P, W, W, P, P, B, D, P, W, P, P)
        bind(native, "NtCreateLowBoxToken", C.c_int32, C.POINTER(P), P, D, P, P, D, P, D, P)
        bind(kernel, "GetProcessMitigationPolicy", B, P, C.c_int, P, Z)
        bind(kernel, "ResumeThread", D, P)
        bind(kernel, "WaitForSingleObject", D, P, D)
        bind(kernel, "GetExitCodeProcess", B, P, C.POINTER(D))
        bind(kernel, "CreatePipe", B, C.POINTER(P), C.POINTER(P), P, D)
        bind(kernel, "SetHandleInformation", B, P, D, D)
        bind(kernel, "ReadFile", B, P, P, D, C.POINTER(D), P)
        bind(kernel, "WriteFile", B, P, P, D, C.POINTER(D), P)
        bind(kernel, "PeekNamedPipe", B, P, P, D, P, C.POINTER(D), P)
        bind(kernel, "CreateFileW", P, W, D, D, P, D, D, P)
        bind(kernel, "GetFileType", D, P)
        bind(kernel, "GetFileInformationByHandle", B, P, C.POINTER(FileInfo))
        bind(kernel, "GetFinalPathNameByHandleW", D, P, W, D, D)
        bind(advapi, "OpenProcessToken", B, P, D, C.POINTER(P))
        bind(advapi, "GetTokenInformation", B, P, C.c_int, P, D, C.POINTER(D))
        bind(advapi, "ConvertSidToStringSidW", B, P, C.POINTER(P))
        bind(advapi, "ConvertStringSidToSidW", B, W, C.POINTER(P))
        bind(advapi, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
             B, W, D, C.POINTER(P), P)
        bind(advapi, "SetFileSecurityW", B, W, D, P)
        bind(advapi, "FreeSid", P, P)
        bind(userenv, "DeriveAppContainerSidFromAppContainerName", C.c_int32, W, C.POINTER(P))
        bind(base, "DeriveCapabilitySidsFromName", B, W, C.POINTER(P), C.POINTER(D),
             C.POINTER(P), C.POINTER(D))

    @staticmethod
    def check(ok, operation):
        if not ok:
            raise Win32Error(operation, C.get_last_error())

    def handle(self, value, operation):
        self.check(value not in (None, INVALID_HANDLE), operation)
        return Handle(self, value)

    def system_directory(self, *, windows=False):
        buffer = C.create_unicode_buffer(32768)
        fn = self.GetWindowsDirectoryW if windows else self.GetSystemDirectoryW
        length = fn(buffer, len(buffer))
        self.check(0 < length < len(buffer), "system_directory")
        return Path(buffer.value)

    @contextmanager
    def token(self, process=None, *, access=8):
        token = P()
        self.check(self.OpenProcessToken(process or self.GetCurrentProcess(), access,
                                        C.byref(token)), "open_token")
        with self.handle(token.value, "open_token") as owned:
            yield owned.value

    def token_info(self, token, kind):
        size = D()
        self.GetTokenInformation(token, kind, None, 0, C.byref(size))
        self.check(0 < size.value < 65536, "token_info_size")
        buffer = C.create_string_buffer(size.value)
        self.check(self.GetTokenInformation(token, kind, buffer, size, C.byref(size)),
                   "token_info")
        return buffer

    def token_dword(self, token, kind):
        value, size = D(), D()
        self.check(self.GetTokenInformation(token, kind, C.byref(value), C.sizeof(value),
                                            C.byref(size)), "token_dword")
        return value.value

    def is_lpac(self, token):
        # TokenIsLessPrivilegedAppContainer (46) is in the SDK but is not
        # implemented on all target kernels. Check the actual immutable LPAC
        # security attribute instead, without treating unsupported queries as OK.
        buffer = self.token_info(token, 39)  # TokenSecurityAttributes
        header = TokenSecurityAttributes.from_buffer(buffer)
        if header.version != 1 or header.count > 512:
            return False
        for index in range(header.count):
            attribute = header.attributes[index]
            name = C.wstring_at(attribute.name.buffer, attribute.name.length // 2)
            if name == "WIN://NOALLAPPPKG":
                return (attribute.type == 2 and attribute.count == 1
                        and not attribute.flags & 0x10 and attribute.values[0] == 1)
        return False

    def sid_string(self, sid):
        value = P()
        self.check(self.ConvertSidToStringSidW(sid, C.byref(value)), "sid_string")
        try:
            return C.wstring_at(value)
        finally:
            self.LocalFree(value)

    def user_sid(self):
        with self.token() as token:
            info = self.token_info(token, 1)
            return self.sid_string(SidAttributes.from_buffer(info).sid)

    @contextmanager
    def sid(self, string):
        value = P()
        self.check(self.ConvertStringSidToSidW(string, C.byref(value)), "parse_sid")
        try:
            yield value
        finally:
            self.LocalFree(value)

    def package_sid(self, name):
        value = P()
        hr = self.DeriveAppContainerSidFromAppContainerName(name, C.byref(value))
        if hr < 0:
            raise Win32Error("derive_package_sid", hr & 0xFFFFFFFF)
        try:
            return self.sid_string(value)
        finally:
            self.FreeSid(value)

    def capability_sid(self, name):
        groups, caps, ngroups, ncaps = P(), P(), D(), D()
        self.check(self.DeriveCapabilitySidsFromName(
            name, C.byref(groups), C.byref(ngroups), C.byref(caps), C.byref(ncaps)),
            "derive_capability_sid")
        try:
            self.check(ncaps.value == 1, "capability_count")
            return self.sid_string(C.cast(caps, C.POINTER(P))[0])
        finally:
            for array, count in ((groups, ngroups), (caps, ncaps)):
                if array:
                    for index in range(count.value):
                        self.LocalFree(C.cast(array, C.POINTER(P))[index])
                    self.LocalFree(array)

    def set_acl(self, path, sid=None, *, execute=False):
        """Replace DACL only on application-owned staging files/directories.

        Keep host owner/SYSTEM access; deny container mutations explicitly,
        including WRITE_DAC. Never grant ALL APPLICATION PACKAGES.
        """
        owner = self.user_sid()
        entries = ""
        if sid:
            entries += f"(D;OICI;0x{WRITE_RIGHTS:x};;;{sid})"
        entries += f"(A;OICI;FA;;;{owner})(A;OICI;FA;;;SY)"
        if sid:
            rights = "FRFX" if execute else "FR"
            entries += f"(A;OICI;{rights};;;{sid})"
        descriptor = P()
        self.check(self.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            "D:P" + entries, 1, C.byref(descriptor), None), "build_acl")
        try:
            self.check(self.SetFileSecurityW(str(path), 4 | 0x80000000, descriptor), "set_acl")
        finally:
            self.LocalFree(descriptor)

    def create_job(self, limits):
        job = self.handle(self.CreateJobObjectW(None, None), "create_job")
        try:
            info = ExtendedLimits()
            # JOB_TIME | ACTIVE_PROCESS | PROCESS_MEMORY | JOB_MEMORY |
            # DIE_ON_UNHANDLED_EXCEPTION | KILL_ON_JOB_CLOSE. No breakaway flags.
            info.basic.flags = 0x4 | 0x8 | 0x100 | 0x200 | 0x400 | 0x2000
            info.basic.job_time = int(limits.cpu_seconds * 10_000_000)
            info.basic.active_processes = 1
            info.process_memory = info.job_memory = limits.memory_bytes
            self.check(self.SetInformationJobObject(job.value, 9, C.byref(info),
                                                    C.sizeof(info)), "set_job_limits")
            ui = D(0xFF)  # handles, clipboard, settings, atoms, desktops, logoff
            self.check(self.SetInformationJobObject(job.value, 4, C.byref(ui),
                                                    C.sizeof(ui)), "set_job_ui_limits")
            return job
        except BaseException:
            job.close()
            raise

    @contextmanager
    def desktop(self, name, package_sid):
        # No hooks, desktop switching, journal access or ACL changes for LPAC.
        # The name is host-generated; no process switches the user's desktop.
        descriptor = P()
        sddl = (f"D:P(D;;0xD0000;;;{package_sid})"
                f"(A;;GA;;;{self.user_sid()})(A;;GA;;;SY)"
                f"(A;;0x83;;;{package_sid})S:(ML;;NW;;;LW)")
        self.check(self.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, C.byref(descriptor), None), "build_desktop_acl")
        try:
            security = SecurityAttributes(C.sizeof(SecurityAttributes), descriptor, False)
            handle = self.CreateDesktopW(name, None, None, 0, 0x10000000, C.byref(security))
            self.check(handle, "create_private_desktop")
        finally:
            self.LocalFree(descriptor)
        try:
            yield name
        finally:
            self.check(self.CloseDesktop(handle), "close_private_desktop")

    def pipe(self, *, parent_reads):
        read, write = P(), P()
        sa = SecurityAttributes(C.sizeof(SecurityAttributes), None, True)
        self.check(self.CreatePipe(C.byref(read), C.byref(write), C.byref(sa), 65536),
                   "create_pipe")
        reader, writer = Handle(self, read.value), Handle(self, write.value)
        try:
            parent = reader if parent_reads else writer
            self.check(self.SetHandleInformation(parent.value, 1, 0), "pipe_inheritance")
            return reader, writer
        except BaseException:
            reader.close()
            writer.close()
            raise

    def write(self, handle, data):
        offset = 0
        while offset < len(data):
            count = D()
            part = data[offset:offset + 4096]
            self.check(self.WriteFile(handle, part, len(part), C.byref(count), None), "write_pipe")
            self.check(count.value != 0, "write_pipe_empty")
            offset += count.value

    def read_available(self, handle, maximum=4096):
        available = D()
        if not self.PeekNamedPipe(handle, None, 0, None, C.byref(available), None):
            if C.get_last_error() == 109:  # ERROR_BROKEN_PIPE
                return b""
            self.check(False, "peek_pipe")
        if not available.value:
            return b""
        size = min(maximum, available.value)
        buffer, count = C.create_string_buffer(size), D()
        self.check(self.ReadFile(handle, buffer, size, C.byref(count), None), "read_pipe")
        return buffer.raw[:count.value]

    def spawn(self, argv, env, cwd, package_sid, capability_sid, job, stdio, *, desktop):
        """Create suspended AND atomically inside the Job; never expose a naked worker."""
        with ExitStack() as stack:
            package = stack.enter_context(self.sid(package_sid))
            capability = stack.enter_context(self.sid(capability_sid))
            registry = stack.enter_context(self.sid(self.capability_sid("registryRead")))
            caps = (SidAttributes * 2)(SidAttributes(capability, 4), SidAttributes(registry, 4))
            # Creating a LowBox token explicitly avoids CreateProcess's profile
            # lookup (ERROR_FILE_NOT_FOUND for an unregistered random SID).
            # A restricted version of our own token needs no administrator/service.
            with self.token(access=0xB) as original:
                restricted = P()
                status = self.NtCreateLowBoxToken(C.byref(restricted), original, 0xB,
                                                  None, package, 2, caps, 0, None)
                if status < 0:
                    raise Win32Error("create_lowbox_token", status & 0xFFFFFFFF)
                lowbox = stack.enter_context(Handle(self, restricted.value))
            # LPAC opt-out and child restriction. Standard CPython's ctypes
            # requires USER32; Win32k lockdown is intentionally not enabled.
            # The approved compatibility boundary uses a private desktop + UI Job.
            opt_out, child_policy = D(1), D(1)
            handles = (P * 3)(*stdio)
            jobs = (P * 1)(job.value)
            attributes = ((0x2000F, opt_out),
                          (0x2000E, child_policy),
                          (0x20002, handles), (0x2000D, jobs))
            size = Z()
            self.InitializeProcThreadAttributeList(None, len(attributes), 0, C.byref(size))
            self.check(size.value > 0, "attribute_size")
            buffer = C.create_string_buffer(size.value)
            self.check(self.InitializeProcThreadAttributeList(
                buffer, len(attributes), 0, C.byref(size)), "init_attributes")
            stack.callback(self.DeleteProcThreadAttributeList, buffer)
            for key, value in attributes:
                self.check(self.UpdateProcThreadAttribute(buffer, 0, key, C.byref(value),
                                                         C.sizeof(value), None, None),
                           "set_process_attribute")
            startup, process = StartupInfoEx(), ProcessInfo()
            startup.startup.cb = C.sizeof(startup)
            startup.startup.flags = 0x100  # STARTF_USESTDHANDLES
            startup.startup.desktop = desktop
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = stdio
            startup.attributes = C.cast(buffer, P)
            command = C.create_unicode_buffer(subprocess.list2cmdline([str(a) for a in argv]))
            environment = C.create_unicode_buffer(
                "\0".join(f"{key}={value}" for key, value in sorted(env.items())) + "\0\0")
            # SUSPENDED | UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT |
            # DETACHED_PROCESS. CREATE_NO_WINDOW still initializes a console
            # server, which conflicts with the single-process/child restriction.
            flags = 0x4 | 0x400 | 0x80000 | 0x8
            self.check(self.CreateProcessAsUserW(lowbox.value, str(argv[0]), command, None, None, True, flags,
                                          environment, str(cwd), C.byref(startup),
                                          C.byref(process)), "create_lpac_process")
            return Handle(self, process.process), Handle(self, process.thread), process.pid

    def verify_process(self, process=None, job=None, package_sid=None):
        process = process or self.GetCurrentProcess()
        with self.token(process) as token:
            if not self.token_dword(token, 29) or not self.is_lpac(token):
                raise Win32Error("lpac_token_required", 5)
            if package_sid:
                info = self.token_info(token, 31)
                if self.sid_string(P.from_buffer(info)) != package_sid:
                    raise Win32Error("package_sid_mismatch", 5)
        for kind in (13,):  # ProcessChildProcessPolicy
            policy = D()
            self.check(self.GetProcessMitigationPolicy(process, kind, C.byref(policy), C.sizeof(policy)),
                       "query_process_mitigation")
            self.check(policy.value & 1, "process_mitigation_required")
        present = B()
        self.check(self.IsProcessInJob(process, job, C.byref(present)), "query_job_membership")
        self.check(present.value, "job_membership_required")
        ui = D()
        self.check(self.QueryInformationJobObject(job, 4, C.byref(ui), C.sizeof(ui), None),
                   "query_job_ui_limits")
        self.check(ui.value == 0xFF, "job_ui_limits_required")

    def exit_code(self, process):
        value = D()
        self.check(self.GetExitCodeProcess(process, C.byref(value)), "exit_code")
        return value.value

    def accounting(self, job):
        value = Accounting()
        self.check(self.QueryInformationJobObject(job, 1, C.byref(value), C.sizeof(value), None),
                   "job_accounting")
        return value

    def file_info(self, handle):
        info = FileInfo()
        self.check(self.GetFileInformationByHandle(handle, C.byref(info)), "file_info")
        return info

    def final_path(self, handle):
        buffer = C.create_unicode_buffer(32768)
        size = self.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        self.check(0 < size < len(buffer), "final_path")
        return Path(buffer.value)


@lru_cache(maxsize=1)
def api():
    return Win32()


def reject_reparse_path(path: Path) -> None:
    """Only local paths with no reparse components are admitted to staging."""
    path = path.absolute()
    if str(path).startswith("\\\\") or ":" in str(path)[2:]:
        raise ValueError("local_path_required")
    for component in (path, *path.parents):
        if component.lstat().st_file_attributes & 0x400:
            raise ValueError("reparse_point_denied")


@contextmanager
def open_verified_file(path: Path):
    """Hold each ancestor against rename and copy from the verified file handle.

    FILE_SHARE_READ only prevents concurrent writes/replacements until copying
    ends. Reject reparse points, non-disk objects and multiply-linked files.
    """
    import msvcrt

    win = api()
    path = path.absolute()
    reject_reparse_path(path)
    with ExitStack() as stack:
        for directory in reversed(path.parents):
            handle = stack.enter_context(win.handle(win.CreateFileW(
                str(directory), 0x80, 1 | 2, None, 3, 0x00200000 | 0x02000000, None),
                "open_ancestor"))
            if win.file_info(handle.value).attributes & 0x400:
                raise ValueError("reparse_point_denied")
        handle = stack.enter_context(win.handle(win.CreateFileW(
            str(path), 0x80000000, 1, None, 3, 0x00200000, None), "open_snapshot_source"))
        info = win.file_info(handle.value)
        if info.attributes & (0x400 | 0x10) or info.links != 1 or win.GetFileType(handle.value) != 1:
            raise ValueError("dataset_not_regular")
        final = str(win.final_path(handle.value))
        if os.path.normcase(final.removeprefix("\\\\?\\")) != os.path.normcase(str(path)):
            raise ValueError("dataset_path_changed")
        fd = msvcrt.open_osfhandle(handle.value, os.O_RDONLY | os.O_BINARY)
        handle.value = None  # ownership transfers to the CRT
        with os.fdopen(fd, "rb") as reader:
            yield reader
