"""NON-ADMIN: step 5 on real Windows -- squat, second server, rights, real client."""
import ctypes
import pywintypes
import win32file
import win32pipe
from trcc.adapters.infra.named_pipe import CLIENT_ACCESS, NamedPipeChannel
from trcc.adapters.system._service_broker import (SERVICE_PIPE, SERVICE_SDDL,
                                                   ServiceClient)

def result(name, ok, detail):
    print(f"{'PASS' if ok else 'FAIL'}  {name}: {detail}", flush=True)

print("elevated:", bool(ctypes.windll.shell32.IsUserAnAdmin()))

# 1. A decoy pipe this user owns; the client expects SYSTEM -> refused.
decoy = r"\\.\pipe\trcc-squat-decoy"
server = win32pipe.CreateNamedPipe(decoy, win32pipe.PIPE_ACCESS_DUPLEX | win32file.FILE_FLAG_OVERLAPPED,
                                   win32pipe.PIPE_TYPE_BYTE, 1, 65536, 65536, 0, None)
try:
    NamedPipeChannel.for_service(decoy, SERVICE_SDDL).connect(2)
    result("decoy owned by this user", False, "the client CONNECTED to a squatter")
except Exception as e:
    result("decoy owned by this user", True, f"refused: {type(e).__name__}: {e}")
finally:
    win32file.CloseHandle(server)

# 2. A second SERVER under the service's name, as this user -> refused.
try:
    h = win32pipe.CreateNamedPipe(SERVICE_PIPE, win32pipe.PIPE_ACCESS_DUPLEX,
                                  win32pipe.PIPE_TYPE_BYTE, 255, 65536, 65536, 0, None)
    win32file.CloseHandle(h)
    result("second server named trcc-service", False, "CREATED an instance of SYSTEM's pipe")
except pywintypes.error as e:
    result("second server named trcc-service", e.winerror == 5, f"WinError {e.winerror}: {e.strerror}")

# 3. Generic read+write on the service pipe -> access denied.
try:
    h = win32file.CreateFile(SERVICE_PIPE, win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                             0, None, win32file.OPEN_EXISTING, 0, None)
    win32file.CloseHandle(h)
    result("GENERIC_WRITE on trcc-service", False, "OPENED with generic write")
except pywintypes.error as e:
    result("GENERIC_WRITE on trcc-service", e.winerror == 5, f"WinError {e.winerror}: {e.strerror}")

# 4. Our real client: CLIENT_ACCESS + hello -> works.
try:
    stream = NamedPipeChannel.for_service(SERVICE_PIPE, SERVICE_SDDL).connect(5)
    client = ServiceClient(stream)
    reply = client.call({"op": "hello"})[0]
    client.close()
    result(f"real client with {CLIENT_ACCESS:#x}", reply.get("ok") is True, f"hello -> {reply}")
except Exception as e:
    result(f"real client with {CLIENT_ACCESS:#x}", False, f"{type(e).__name__}: {e}")
