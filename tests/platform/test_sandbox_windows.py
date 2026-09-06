"""Real LPAC conformance. A provisioned target must pass, never silently skip.

PowerShell: $env:RUN_SANDBOX_INTEGRATION='1'; python -m pytest tests/platform/test_sandbox_windows.py
These probes deliberately bypass both AST checks and the runner's safe builtins.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from roco_pvp_agent.sandbox.backends.windows import WindowsSandboxBackend
from roco_pvp_agent.sandbox.models import (SandboxExecutionControl, SandboxExecutionRequest,
                                          SandboxLimits)
from roco_pvp_agent.sandbox.service import SandboxQueryService

pytestmark = pytest.mark.skipif(
    os.name != "nt" or os.environ.get("RUN_SANDBOX_INTEGRATION") != "1",
    reason="set RUN_SANDBOX_INTEGRATION=1 on a prepared Windows x64 host")
ROOT = Path(__file__).resolve().parents[2]
RUNTIME = Path(os.environ.get("SANDBOX_WINDOWS_TEST_RUNTIME", str(ROOT / "sandbox-runtime/windows-runtime/python.exe")))


@pytest.fixture(scope="module")
def backend():
    instance = WindowsSandboxBackend(RUNTIME)
    assert instance.health.healthy, instance.health.reason_code
    return instance


def request(tmp_path, code="emit_result(1)", *, limits=None, cancel=None):
    data = tmp_path / "full_spirits.json"
    data.write_text('[{"name":"迪莫","value":2}]', encoding="utf-8")
    return SandboxExecutionRequest(uuid.uuid4().hex, code, ("full_spirits",),
                                   {"full_spirits": data}, "test-digest", limits or SandboxLimits(),
                                   SandboxExecutionControl(None, cancel or threading.Event()))


def raw(backend, req, body):
    source = ("import ctypes, json, os, sys\n"
              "payload=json.load(sys.stdin)\n"
              + body + "\nprint(json.dumps({'ok':True,'result':result}))\n")
    return backend._execute(req, probe_script=source)


def test_real_query_and_evidence(backend, tmp_path, caplog):
    caplog.set_level("INFO", logger="roco_pvp_agent.sandbox.audit")
    data = tmp_path / "full_spirits.json"
    data.write_text('[{"name":"迪莫","value":2}]', encoding="utf-8")
    service = SandboxQueryService(backend, data_root=tmp_path)
    result = service.execute(code="emit_result(pd.DataFrame(load_dataset('full_spirits')))",
                             dataset_ids=["full_spirits"], deadline=None, cancel_event=threading.Event())
    assert result.ok
    envelope = json.loads(result.content)
    assert envelope["backend"] == "windows"
    assert envelope["result"] == [{"name": "迪莫", "value": 2}]
    assert envelope["evidence_id"].startswith("sandbox:")


def test_real_lpac_token_job_and_no_inherited_secret_handle(backend, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"SECRET_HANDLE_SENTINEL")
    with secret.open("rb") as stream:
        os.set_inheritable(stream.fileno(), True)
        import msvcrt
        handle = msvcrt.get_osfhandle(stream.fileno())
        result = raw(backend, request(tmp_path), f"""
import importlib.util
spec=importlib.util.spec_from_file_location('win', {str(RUNTIME.parent / '_win32.py')!r})
win=importlib.util.module_from_spec(spec)
spec.loader.exec_module(win)
api=win.api()
api.verify_process()
buf=ctypes.create_string_buffer(64)
count=win.D()
try:
    success=api.ReadFile({handle},buf,64,ctypes.byref(count),None)
    result=(not success or b'SECRET_HANDLE_SENTINEL' not in buf.raw)
except OSError as e:
    result=(getattr(e,'winerror',0)&0xFFFFFFFF) in (6,0xC0000008)
""")
    assert result.ok and result.result is True


def test_real_host_read_write_and_process_injection_denied(backend, tmp_path):
    secret = tmp_path / "host-secret.txt"
    secret.write_text("SECRET", encoding="utf-8")
    result = raw(backend, request(tmp_path), f"""
result=[]
for path,mode in [({str(secret)!r},'rb'),({str(secret)!r},'wb'),
                  (payload['dataset_paths']['full_spirits'],'wb'),
                  (sys.executable,'wb'),(os.path.join(os.getcwd(),'new.txt'),'wb')]:
    try:
        with open(path,mode): pass
        result.append(False)
    except OSError as e: result.append(e.errno==13 and getattr(e,'winerror',None) in (None,5))
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.OpenProcess.restype=ctypes.c_void_p
k.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
h=k.OpenProcess(0x20|0x8|0x80,False,{os.getpid()})
result.append(not h and ctypes.get_last_error()==5)
""")
    assert result.ok and result.result == [True] * 6
    assert secret.read_text() == "SECRET"


def test_real_acl_change_and_host_named_pipe_denied(backend, tmp_path):
    # A host pipe with its ordinary DACL must remain inaccessible. The parent
    # holds it open so a missing endpoint cannot masquerade as denied access.
    from roco_pvp_agent.sandbox.backends.win32 import api, P, D, W
    import ctypes as C
    win = api()
    kernel = C.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateNamedPipeW
    create.restype = P
    create.argtypes = [W, D, D, D, D, D, D, P]
    name = r"\\.\pipe\roco-conformance-" + uuid.uuid4().hex
    with win.handle(create(name, 3, 0, 1, 4096, 4096, 0, None), "create_test_pipe"):
        result = raw(backend, request(tmp_path), f"""
import importlib.util
spec=importlib.util.spec_from_file_location('win', {str(RUNTIME.parent / '_win32.py')!r})
win=importlib.util.module_from_spec(spec)
spec.loader.exec_module(win)
api=win.api()
result=[]
try:
    api.set_acl(payload['dataset_paths']['full_spirits'])
    result.append(False)
except OSError as e: result.append(e.winerror==5)
h=api.CreateFileW({name!r},0xC0000000,0,None,3,0,None)
result.append(h==win.INVALID_HANDLE and ctypes.get_last_error()==5)
""")
    assert result.ok and result.result == [True, True]


def test_real_cpu_and_memory_limits(backend, tmp_path):
    cpu = raw(backend, request(tmp_path, limits=SandboxLimits(cpu_seconds=1, wall_seconds=4)),
              "while True: pass")
    assert cpu.error_code == "sandbox_resource_limit" and cpu.resource_reason == "cpu"
    memory = backend.execute(request(tmp_path,
        "values=[0]*200_000_000\nemit_result(len(values))"))
    assert memory.error_code == "sandbox_resource_limit"


def test_real_wall_timeout_cancel_and_raw_output(backend, tmp_path):
    started = time.monotonic()
    timeout = raw(backend, request(tmp_path, limits=SandboxLimits(wall_seconds=0.5)),
                  "import time\ntime.sleep(30)\nresult=1")
    assert timeout.error_code == "sandbox_timeout"
    assert time.monotonic() - started < 2.5
    cancel = threading.Event()
    timer = threading.Timer(0.3, cancel.set)
    timer.start()
    try:
        cancelled = raw(backend, request(tmp_path, cancel=cancel),
                        "import time\ntime.sleep(30)\nresult=1")
    finally:
        timer.cancel()
    assert cancelled.error_code == "sandbox_timeout"
    overflow = raw(backend, request(tmp_path), "os.write(1,b'x'*100000)\nresult=1")
    assert overflow.error_code == "sandbox_output_too_large"
    assert not any(t.name.startswith("sandbox-input-") for t in threading.enumerate())


def test_real_unicode_paths_and_concurrent_identity_separation(backend, tmp_path):
    from roco_pvp_agent.sandbox.backends.win32 import api
    roots = [tmp_path / "中文 路径 A", tmp_path / "中文 路径 B"]
    for root in roots:
        root.mkdir()
    body = f"""
import importlib.util, time
spec=importlib.util.spec_from_file_location('win', {str(RUNTIME.parent / '_win32.py')!r})
win=importlib.util.module_from_spec(spec)
spec.loader.exec_module(win)
api=win.api()
with api.token() as token:
    info=api.token_info(token,31)
    sid=api.sid_string(win.P.from_buffer(info))
time.sleep(0.2)
result={{'sid':sid,'path':payload['dataset_paths']['full_spirits']}}
"""
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(lambda root: raw(backend, request(root), body), roots))
    assert all(r.ok for r in results)
    assert results[0].result["sid"] != results[1].result["sid"]
    for result in results:
        assert not Path(result.result["path"]).exists()


def test_real_runtime_manifest_tamper_fails_closed(tmp_path):
    # Avoid copying the large runtime: an incomplete forged bundle must fail
    # verification before any executable in it is launched.
    python = tmp_path / "python.exe"
    python.write_bytes(b"not executable")
    (tmp_path / "roco-runtime.json").write_text('{"format":1}', encoding="utf-8")
    instance = WindowsSandboxBackend(python)
    assert not instance.health.healthy
    assert instance.health.reason_code == "windows_runtime_integrity_failed"


def test_real_private_registry_and_desktop_denied(backend, tmp_path):
    import winreg
    name = "Software\\RocoSandboxTest" + uuid.uuid4().hex
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, name) as key:
        winreg.SetValueEx(key, "secret", 0, winreg.REG_SZ, "HOST_ONLY")
    try:
        result = raw(backend, request(tmp_path), f"""
import winreg
result=[]
try:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,{name!r}) as key:
        winreg.QueryValueEx(key,'secret')
    result.append(False)
except OSError as e: result.append(e.winerror==5)
u=ctypes.WinDLL('user32',use_last_error=True)
k=ctypes.WinDLL('kernel32',use_last_error=True)
P=ctypes.c_void_p
D=ctypes.c_uint32
u.GetThreadDesktop.restype=P
u.GetThreadDesktop.argtypes=[D]
u.GetUserObjectInformationW.restype=ctypes.c_int
u.GetUserObjectInformationW.argtypes=[P,ctypes.c_int,P,D,P]
desktop=u.GetThreadDesktop(k.GetCurrentThreadId())
name=ctypes.create_unicode_buffer(256)
needed=D()
ok=u.GetUserObjectInformationW(desktop,2,name,ctypes.sizeof(name),ctypes.byref(needed))
result.append(bool(ok) and name.value.startswith('roco-'))
u.OpenDesktopW.restype=P
u.OpenDesktopW.argtypes=[ctypes.c_wchar_p,D,ctypes.c_int,D]
other=u.OpenDesktopW('Default',0,False,0x101)
result.append(not other and ctypes.get_last_error()==5)
if other:
    u.CloseDesktop.argtypes=[P]
    u.CloseDesktop(other)
u.OpenClipboard.argtypes=[P]
opened=u.OpenClipboard(None)
result.append(not opened and ctypes.get_last_error()==5)
if opened: u.CloseClipboard()
""")
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, name)
    assert result.ok and result.result == [True] * 4


def test_real_concurrent_requests_cannot_read_each_other(backend, tmp_path, monkeypatch):
    barrier = threading.Barrier(2)
    roots = []
    lock = threading.Lock()
    supervise = backend._supervise

    def coordinated(argv, env, root, *args):
        with lock:
            roots.append(root)
        barrier.wait(timeout=5)
        peer = next(other for other in roots if other != root) / "data/full_spirits.json"
        (root / "probe.py").write_text(
            "import json,sys\njson.load(sys.stdin)\n"
            f"try:\n open({str(peer)!r},'rb').read()\n result=False\n"
            "except OSError as e:\n result=e.errno==13\n"
            "print(json.dumps({'ok':True,'result':result}))\n", encoding="utf-8")
        try:
            return supervise(argv, env, root, *args)
        finally:
            # Keep both snapshots present until both denial checks finish.
            barrier.wait(timeout=5)

    monkeypatch.setattr(backend, "_supervise", coordinated)
    paths = [tmp_path / "A", tmp_path / "B"]
    for path in paths:
        path.mkdir()
    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(lambda path: raw(backend, request(path), "result=0"), paths))
    assert all(result.ok and result.result is True for result in results)
    assert all(not root.exists() for root in roots)


def test_real_host_window_messages_denied(backend, tmp_path):
    import ctypes as C
    from roco_pvp_agent.sandbox.backends.win32 import P, D, W
    user = C.WinDLL("user32", use_last_error=True)
    user.CreateWindowExW.restype = P
    user.CreateWindowExW.argtypes = [D, W, W, D, C.c_int, C.c_int, C.c_int, C.c_int, P, P, P, P]
    user.DestroyWindow.argtypes = [P]
    user.SendMessageTimeoutW.restype = P
    user.SendMessageTimeoutW.argtypes = [P, D, C.c_size_t, P, D, D, P]
    # A private host-owned message-only window; never send messages to user apps.
    window = user.CreateWindowExW(0, "STATIC", "HOST_WINDOW_SENTINEL", 0,
                                  0, 0, 0, 0, P(-3), None, None, None)
    assert window
    try:
        buffer = C.create_unicode_buffer(128)
        returned = C.c_size_t()
        assert user.SendMessageTimeoutW(window, 0xD, len(buffer), buffer, 2, 1000, C.byref(returned))
        assert buffer.value == "HOST_WINDOW_SENTINEL"
        result = raw(backend, request(tmp_path), f"""
u=ctypes.WinDLL('user32',use_last_error=True)
u.SendMessageTimeoutW.restype=ctypes.c_void_p
u.SendMessageTimeoutW.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_size_t,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
buffer=ctypes.create_unicode_buffer(128)
returned=ctypes.c_size_t()
ctypes.set_last_error(0)
ok=u.SendMessageTimeoutW({window},0xD,len(buffer),buffer,2,1000,ctypes.byref(returned))
result=[not ok,ctypes.get_last_error(),buffer.value]
""")
        assert result.ok, result
        assert result.result[0] and result.result[1] in {5, 1400} and result.result[2] == ""
    finally:
        assert user.DestroyWindow(window)


@pytest.mark.parametrize("phase", ["suspended", "running"])
def test_real_host_death_kills_job_even_during_startup(backend, tmp_path, phase):
    import ctypes as C
    import shutil
    import sys
    from roco_pvp_agent.sandbox.backends.win32 import api, P, D, B
    report = tmp_path / "worker.json"
    helper = tmp_path / "host.py"
    helper.write_text(f'''
import json, os, tempfile, threading, time
from pathlib import Path
from roco_pvp_agent.sandbox.backends.win32 import api
from roco_pvp_agent.sandbox.backends.windows import WindowsSandboxBackend
from roco_pvp_agent.sandbox.models import SandboxExecutionRequest, SandboxExecutionControl, SandboxLimits
tempfile.tempdir={str(tmp_path)!r}
backend=WindowsSandboxBackend(Path({str(RUNTIME)!r}))
assert backend.health.healthy
win=api()
spawn=win.spawn
def capture(*args,**kwargs):
    result=spawn(*args,**kwargs)
    Path({str(report)!r}).write_text(json.dumps({{'pid':result[2],'root':str(args[2])}}))
    if {phase!r}=='suspended': os._exit(0)
    return result
win.spawn=capture
data=Path({str(tmp_path / 'full_spirits.json')!r})
data.write_text('[]')
req=SandboxExecutionRequest('host-death','emit_result(1)',('full_spirits',),{{'full_spirits':data}},'test',SandboxLimits(wall_seconds=30),SandboxExecutionControl(None,threading.Event()))
backend._execute(req,probe_script='import json,sys,time\\njson.load(sys.stdin)\\ntime.sleep(60)')
''', encoding="utf-8")
    host = subprocess.Popen([sys.executable, str(helper)], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    win = api()
    process = None
    details = None
    try:
        deadline = time.monotonic() + 20
        while not report.exists() and host.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        if not report.exists():
            if host.poll() is None:
                host.kill()
            _, diagnostic = host.communicate(timeout=3)
            pytest.fail(f"host never reached startup: exit={host.returncode}, "
                        f"stderr={diagnostic.decode(errors='replace')}")
        details = json.loads(report.read_text())
        kernel = C.WinDLL("kernel32", use_last_error=True)
        open_process = kernel.OpenProcess
        open_process.restype = P
        open_process.argtypes = [D, B, D]
        value = open_process(0x100000 | 0x1000, False, details["pid"])
        if value:
            process = win.handle(value, "open_test_worker")
        else:
            assert phase == "suspended" and C.get_last_error() == 87
        if phase == "running":
            assert win.WaitForSingleObject(process.value, 0) == 258
            host.kill()
        host.wait(timeout=3)
        if process:
            assert win.WaitForSingleObject(process.value, 3000) == 0
    finally:
        if host.poll() is None:
            host.kill()
        host.communicate(timeout=3)
        if process:
            process.close()
        if details:
            root = Path(details["root"]).resolve()
            assert root.is_relative_to(tmp_path.resolve()) and root.name.startswith("roco-windows-")
            if root.exists():
                shutil.rmtree(root)


# Fixed native DNS probe, shared with the host positive control. Using a real
# local DNS responder avoids mistaking missing connectivity/NXDOMAIN for denial.
DNS_PROBE = '''
import ctypes as C, socket
D=C.c_uint32
P=C.c_void_p
def dns_query(name,custom=True):
    dns=C.WinDLL('dnsapi',use_last_error=True)
    # The legacy native resolver accepts an explicit IPv4 server list too.
    servers=(D*2)(1,int.from_bytes(socket.inet_aton('127.0.0.2'),'little'))
    dns.DnsQuery_W.restype=D
    dns.DnsQuery_W.argtypes=[C.c_wchar_p,C.c_uint16,D,P,P,P]
    records=P()
    status=dns.DnsQuery_W(name,1,0x108,servers if custom else None,C.byref(records),None)
    if records:
        dns.DnsRecordListFree.argtypes=[P,C.c_int]
        dns.DnsRecordListFree(records,1)
    return int(status)
'''


def test_real_native_dns_denied_with_live_host_control(backend, tmp_path):
    import socket
    import struct
    control_name = "control-" + uuid.uuid4().hex + ".example.com"
    worker_name = "worker-" + uuid.uuid4().hex + ".example.com"
    received = []
    stop = threading.Event()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(("127.0.0.2", 53))
        server.settimeout(0.1)

        def respond():
            while not stop.is_set():
                try:
                    packet, peer = server.recvfrom(4096)
                except socket.timeout:
                    continue
                received.append(packet)
                end = 12
                while packet[end]:
                    end += packet[end] + 1
                question = packet[12:end + 5]
                response = packet[:2] + struct.pack('!HHHHH',0x8183,1,0,0,0) + question
                server.sendto(response, peer)

        thread = threading.Thread(target=respond)
        thread.start()
        try:
            namespace = {}
            exec(DNS_PROBE, namespace)
            assert namespace["dns_query"](control_name) == 9003
            assert received, "host control did not reach the live DNS server"
            received.clear()
            result = raw(backend, request(tmp_path), DNS_PROBE + f"\nresult=[dns_query({worker_name!r}),dns_query({worker_name!r},False)]")
            # Windows also reports ERROR_INVALID_PARAMETER for resolver APIs
            # unavailable to a profile-less LPAC. Identical host calls below
            # must actually resolve; an invalid test cannot pass as isolation.
            assert result.ok and all(code in {5, 87, 10013} for code in result.result), result
            assert not received, "sandbox DNS request escaped through the Windows resolver"
            assert namespace["dns_query"](worker_name) == 9003
            assert namespace["dns_query"](worker_name, False) in {9003, 9501}
        finally:
            stop.set()
            thread.join(timeout=1)
            assert not thread.is_alive()


@pytest.mark.asyncio
async def test_real_sse_disconnect_cancels_worker(backend, tmp_path, monkeypatch):
    import asyncio
    from urllib.parse import urlencode
    from langchain_core.messages import AIMessage
    from fakes import ScriptedLLM, tool_call
    from roco_pvp_agent.advisor.agent import TeamAdvisorAgent
    from roco_pvp_agent.config import Settings
    from roco_pvp_agent.sandbox.service import SandboxSetup
    from roco_pvp_agent.sandbox.backends.win32 import api
    from ui import server

    request(tmp_path)  # provision the synthetic catalog
    service = SandboxQueryService(backend, data_root=tmp_path)
    settings = Settings(api_key="test", base_url="http://test.invalid", model="test", timeout=15)
    llm = ScriptedLLM([
        AIMessage(content="", tool_calls=[tool_call("tool_search", {"tool_names": ["sandbox_python_query"]}, "discover")]),
        AIMessage(content="", tool_calls=[tool_call("sandbox_python_query", {
            "code": "while True:\n    pass\nemit_result(1)", "dataset_ids": ["full_spirits"]}, "query")]),
        AIMessage(content="", tool_calls=[tool_call("final_answer", {"text": "cancelled"}, "final")]),
    ])
    agent = TeamAdvisorAgent(settings, llm=llm, sandbox_setup=SandboxSetup(service, backend.health))
    assert agent._registry.get('sandbox_python_query').tool._service._backend is backend
    monkeypatch.setattr(server, "TeamAdvisorAgent", lambda *a, **k: agent)
    app = server.create_chat_app(settings)
    started, finished = threading.Event(), threading.Event()
    outcomes = []
    resume = api().ResumeThread
    execute = backend._execute

    def resumed(handle):
        value = resume(handle)
        started.set()
        return value

    def completed(*args, **kwargs):
        try:
            result = execute(*args, **kwargs)
            outcomes.append(result)
            return result
        finally:
            finished.set()

    monkeypatch.setattr(api(), "ResumeThread", resumed)
    monkeypatch.setattr(backend, "_execute", completed)
    initial = True

    async def receive():
        nonlocal initial
        if initial:
            initial = False
            return {"type": "http.request", "body": b"", "more_body": False}
        assert await asyncio.to_thread(started.wait, 5), "SSE never started the sandbox tool"
        return {"type": "http.disconnect"}

    messages = []

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"},
             "http_version": "1.1", "method": "GET", "scheme": "http",
             "path": "/api/chat/stream", "raw_path": b"/api/chat/stream",
             "query_string": urlencode({"message": "帮我组队"}).encode(), "headers": [],
             "client": ("127.0.0.1", 10000), "server": ("127.0.0.1", 80), "root_path": ""}
    await asyncio.wait_for(app(scope, receive, send), 10)
    assert started.is_set(), b''.join(m.get('body',b'') for m in messages).decode('utf-8')
    assert await asyncio.to_thread(finished.wait, 3), (outcomes, messages)
    assert outcomes and outcomes[0].error_code == "sandbox_timeout"
    assert outcomes[0].resource_reason in {"cancelled", "cancelled_or_deadline"}
    assert not any(thread.name.startswith("sandbox-input-") for thread in threading.enumerate())
