"""One native SDK request in a parent-owned, deadline-controlled process.

The parent owns admission, credentials and process termination. This worker never
opens a ledger. Its transport admits only the exact prepared bytes, one physical
send, and an unencoded bounded body before any SDK response decoder sees it.
"""

import base64
import contextlib
import hashlib
import importlib.metadata
import json
import logging
import math
import os
import socket
import sys
import time

MAX_STDIN = 131072
MAX_BODY = 65536
MAX_PAYLOAD = 32768
_URLS = {
    "openai_responses": "https://api.openai.com/v1/responses",
    "typesafe": "https://api.typesafe.ai/v1/systemone",
}


def _failure(code="unknown_dispatch"):
    return {"failure": code}


def _decode(value, maximum):
    if not isinstance(value, str) or len(value) > ((maximum + 2) // 3) * 4:
        raise ValueError("envelope")
    result = base64.b64decode(value, validate=True)
    if len(result) > maximum:
        raise ValueError("envelope")
    return result


def execute(
    flavor, payload_json, credential, timeout_s, max_response_bytes, *, mock=None, observe=None
):
    """Run the native path, returning only a closed failure on any local error."""
    try:
        for package, version in (("openai", "3.31.0"), ("typesafe-sdk", "0.7.5")):
            if importlib.metadata.version(package) != version:
                return _failure()
        return _execute(
            flavor,
            payload_json,
            credential,
            timeout_s,
            max_response_bytes,
            mock=mock,
            observe=observe,
        )
    except Exception:
        return _failure()


def _execute(
    flavor, payload_json, credential, timeout_s, max_response_bytes, *, mock=None, observe=None
):
    """Return a closed IPC reply; call only inside the sanitized owned worker.

    Direct calls support unit tests without changing process environment, logging
    or socket globals. They do not establish a hard wall deadline: only the
    supervising process can interrupt a blocked SDK/transport call.
    """
    try:
        if (
            flavor not in _URLS
            or type(payload_json) is not bytes
            or not 0 < len(payload_json) <= MAX_PAYLOAD
            or not isinstance(credential, str)
            or not credential
            or len(credential) > 4096
            or type(timeout_s) not in {int, float}
            or not math.isfinite(timeout_s)
            or not 0 < timeout_s <= 60
            or type(max_response_bytes) is not int
            or not 0 < max_response_bytes <= MAX_BODY
        ):
            return _failure()
        payload = json.loads(payload_json)
        if not isinstance(payload, dict):
            return _failure()
        if mock is not None and (
            not isinstance(mock, dict) or credential != "dummy-offline-not-a-real-key"
        ):
            return _failure()
    except (ValueError, TypeError):
        return _failure()

    # Imports stay inside this function; main sanitizes before reaching them.
    import httpx2

    deadline = time.monotonic() + timeout_s
    reply = None
    sends = 0
    bound_failed = False
    timed_out = False

    def mock_handler(request):
        if mock.get("raise_read_timeout", False):
            raise httpx2.ReadTimeout("deadline", request=request)
        delay = mock.get("delay_s", 0)
        if type(delay) not in {int, float} or not math.isfinite(delay) or not 0 <= delay <= 60:
            raise ValueError("mock")
        time.sleep(delay)
        status = mock.get("status_code", 200)
        headers = mock.get("headers", {"content-type": "application/json"})
        if type(status) is not int or not 100 <= status <= 599 or not isinstance(headers, dict):
            raise ValueError("mock")
        body = _decode(mock.get("body_base64", ""), 131072)
        return httpx2.Response(status, headers=headers, stream=httpx2.ByteStream(body))

    inner = (
        httpx2.MockTransport(mock_handler)
        if mock is not None
        else httpx2.HTTPTransport(trust_env=False, retries=0)
    )

    class BoundTransport(httpx2.BaseTransport):
        def handle_request(self, request):
            nonlocal reply, sends, bound_failed, timed_out
            if (
                sends
                or request.method != "POST"
                or str(request.url) != _URLS[flavor]
                or request.content != payload_json
                or request.headers.get("content-encoding", "identity").lower() != "identity"
            ):
                bound_failed = True
                raise ValueError("wire_binding")
            if time.monotonic() >= deadline:
                timed_out = True
                raise httpx2.ReadTimeout("deadline", request=request)
            sends += 1
            if observe is not None:
                observe()
            try:
                response = inner.handle_request(request)
            except httpx2.TimeoutException:
                timed_out = True
                raise
            try:
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    bound_failed = True
                    raise ValueError("response_encoding")
                chunks = []
                count = 0
                for chunk in response.iter_raw():
                    count += len(chunk)
                    if count > max_response_bytes:
                        bound_failed = True
                        raise ValueError("response_bytes")
                    if time.monotonic() >= deadline:
                        timed_out = True
                        raise httpx2.ReadTimeout("deadline", request=request)
                    chunks.append(chunk)
                body = b"".join(chunks)
                safe_headers = {}
                for name in ("retry-after", "retry-after-ms"):
                    value = response.headers.get(name)
                    if (
                        value is not None
                        and len(value) <= 128
                        and value.isascii()
                        and value.isprintable()
                    ):
                        safe_headers[name] = value
                reply = {
                    "status_code": response.status_code,
                    "body_base64": base64.b64encode(body).decode("ascii"),
                    "headers": safe_headers,
                }
                # Preserve only decoder-relevant headers internally, never via IPC.
                return httpx2.Response(
                    response.status_code,
                    headers={
                        "content-type": response.headers.get("content-type", "application/json")
                    },
                    content=body,
                    request=request,
                )
            except httpx2.TimeoutException:
                timed_out = True
                raise
            finally:
                response.close()

        def close(self):
            inner.close()

    transport = BoundTransport()
    http = httpx2.Client(
        transport=transport,
        trust_env=False,
        follow_redirects=False,
        timeout=timeout_s,
        headers={"Accept-Encoding": "identity"},
    )
    try:
        if flavor == "openai_responses":
            from openai import OpenAI

            class BoundedPlatformOpenAI(OpenAI):
                def platform_headers(self):
                    # The pinned SDK's optional OS telemetry calls platform.platform(),
                    # which may block in Windows WMI. Describe only this interpreter;
                    # keep authentication, serialization and dispatch in the native SDK.
                    return {
                        "X-Stainless-Lang": "python",
                        "X-Stainless-Package-Version": "3.31.0",
                        "X-Stainless-OS": {
                            "win32": "Windows",
                            "linux": "Linux",
                            "darwin": "MacOS",
                        }.get(sys.platform, "Unknown"),
                        "X-Stainless-Arch": "Unknown",
                        "X-Stainless-Runtime": sys.implementation.name,
                        "X-Stainless-Runtime-Version": sys.version.split()[0],
                    }

            with BoundedPlatformOpenAI(
                api_key=credential,
                base_url="https://api.openai.com/v1",
                organization="",
                project="",
                max_retries=0,
                timeout=timeout_s,
                http_client=http,
            ) as client:
                client.responses.create(**payload)
        else:
            from typesafe_sdk import RetryPolicy, TypeSafeClient

            with TypeSafeClient(
                api_key=credential,
                model=payload["model"],
                base_url="https://api.typesafe.ai",
                timeout=timeout_s,
                retry=RetryPolicy(max_retries=0),
                compression=False,
                http_client=http,
            ) as client:
                client.system_one(
                    state=payload["state"],
                    model=payload["model"],
                    questions=payload["questions"],
                )
    except Exception:
        # A bounded raw error/malformed body is still returned for controller
        # accounting. SDK exceptions, formatted messages and objects never cross.
        pass
    finally:
        http.close()
    if timed_out or time.monotonic() >= deadline:
        return _failure("deadline")
    if bound_failed or reply is None:
        return _failure()
    if mock is not None:
        reply["mock_sends"] = sends
        reply["request_sha256"] = hashlib.sha256(payload_json).hexdigest()
    return reply


def _deny_network():
    def deny(*args, **kwargs):
        raise RuntimeError("network_disabled")

    socket.socket.connect = deny
    socket.socket.connect_ex = deny
    socket.create_connection = deny
    socket.getaddrinfo = deny

    def audit(event, args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.bind", "socket.sendto"}:
            deny()

    sys.addaudithook(audit)
    # Positive control, before any optional SDK import.
    try:
        socket.create_connection(("example.invalid", 443))
    except RuntimeError:
        return
    raise RuntimeError("network_guard")


def main():
    """One bounded request, at most one content-free event, one closed reply."""
    allowed = {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP"}
    retained = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    os.environ.clear()
    os.environ.update(retained)
    logging.disable(sys.maxsize)
    result = _failure()
    original_stdout = sys.stdout

    def observe():
        original_stdout.write('{"event":"wire_send"}\n')
        original_stdout.flush()

    try:
        raw = sys.stdin.buffer.read(MAX_STDIN + 1)
        if len(raw) > MAX_STDIN:
            raise ValueError("envelope")
        envelope = json.loads(raw)
        if not isinstance(envelope, dict) or set(envelope) != {
            "flavor",
            "payload_base64",
            "credential",
            "timeout_s",
            "max_response_bytes",
            "mock",
        }:
            raise ValueError("envelope")
        if envelope["mock"] is not None:
            _deny_network()
        with open(os.devnull, "w", encoding="utf-8") as sink:
            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                result = execute(
                    envelope["flavor"],
                    _decode(envelope["payload_base64"], MAX_PAYLOAD),
                    envelope["credential"],
                    envelope["timeout_s"],
                    envelope["max_response_bytes"],
                    mock=envelope["mock"],
                    observe=observe,
                )
    except Exception:
        result = _failure()
    sys.stdout.write(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
