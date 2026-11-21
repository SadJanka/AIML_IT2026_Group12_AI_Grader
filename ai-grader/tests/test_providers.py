import json
from dataclasses import replace

import httpx
import pytest

from app.config import settings
from app.grading import providers
from app.grading.grader import grade_text
from app.grading.providers import GradingRequest, GroqProvider, LLMError, get_provider

CRITERIA = [{"id": 1, "name": "Saturs", "description": "", "max_points": 3, "topic": ""}]
REQ = GradingRequest("U", CRITERIA, "teksts")
GOOD = json.dumps({"criteria": [{"id": 1, "points": 2}], "summary": "ok"})


def groq_cfg(**kw):
    return replace(settings, llm_provider="groq", groq_api_key="gsk_test", **kw)


def fake_post(responses, calls):
    def _post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        status, body, hdrs = responses.pop(0)
        return httpx.Response(status, json=body, headers=hdrs, request=httpx.Request("POST", url))
    return _post


def ok_body(content=GOOD):
    return {"choices": [{"message": {"content": content}}]}


def test_groq_is_selectable():
    assert isinstance(get_provider(groq_cfg()), GroqProvider)
    assert get_provider(groq_cfg()).model_label.startswith("groq:")


def test_groq_request_shape(monkeypatch):
    calls = []
    monkeypatch.setattr(providers.httpx, "post", fake_post([(200, ok_body(), {})], calls))
    out = GroqProvider(groq_cfg()).generate("sys", "user", REQ)
    assert out == GOOD
    assert calls[0]["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer gsk_test"
    assert calls[0]["json"]["response_format"] == {"type": "json_object"}


def test_waits_and_retries_on_429(monkeypatch):
    calls, slept = [], []
    monkeypatch.setattr(providers.httpx, "post", fake_post([
        (429, {"error": "rate"}, {"retry-after": "7"}),
        (429, {"error": "rate"}, {}),
        (200, ok_body(), {}),
    ], calls))
    out = GroqProvider(groq_cfg(), sleep=slept.append).generate("s", "u", REQ)
    assert out == GOOD and len(calls) == 3
    assert slept == [7.0, 10.0]


def test_gives_clear_error_when_limit_never_clears(monkeypatch):
    responses = [(429, {"error": "rate"}, {})] * 3
    monkeypatch.setattr(providers.httpx, "post", fake_post(list(responses), []))
    with pytest.raises(LLMError, match="limit"):
        GroqProvider(groq_cfg(llm_max_retries=2), sleep=lambda s: None).generate("s", "u", REQ)


def test_wrong_key_message(monkeypatch):
    monkeypatch.setattr(providers.httpx, "post", fake_post([(401, {"error": "bad key"}, {})], []))
    with pytest.raises(LLMError, match="API key"):
        GroqProvider(groq_cfg()).generate("s", "u", REQ)


def test_missing_key_message():
    with pytest.raises(LLMError, match="GROQ_API_KEY"):
        GroqProvider(replace(settings, groq_api_key="")).generate("s", "u", REQ)


def test_bad_json_400_is_retried_by_grader(monkeypatch):
    calls = []
    monkeypatch.setattr(providers.httpx, "post", fake_post([
        (400, {"error": {"code": "json_validate_failed"}}, {}),
        (200, ok_body(), {}),
    ], calls))
    result = grade_text("U", CRITERIA, "teksts", provider=GroqProvider(groq_cfg()))
    assert len(calls) == 2 and result.criteria[0].points == 2


def test_json_mode_can_be_disabled(monkeypatch):
    calls = []
    monkeypatch.setattr(providers.httpx, "post", fake_post([(200, ok_body(), {})], calls))
    GroqProvider(groq_cfg(llm_json_mode=False)).generate("s", "u", REQ)
    assert "response_format" not in calls[0]["json"]
