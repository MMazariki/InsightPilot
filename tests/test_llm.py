import json
from types import SimpleNamespace

import pytest
import requests

from src import llm
from src import settings as app_settings

MESSAGES = [
    {"role": "system", "content": "INSTRUCTIONS"},
    {"role": "user", "content": "Net sales by region"},
]


def block(**changes):
    return dict(app_settings.MODEL_DEFAULTS, **changes)


class FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self.ok = 200 <= status < 300
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(str(self.status_code))


@pytest.fixture
def post(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return fake_post.response

    fake_post.response = FakeResponse()
    fake_post.calls = calls
    monkeypatch.setattr(llm.requests, "post", fake_post)
    return fake_post


# ---- extract_json ----------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ('{"a": 1}', '{"a": 1}'),
    ('```json\n{"a": 1}\n```', '{"a": 1}'),
    ('Here is the plan: {"a": {"b": 2}} Hope it helps.', '{"a": {"b": 2}}'),
    ("no json here", "no json here"),
    ("", ""),
])
def test_extract_json(raw, expected):
    assert llm.extract_json(raw) == expected


# ---- Ollama ----------------------------------------------------------

def test_default_ollama_request_is_identical_to_the_evaluation_harness(post, monkeypatch):
    """The default admin settings must not change what was measured."""

    from evaluation import ollama_query_plan_adapter as harness

    post.response = FakeResponse(payload={"message": {"content": ' {"x": 1} '}})
    generate, chat = llm.build_callables(block())
    assert generate("qwen2.5-coder:7b", "Net sales by region", "INSTRUCTIONS") == '{"x": 1}'
    app_url, app_kwargs = post.calls[-1]

    monkeypatch.setattr(harness.requests, "post", post)
    harness.generate_plan("qwen2.5-coder:7b", "Net sales by region", "INSTRUCTIONS")
    harness_url, harness_kwargs = post.calls[-1]

    assert app_url == harness_url == "http://localhost:11434/api/chat"
    assert json.dumps(app_kwargs["json"]) == json.dumps(harness_kwargs["json"])
    assert app_kwargs["timeout"] == harness_kwargs["timeout"] == 240

    chat("qwen2.5-coder:7b", MESSAGES)
    harness.generate_messages("qwen2.5-coder:7b", MESSAGES)
    assert json.dumps(post.calls[-2][1]["json"]) == json.dumps(post.calls[-1][1]["json"])


def test_ollama_optional_settings_are_sent_only_when_set():
    payload = llm.ollama_payload(
        block(temperature=0.3, top_p=0.9, seed=7, max_output_tokens=500,
              num_ctx=4096, keep_alive="30m"), "m", MESSAGES)
    assert payload["options"] == {
        "temperature": 0.3, "num_ctx": 4096, "top_p": 0.9, "seed": 7,
        "num_predict": 500}
    assert payload["keep_alive"] == "30m"
    bare = llm.ollama_payload(block(temperature=None), "m", MESSAGES)
    assert bare["options"] == {"num_ctx": 8192}
    assert "keep_alive" not in bare


def test_ollama_errors_are_explained(post, monkeypatch):
    post.response = FakeResponse(status=404, text="model not found")
    with pytest.raises(llm.ModelError, match="ollama pull missing"):
        llm.chat_once(block(model="missing"), MESSAGES)

    def refuse(url, **kwargs):
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(llm.requests, "post", refuse)
    with pytest.raises(llm.ModelError, match="Could not reach Ollama"):
        llm.chat_once(block(), MESSAGES)

    def slow(url, **kwargs):
        raise requests.Timeout()

    monkeypatch.setattr(llm.requests, "post", slow)
    with pytest.raises(llm.ModelError, match="within 240 seconds"):
        llm.chat_once(block(), MESSAGES)


# ---- OpenAI-compatible ----------------------------------------------

def openai_block(**changes):
    return block(provider="openai_compatible", model="gpt-x",
                 base_url="https://api.example.com/v1/", **changes)


def test_openai_request_shape_and_key_header(post):
    post.response = FakeResponse(payload={"choices": [{"message": {
        "content": '```json\n{"metrics": []}\n```'}}]})
    reply = llm.chat_once(openai_block(), MESSAGES, api_key="sk-secret")

    assert reply == '{"metrics": []}'
    url, kwargs = post.calls[0]
    assert url == "https://api.example.com/v1/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer sk-secret"
    assert kwargs["json"] == {
        "model": "gpt-x", "messages": MESSAGES, "temperature": 0.0,
        "response_format": {"type": "json_object"}}
    assert kwargs["timeout"] == 240


def test_openai_optional_settings():
    payload = llm.openai_payload(
        openai_block(temperature=None, max_output_tokens=900,
                     token_parameter="max_completion_tokens",
                     reasoning_effort="low", json_mode=False, seed=3, top_p=0.5),
        "gpt-x", MESSAGES)
    assert payload == {
        "model": "gpt-x", "messages": MESSAGES, "top_p": 0.5, "seed": 3,
        "max_completion_tokens": 900, "reasoning_effort": "low"}


def test_openai_without_key_sends_no_authorization(post):
    post.response = FakeResponse(payload={"choices": [{"message": {"content": "{}"}}]})
    llm.chat_once(openai_block(), MESSAGES)
    assert "Authorization" not in post.calls[0][1]["headers"]


@pytest.mark.parametrize("status,fragment", [
    (401, "rejected the API key"),
    (404, "does not know the model"),
    (429, "rate-limiting"),
    (500, "HTTP 500"),
])
def test_openai_errors_are_explained_without_leaking_the_key(post, status, fragment):
    post.response = FakeResponse(status=status, text="upstream detail")
    with pytest.raises(llm.ModelError, match=fragment) as caught:
        llm.chat_once(openai_block(), MESSAGES, api_key="sk-secret")
    assert "sk-secret" not in str(caught.value)


def test_openai_unexpected_or_empty_reply(post):
    post.response = FakeResponse(payload={"unexpected": True})
    with pytest.raises(llm.ModelError, match="unexpected reply"):
        llm.chat_once(openai_block(), MESSAGES)
    post.response = FakeResponse(payload={"choices": [{"message": {"content": ""}}]})
    with pytest.raises(llm.ModelError, match="empty reply"):
        llm.chat_once(openai_block(), MESSAGES)


# ---- Anthropic -------------------------------------------------------

def anthropic_block(**changes):
    return block(provider="anthropic", model="claude-opus-5-5", base_url="",
                 **changes)


class FakeMessages:
    def __init__(self, response, log, name):
        self.response, self.log, self.name = response, log, name

    def create(self, **kwargs):
        self.log.append((self.name, kwargs))
        return self.response


class FakeAnthropic:
    def __init__(self, text='{"ok": true}', stop_reason="end_turn"):
        response = SimpleNamespace(
            stop_reason=stop_reason,
            content=[SimpleNamespace(type="thinking", thinking=""),
                     SimpleNamespace(type="text", text=text)])
        self.log = []
        self.messages = FakeMessages(response, self.log, "messages")
        self.beta = SimpleNamespace(
            messages=FakeMessages(response, self.log, "beta.messages"))


def test_anthropic_request_moves_system_prompt_and_omits_sampling():
    request = llm.anthropic_request(
        anthropic_block(temperature=0.0, top_p=0.5, effort="low"),
        "claude-opus-5-5", MESSAGES)
    assert request == {
        "model": "claude-opus-5-5",
        "max_tokens": 16000,
        "system": "INSTRUCTIONS",
        "messages": [{"role": "user", "content": "Net sales by region"}],
        "output_config": {"effort": "low"},
    }
    plain = llm.anthropic_request(
        anthropic_block(max_output_tokens=2000), "claude-haiku-5-5", MESSAGES)
    assert plain["max_tokens"] == 2000
    assert "output_config" not in plain
    assert "temperature" not in plain and "thinking" not in plain


def test_anthropic_uses_refusal_fallback_by_default():
    pytest.importorskip("anthropic")
    client = FakeAnthropic(text='Sure:\n{"ok": true}')
    reply = llm._anthropic_chat(
        anthropic_block(), "claude-opus-5-5", MESSAGES, "key", client=client)
    assert reply == '{"ok": true}'
    name, kwargs = client.log[0]
    assert name == "beta.messages"
    assert kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    assert kwargs["fallbacks"] == "default"

    client = FakeAnthropic()
    llm._anthropic_chat(anthropic_block(refusal_fallback=False),
                        "claude-opus-5-5", MESSAGES, "key", client=client)
    name, kwargs = client.log[0]
    assert name == "messages"
    assert "fallbacks" not in kwargs and "betas" not in kwargs


@pytest.mark.parametrize("stop_reason,fragment", [
    ("refusal", "declined"),
    ("max_tokens", "cut off"),
])
def test_anthropic_stop_reasons_are_explained(stop_reason, fragment):
    pytest.importorskip("anthropic")
    client = FakeAnthropic(stop_reason=stop_reason)
    with pytest.raises(llm.ModelError, match=fragment):
        llm._anthropic_chat(anthropic_block(), "claude-opus-5-5", MESSAGES,
                            "key", client=client)


def test_anthropic_api_errors_are_explained():
    anthropic = pytest.importorskip("anthropic")
    import httpx2

    def failing(error):
        class Messages:
            def create(self, **kwargs):
                raise error
        return SimpleNamespace(beta=SimpleNamespace(messages=Messages()))

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    for status, cls, fragment in (
        (401, anthropic.AuthenticationError, "rejected the API key"),
        (404, anthropic.NotFoundError, "does not know the model"),
        (429, anthropic.RateLimitError, "rate-limiting"),
    ):
        error = cls("nope", response=httpx2.Response(status, request=request), body=None)
        with pytest.raises(llm.ModelError, match=fragment):
            llm._anthropic_chat(anthropic_block(), "claude-opus-5-5", MESSAGES,
                                "key", client=failing(error))


# ---- Public helpers --------------------------------------------------

def test_build_callables_honours_the_model_argument(post):
    post.response = FakeResponse(payload={"message": {"content": "{}"}})
    generate, _ = llm.build_callables(block(model="configured"))
    generate("asked-for", "q", "i")
    assert post.calls[0][1]["json"]["model"] == "asked-for"


def test_unknown_provider():
    with pytest.raises(llm.ModelError, match="Unknown model provider"):
        llm.chat_once(block(provider="bard"), MESSAGES)


def test_test_connection_reports_success_and_failure(post):
    post.response = FakeResponse(payload={"message": {"content": '{"ok": true}'}})
    ok, message = llm.test_connection(block())
    assert ok and "valid JSON" in message

    post.response = FakeResponse(payload={"message": {"content": "hello"}})
    ok, message = llm.test_connection(block())
    assert ok and "not JSON" in message

    post.response = FakeResponse(status=404)
    ok, message = llm.test_connection(block(model="gone"))
    assert not ok and "ollama pull gone" in message


def test_list_ollama_models(monkeypatch):
    monkeypatch.setattr(llm.requests, "get", lambda url, **kw: FakeResponse(
        payload={"models": [{"name": "b"}, {"name": "a"}]}))
    assert llm.list_ollama_models("http://localhost:11434") == ["a", "b"]

    def down(url, **kw):
        raise requests.ConnectionError()

    monkeypatch.setattr(llm.requests, "get", down)
    assert llm.list_ollama_models("http://localhost:11434") == []


def test_ollama_out_of_memory_is_explained(post):
    post.response = FakeResponse(status=500, text=(
        '{"error":"llama-server process has terminated: exit status 1: '
        'ggml_backend_cpu_buffer_type_alloc_buffer: failed to allocate buffer '
        'of size 1016170496"}'))
    with pytest.raises(llm.ModelError, match="out of free memory"):
        llm.chat_once(block(), MESSAGES)
