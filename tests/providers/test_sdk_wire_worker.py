"""Actual pinned SDKs on authored MockTransport wire; never live observations.

what_bug_this_catches: SDK defaults must not bypass byte binding, retry limits,
predecode response limits or closed errors. Hard process cancellation is tested
by the parent transport tests; these direct calls cannot prove interruption.
"""

import base64
import hashlib
import json

import pytest

from llm_router_eval.shared.providers.sdk_worker import execute

httpx2 = pytest.importorskip(
    "httpx2", reason="install the optional sdk extra for native wire checks"
)

DUMMY = "dummy-offline-not-a-real-key"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def payload_for(flavor):
    if flavor == "openai_responses":
        # Pinned native Responses.create body order, not caller kwargs order.
        return encoded(
            {
                "model": "gpt-6-luna",
                "stream": False,
                "input": "Authored toy: answer eligible.",
                "max_output_tokens": 512,
                "reasoning": {"effort": "none"},
                "store": False,
                "tool_choice": "none",
                "tools": [],
            }
        )
    return encoded(
        {
            "state": "Authored toy: answer eligible.",
            "model": "jev-1.13.0",
            "questions": {
                "eligibility": {
                    "type": "choice",
                    "instructions": "Is it eligible?",
                    "criteria": {"eligible": None, "ineligible": None},
                }
            },
        }
    )


def response_for(flavor):
    if flavor == "openai_responses":
        return encoded(
            {
                "id": "resp_mock_only",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "gpt-6-luna",
                "output": [
                    {
                        "id": "msg_mock_only",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "eligible", "annotations": []}],
                    }
                ],
                "usage": {"input_tokens": 20, "output_tokens": 1, "total_tokens": 21},
            }
        )
    return encoded(
        {
            "model": "jev-1.13.0",
            "usage": {"input_tokens": 20, "output_tokens": 0},
            "answers": {
                "eligibility": {
                    "type": "choice",
                    "choice": "eligible",
                    "confidence": 0.9,
                    "probabilities": {"eligible": 0.9, "ineligible": 0.1},
                }
            },
        }
    )


def mock_reply(body, **options):
    return {"body_base64": base64.b64encode(body).decode("ascii"), **options}


@pytest.fixture(params=["openai_responses", "typesafe"])
def flavor(request):
    return request.param


@pytest.fixture
def physical_sends(monkeypatch):
    sends = []
    original = httpx2.MockTransport.handle_request

    def observed(self, request):
        sends.append((request.method, str(request.url), request.content))
        return original(self, request)

    monkeypatch.setattr(httpx2.MockTransport, "handle_request", observed)
    return sends


def run(flavor, mock, payload=None, timeout=5, cap=65536):
    return execute(flavor, payload or payload_for(flavor), DUMMY, timeout, cap, mock=mock)


def test_native_wire_is_exact_and_response_stays_raw(flavor, physical_sends):
    """SDK object normalization must not replace the accounting raw body."""
    body = response_for(flavor)
    result = run(flavor, mock_reply(body))
    assert "failure" not in result
    assert result["mock_sends"] == len(physical_sends) == 1
    assert physical_sends[0][0] == "POST"
    assert physical_sends[0][2] == payload_for(flavor)
    assert result["request_sha256"] == hashlib.sha256(payload_for(flavor)).hexdigest()
    assert base64.b64decode(result["body_base64"]) == body


def test_semantically_equal_reordered_bytes_never_reach_transport(flavor, physical_sends):
    """JSON equality cannot stand in for charged request byte identity."""
    original = json.loads(payload_for(flavor))
    changed = encoded(dict(reversed(list(original.items()))))
    result = run(flavor, mock_reply(response_for(flavor)), payload=changed)
    assert result == {"failure": "unknown_dispatch"}
    assert physical_sends == []


@pytest.mark.parametrize("status", [429, 500])
def test_native_http_errors_have_one_send_and_preserve_usage(flavor, status, physical_sends):
    body = b'{"error":{"message":"AUTHORED_CANARY"},"usage":{"input_tokens":20}}'
    result = run(
        flavor,
        mock_reply(
            body,
            status_code=status,
            headers={
                "content-type": "application/json",
                "retry-after": "10",
                "retry-after-ms": "10000",
                "set-cookie": "SECRET_CANARY",
            },
        ),
    )
    assert result["status_code"] == status
    assert result["mock_sends"] == len(physical_sends) == 1
    assert base64.b64decode(result["body_base64"]) == body
    assert result["headers"] == {"retry-after": "10", "retry-after-ms": "10000"}
    assert "SECRET_CANARY" not in json.dumps(result)


def test_native_read_timeout_is_closed_and_never_retried(flavor, physical_sends):
    result = run(flavor, {"raise_read_timeout": True})
    assert result == {"failure": "deadline"}
    assert len(physical_sends) == 1


def test_invalid_json_body_is_unchanged_for_controller_accounting(flavor, physical_sends):
    result = run(flavor, mock_reply(b"{invalid-json"))
    assert base64.b64decode(result["body_base64"]) == b"{invalid-json"
    assert len(physical_sends) == 1


def test_oversize_never_reaches_sdk_response_decoder(flavor, physical_sends):
    # This invalid JSON would still be returned if SDK decoding ran and the
    # worker merely recovered a captured oversized body afterward.
    result = run(flavor, mock_reply(b"X" * 65537))
    assert result == {"failure": "unknown_dispatch"}
    assert len(physical_sends) == 1


def test_unsupported_encoding_is_rejected_before_sdk_decode(flavor, physical_sends):
    result = run(
        flavor,
        mock_reply(b"compressed-canary", headers={"content-encoding": "gzip"}),
    )
    assert result == {"failure": "unknown_dispatch"}
    assert len(physical_sends) == 1


def test_oversize_stream_closes_without_reading_the_tail(flavor, monkeypatch):
    """Do not read an unbounded response then check its final size afterward."""
    yielded = []
    closed = []

    class Chunks(httpx2.SyncByteStream):
        def __iter__(self):
            for number, chunk in enumerate((b"X" * 32768, b"Y" * 32769, b"NEVER_READ")):
                yielded.append(number)
                yield chunk

        def close(self):
            closed.append(True)

    def streamed(self, request):
        return httpx2.Response(200, stream=Chunks())

    monkeypatch.setattr(httpx2.MockTransport, "handle_request", streamed)
    result = run(flavor, mock_reply(b"unused"))
    assert result == {"failure": "unknown_dispatch"}
    assert yielded == [0, 1]
    assert closed


def test_oversized_retry_header_is_not_exported(flavor):
    result = run(
        flavor,
        mock_reply(response_for(flavor), headers={"retry-after": "X" * 129}),
    )
    assert result["headers"] == {}


def test_late_mock_is_rejected_but_does_not_claim_interruption(flavor):
    result = run(flavor, mock_reply(response_for(flavor), delay_s=0.035), timeout=0.01)
    assert result == {"failure": "deadline"}


def test_mock_refuses_non_dummy_credential(flavor, physical_sends):
    result = execute(
        flavor,
        payload_for(flavor),
        "UNAUTHORIZED_CREDENTIAL_CANARY",
        5,
        65536,
        mock=mock_reply(response_for(flavor)),
    )
    assert result == {"failure": "unknown_dispatch"}
    assert physical_sends == []


def test_observer_signals_once_after_wire_binding_and_before_mock_send(flavor, monkeypatch):
    events = []
    original = httpx2.MockTransport.handle_request

    def observed(self, request):
        events.append("physical_mock_send")
        return original(self, request)

    monkeypatch.setattr(httpx2.MockTransport, "handle_request", observed)
    result = execute(
        flavor,
        payload_for(flavor),
        DUMMY,
        5,
        65536,
        mock=mock_reply(response_for(flavor)),
        observe=lambda: events.append("wire_send"),
    )
    assert "failure" not in result
    assert events == ["wire_send", "physical_mock_send"]


def test_openai_optional_platform_telemetry_cannot_query_wmi(monkeypatch):
    import openai._base_client as sdk_base

    def forbidden():
        raise AssertionError("SDK platform probe must not execute")

    monkeypatch.setattr(sdk_base, "get_platform", forbidden)
    result = run("openai_responses", mock_reply(response_for("openai_responses")))
    assert result["status_code"] == 200 and result["mock_sends"] == 1
