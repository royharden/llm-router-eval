"""Explicit native SDK transport with one owned process per request.

No SDK import, ambient credential lookup, or network activity occurs on import.
Caller authorization/account ownership is established outside this library.
"""

import base64
import hashlib
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..callledger.models import decode_wire_json
from .controller import TransportFailure, TransportReply


@dataclass(frozen=True)
class ProjectCredential:
    """Explicit caller-supplied credential; never loads an environment variable/file."""

    account_handle: str
    api_key: str = field(repr=False)

    def __post_init__(self):
        if (
            not isinstance(self.account_handle, str)
            or not self.account_handle
            or len(self.account_handle) > 128
            or not isinstance(self.api_key, str)
            or not self.api_key.isascii()
            or not self.api_key
            or len(self.api_key) > 4096
            or any(ord(c) <= 32 or ord(c) == 127 for c in self.api_key)
        ):
            raise ValueError("credential_shape")


class SDKTransport:
    """Pinned text-only native seam. Mock wire is always classified as simulation."""

    def __init__(self, flavor, *, credential=None, mock=None):
        if flavor not in {"openai_responses", "typesafe"}:
            raise ValueError("unsupported_sdk_flavor")
        if (credential is None) == (mock is None):
            raise ValueError("explicit_sdk_mode")
        if credential is not None and not isinstance(credential, ProjectCredential):
            raise ValueError("explicit_project_credential")
        self.flavor = flavor
        self._credential = credential
        # Detach caller dictionaries without retaining mutable test configuration.
        self._mock = None if mock is None else json.loads(json.dumps(mock, allow_nan=False))
        self.last_execution = None

    @property
    def is_test_transport(self):
        return self._mock is not None

    def prepare_payload(self, payload_json):
        """Adopt the pinned SDK wire order before request evidence is registered.

        The worker additionally compares actual SDK bytes before any physical send.
        An SDK/source/order discrepancy therefore cannot send an unbound request.
        """
        if type(payload_json) is not bytes or len(payload_json) > 32000:
            raise ValueError("sdk_request_bytes")
        body = decode_wire_json(payload_json.decode("utf-8"), max_bytes=32000)
        if not isinstance(body, dict):
            raise ValueError("sdk_request_shape")
        pending = [body]
        text_bytes = 0
        while pending:
            item = pending.pop()
            if isinstance(item, str):
                text_bytes += len(item.encode("utf-8"))
            elif isinstance(item, dict):
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
            if text_bytes > 8000:
                raise ValueError("sdk_text_bytes")
        if self.flavor == "openai_responses":
            order = (
                "model",
                "stream",
                "service_tier",
                "input",
                "max_output_tokens",
                "reasoning",
                "store",
                "text",
                "tool_choice",
                "tools",
            )
            if (
                set(body) - set(order)
                or body.get("model") != "gpt-6-luna"
                or body.get("stream") is not False
                or body.get("store") is not False
                or body.get("tools") != []
                or body.get("tool_choice") != "none"
                or body.get("service_tier", "default") != "default"
                or type(body.get("max_output_tokens")) is not int
                or not 1 <= body["max_output_tokens"] <= 512
                or not isinstance(body.get("input"), list)
                or not body["input"]
            ):
                raise ValueError("sdk_request_shape")
            # Force Standard rather than inheriting a project/account default.
            # This is included BEFORE the native bytes and evidence digest bind.
            body["service_tier"] = "default"
            for message in body["input"]:
                if (
                    not isinstance(message, dict)
                    or set(message) != {"role", "content"}
                    or message["role"] not in {"system", "user", "assistant"}
                    or not isinstance(message["content"], list)
                    or not message["content"]
                ):
                    raise ValueError("sdk_request_shape")
                for part in message["content"]:
                    if (
                        not isinstance(part, dict)
                        or set(part) != {"type", "text"}
                        or part["type"] != "input_text"
                        or not isinstance(part["text"], str)
                    ):
                        raise ValueError("sdk_text_only")
        else:
            order = ("state", "model", "questions")
            if set(body) != set(order) or body["model"] != "jev-1.13.0":
                raise ValueError("sdk_request_shape")
        prepared = json.dumps(
            {k: body[k] for k in order if k in body},
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(prepared) > 32000:
            raise ValueError("sdk_request_bytes")
        return prepared

    def send(self, payload_json, *, timeout_s, max_retries, max_response_bytes):
        if (
            max_retries != 0
            or type(timeout_s) not in {int, float}
            or not math.isfinite(timeout_s)
            or not 0 < timeout_s <= 60
            or type(max_response_bytes) is not int
            or not 1 <= max_response_bytes <= 65536
            or self.prepare_payload(payload_json) != payload_json
        ):
            raise ValueError("sdk_send_limits")
        packet = dict(
            flavor=self.flavor,
            payload_base64=base64.b64encode(payload_json).decode("ascii"),
            credential="dummy-offline-not-a-real-key"
            if self.is_test_transport
            else self._credential.api_key,
            timeout_s=timeout_s,
            max_response_bytes=max_response_bytes,
            mock=self._mock,
        )
        data = json.dumps(packet, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(data) > 131072:
            raise ValueError("sdk_worker_input_bytes")
        output, observation = _owned_worker(data, timeout_s=timeout_s)
        observation.update(
            simulation=self.is_test_transport,
            payload_sha256=hashlib.sha256(payload_json).hexdigest(),
        )
        self.last_execution = observation
        if observation["timed_out"]:
            raise TransportFailure("deadline")
        try:
            reply = decode_wire_json(output.decode("utf-8"), max_bytes=100000)
            if not isinstance(reply, dict):
                raise ValueError("worker_reply")
            if "failure" in reply:
                raise TransportFailure(reply["failure"])
            body = base64.b64decode(reply["body_base64"], validate=True)
            status = reply["status_code"]
            headers = reply["headers"]
            if (
                type(status) is not int
                or not 100 <= status <= 599
                or len(body) > max_response_bytes
                or not isinstance(headers, dict)
                or set(headers) - {"retry-after", "retry-after-ms"}
                or any(not isinstance(v, str) or len(v) > 128 for v in headers.values())
            ):
                raise ValueError("worker_reply")
            return TransportReply(status, body, headers)
        except TransportFailure:
            raise
        except Exception:
            raise TransportFailure("unknown_dispatch") from None


def _owned_worker(data, *, timeout_s):
    """Hard deadline includes startup and pipe writes; kill the owned tree before return."""
    start = time.monotonic()
    env = {
        k: os.environ[k] for k in ["SystemRoot", "WINDIR", "PATH", "TEMP", "TMP"] if k in os.environ
    }
    env.update(
        PYTHONPATH=str(Path(__file__).resolve().parents[len(__package__.split("."))]),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONUTF8="1",
    )
    job = None
    if os.name == "nt":
        job = _WindowsJob()
        # A venv's Windows launcher can create its interpreter immediately. Suspend
        # the launcher until Job assignment, so no descendant escapes that race.
        flags = dict(creationflags=subprocess.CREATE_NO_WINDOW | 0x00000004)
    elif os.name == "posix" and hasattr(os, "waitid"):
        flags = dict(start_new_session=True)
    else:
        raise TransportFailure("unknown_dispatch")
    process = None
    captured = []
    io_failed = []
    timed_out = False
    active_after = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-B", "-m", __package__ + ".sdk_worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            **flags,
        )
        if job is not None:
            job.assign(process)

        def write():
            try:
                process.stdin.write(data)
                process.stdin.close()
            except (OSError, ValueError):
                io_failed.append(True)

        def read():
            try:
                captured.append(process.stdout.read(100001))
            except (OSError, ValueError):
                io_failed.append(True)

        threads = [
            threading.Thread(target=write, daemon=True),
            threading.Thread(target=read, daemon=True),
        ]
        for thread in threads:
            thread.start()
        if job is not None:
            try:
                process.wait(timeout=max(0.001, timeout_s - (time.monotonic() - start)))
            except subprocess.TimeoutExpired:
                timed_out = True
        else:
            # Observe completion without reaping the owned session leader. Its PID cannot
            # be reused before group termination, including the normal-return path.
            while os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is None:
                if time.monotonic() - start >= timeout_s:
                    timed_out = True
                    break
                time.sleep(0.005)
    finally:
        if job is not None:
            active_after = job.close()
        elif process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process is not None:
            process.wait(timeout=5)
            for thread in locals().get("threads", []):
                thread.join(timeout=5)
                if thread.is_alive():
                    raise TransportFailure("unknown_dispatch")
            process.stdin.close()
            process.stdout.close()
    observation = dict(
        worker_pid=process.pid,
        exit_code=process.returncode,
        timed_out=timed_out,
        elapsed_seconds=time.monotonic() - start,
        owned_tree_closed=True,
        active_owned_processes_after=active_after,
        simulation=None,  # Set by the caller's explicit mode; this is process evidence only.
    )
    if io_failed or not captured or len(captured[0]) > 100000:
        if not timed_out:
            raise TransportFailure("unknown_dispatch")
    lines = (captured[0] if captured else b"").splitlines()
    event = b'{"event":"wire_send"}'
    # Worker prints compact event JSON, then exactly one final reply. A timed-out
    # send retains the event even though no final body exists.
    observed = bool(lines and lines[0].replace(b" ", b"") == event)
    observation["physical_dispatch_observed"] = observed
    if observed:
        lines = lines[1:]
    if not timed_out and len(lines) != 1:
        raise TransportFailure("unknown_dispatch")
    return (lines[0] if len(lines) == 1 else b""), observation


class _WindowsJob:
    """Kill-on-close Job assigned while the worker is still waiting for stdin."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes as w

        class Basic(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", w.DWORD),
                ("minws", ctypes.c_size_t),
                ("maxws", ctypes.c_size_t),
                ("active_limit", w.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", w.DWORD),
                ("scheduling", w.DWORD),
            ]

        class IO(ctypes.Structure):
            _fields_ = [
                (n, ctypes.c_uint64)
                for n in [
                    "readops",
                    "writeops",
                    "otherops",
                    "readbytes",
                    "writebytes",
                    "otherbytes",
                ]
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Basic),
                ("io", IO),
                ("process_mem", ctypes.c_size_t),
                ("job_mem", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        class Accounting(ctypes.Structure):
            _fields_ = [
                ("u", ctypes.c_int64),
                ("k", ctypes.c_int64),
                ("pu", ctypes.c_int64),
                ("pk", ctypes.c_int64),
                ("faults", w.DWORD),
                ("total", w.DWORD),
                ("active", w.DWORD),
                ("terminated", w.DWORD),
            ]

        self.ctypes, self.Accounting = ctypes, Accounting
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, args, result in [
            ("CreateJobObjectW", [ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            ("SetInformationJobObject", [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            ("AssignProcessToJobObject", [w.HANDLE, w.HANDLE], w.BOOL),
            (
                "QueryInformationJobObject",
                [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p],
                w.BOOL,
            ),
            ("TerminateJobObject", [w.HANDLE, w.UINT], w.BOOL),
            ("CloseHandle", [w.HANDLE], w.BOOL),
        ]:
            fn = getattr(self.k, name)
            fn.argtypes = args
            fn.restype = result
        self.handle = self.k.CreateJobObjectW(None, None)
        limit = Extended()
        limit.basic.flags = 0x2008
        limit.basic.active_limit = 8
        if not self.handle or not self.k.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limit), ctypes.sizeof(limit)
        ):
            if self.handle:
                self.k.CloseHandle(self.handle)
            raise TransportFailure("unknown_dispatch")

    def assign(self, process):
        if not self.k.AssignProcessToJobObject(self.handle, int(process._handle)):
            process.kill()
            raise TransportFailure("unknown_dispatch")
        ntdll = self.ctypes.WinDLL("ntdll")
        resume = ntdll.NtResumeProcess
        resume.argtypes = [self.ctypes.c_void_p]
        resume.restype = self.ctypes.c_long
        if resume(int(process._handle)) < 0:
            raise TransportFailure("unknown_dispatch")

    def close(self):
        c = self.ctypes
        try:
            if not self.k.TerminateJobObject(self.handle, 125):
                raise TransportFailure("unknown_dispatch")
            until = time.monotonic() + 5
            status = self.Accounting()
            while True:
                if not self.k.QueryInformationJobObject(
                    self.handle, 1, c.byref(status), c.sizeof(status), None
                ):
                    raise TransportFailure("unknown_dispatch")
                if not status.active:
                    return 0
                if time.monotonic() >= until:
                    raise TransportFailure("unknown_dispatch")
                time.sleep(0.005)
        finally:
            self.k.CloseHandle(self.handle)
