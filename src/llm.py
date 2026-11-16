"""Model providers for the app's planner and verifier.

``StructuredPlanningAgent`` talks to a model through two plain functions:

    generate(model, question, instructions) -> str     # the planning call
    chat(model, messages) -> str                       # repair turn, verifier

This module builds those functions from a block of admin settings
(``src/settings.py``) for three kinds of provider:

* ``ollama``             local models (the default; free, nothing leaves the PC)
* ``openai_compatible``  any ``/chat/completions`` API: OpenAI, OpenRouter,
                         Groq, Gemini's OpenAI endpoint, LM Studio, vLLM ...
* ``anthropic``          Claude models through the official ``anthropic`` SDK

With the default settings the Ollama request is byte-for-byte the one the
evaluation harness sends (``evaluation/ollama_query_plan_adapter.py``).

Only the question, the planner instructions (which include table, column and
measure names and sample column values) and the draft query plan are ever sent
to a model. Query results are never sent.
"""

import json

import requests

CLOUD_PROVIDERS = ("openai_compatible", "anthropic")
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"
ANTHROPIC_DEFAULT_MAX_TOKENS = 16000


class ModelError(Exception):
    """A model call failed; the message is safe to show to the user."""


def extract_json(text):
    """Return the JSON object inside ``text`` as a string.

    Local models in JSON mode return bare JSON. Cloud models sometimes wrap it
    in a Markdown code fence or add a sentence around it; this keeps the outer
    ``{...}``. Text without braces is returned unchanged so the caller's
    parser reports the real problem.
    """

    text = (text or "").strip()
    if text.startswith("{") and text.endswith("}"):
        return text
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return text
    return text[start:end + 1]


# ---------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------

def ollama_payload(config, model, messages):
    options = {}
    if config.get("temperature") is not None:
        temperature = config["temperature"]
        # Keep the integer 0 the evaluation harness sends.
        options["temperature"] = int(temperature) if temperature == int(temperature) else temperature
    options["num_ctx"] = config["num_ctx"]
    if config.get("top_p") is not None:
        options["top_p"] = config["top_p"]
    if config.get("seed") is not None:
        options["seed"] = config["seed"]
    if config.get("max_output_tokens") is not None:
        options["num_predict"] = config["max_output_tokens"]
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "options": options,
        "messages": messages,
    }
    if config.get("keep_alive"):
        payload["keep_alive"] = config["keep_alive"]
    return payload


def _ollama_chat(config, model, messages):
    url = config["base_url"].rstrip("/") + "/api/chat"
    try:
        response = requests.post(
            url, json=ollama_payload(config, model, messages),
            timeout=config["timeout_seconds"])
    except requests.Timeout as exc:
        raise ModelError(
            f"The local model did not answer within {config['timeout_seconds']} "
            "seconds.") from exc
    except requests.RequestException as exc:
        raise ModelError(
            f"Could not reach Ollama at {config['base_url']}. Is it running?"
        ) from exc
    if response.status_code == 404:
        raise ModelError(
            f"Ollama does not have the model '{model}'. Install it with: "
            f"ollama pull {model}")
    if not response.ok:
        raise ModelError(
            f"Ollama returned HTTP {response.status_code}: {response.text[:300]}")
    return response.json()["message"]["content"].strip()


def list_ollama_models(base_url, timeout=3):
    """Names of the models installed in Ollama (empty list if unreachable)."""

    try:
        response = requests.get(base_url.rstrip("/") + "/api/tags", timeout=timeout)
        response.raise_for_status()
        return sorted(m["name"] for m in response.json().get("models", []))
    except (requests.RequestException, ValueError, KeyError):
        return []


# ---------------------------------------------------------------------
# OpenAI-compatible
# ---------------------------------------------------------------------

def openai_payload(config, model, messages):
    payload = {"model": model, "messages": messages}
    if config.get("temperature") is not None:
        payload["temperature"] = config["temperature"]
    if config.get("top_p") is not None:
        payload["top_p"] = config["top_p"]
    if config.get("seed") is not None:
        payload["seed"] = config["seed"]
    if config.get("max_output_tokens") is not None:
        payload[config.get("token_parameter") or "max_tokens"] = config["max_output_tokens"]
    if config.get("reasoning_effort"):
        payload["reasoning_effort"] = config["reasoning_effort"]
    if config.get("json_mode", True):
        payload["response_format"] = {"type": "json_object"}
    return payload


def _openai_chat(config, model, messages, api_key):
    url = config["base_url"].rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        response = requests.post(
            url, headers=headers, json=openai_payload(config, model, messages),
            timeout=config["timeout_seconds"])
    except requests.Timeout as exc:
        raise ModelError(
            f"The model service did not answer within "
            f"{config['timeout_seconds']} seconds.") from exc
    except requests.RequestException as exc:
        raise ModelError(
            f"Could not reach the model service at {config['base_url']}.") from exc

    if response.status_code in (401, 403):
        raise ModelError(
            "The model service rejected the API key. Check the key on the "
            "Admin page.")
    if response.status_code == 404:
        raise ModelError(
            f"The model service does not know the model '{model}' (or the "
            "address is wrong).")
    if response.status_code == 429:
        raise ModelError(
            "The model service is rate-limiting or the account is out of "
            "credit. Try again shortly.")
    if not response.ok:
        raise ModelError(
            f"The model service returned HTTP {response.status_code}: "
            f"{response.text[:300]}")

    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ModelError("The model service sent an unexpected reply.") from exc
    if not content:
        raise ModelError("The model returned an empty reply.")
    return extract_json(content)


def list_openai_models(base_url, api_key, timeout=8):
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        response = requests.get(
            base_url.rstrip("/") + "/models", headers=headers, timeout=timeout)
        response.raise_for_status()
        return sorted(m["id"] for m in response.json().get("data", []))
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return []


# ---------------------------------------------------------------------
# Anthropic (official SDK)
# ---------------------------------------------------------------------

def anthropic_request(config, model, messages):
    """Keyword arguments for ``client.messages.create``.

    The Messages API takes the system prompt as a top-level field. Current
    Claude models reject ``temperature`` / ``top_p``, so sampling settings are
    not sent; depth is controlled with ``effort`` instead.
    """

    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    turns = [{"role": m["role"], "content": m["content"]}
             for m in messages if m["role"] != "system"]
    request = {
        "model": model,
        "max_tokens": config.get("max_output_tokens") or ANTHROPIC_DEFAULT_MAX_TOKENS,
        "messages": turns,
    }
    if system:
        request["system"] = system
    if config.get("effort"):
        request["output_config"] = {"effort": config["effort"]}
    return request


def _anthropic_client(config, api_key):
    import anthropic

    options = {"timeout": float(config["timeout_seconds"])}
    if api_key:
        options["api_key"] = api_key
    if config.get("base_url"):
        options["base_url"] = config["base_url"]
    # Without a saved key the SDK uses ANTHROPIC_API_KEY or an `ant auth
    # login` profile.
    return anthropic.Anthropic(**options)


def _anthropic_chat(config, model, messages, api_key, client=None):
    import anthropic

    request = anthropic_request(config, model, messages)
    try:
        client = client or _anthropic_client(config, api_key)
        if config.get("refusal_fallback", True):
            # If a safety classifier declines the request, the API re-runs it
            # on a fallback model inside the same call.
            response = client.beta.messages.create(
                betas=[ANTHROPIC_FALLBACK_BETA], fallbacks="default", **request)
        else:
            response = client.messages.create(**request)
    except anthropic.AuthenticationError as exc:
        raise ModelError(
            "Anthropic rejected the API key. Check the key on the Admin page."
        ) from exc
    except anthropic.PermissionDeniedError as exc:
        raise ModelError(
            "This Anthropic API key is not allowed to use that model.") from exc
    except anthropic.NotFoundError as exc:
        raise ModelError(f"Anthropic does not know the model '{model}'.") from exc
    except anthropic.RateLimitError as exc:
        raise ModelError(
            "Anthropic is rate-limiting this key. Try again shortly.") from exc
    except anthropic.APITimeoutError as exc:
        raise ModelError(
            f"Claude did not answer within {config['timeout_seconds']} seconds."
        ) from exc
    except anthropic.APIConnectionError as exc:
        raise ModelError("Could not reach the Anthropic API.") from exc
    except anthropic.APIStatusError as exc:
        raise ModelError(
            f"Anthropic returned HTTP {exc.status_code}: {exc.message}") from exc
    except anthropic.AnthropicError as exc:
        raise ModelError(
            "No Anthropic API key is configured. Add one on the Admin page."
        ) from exc

    if response.stop_reason == "refusal":
        raise ModelError("Claude declined to answer this request.")
    if response.stop_reason == "max_tokens":
        raise ModelError(
            "Claude's reply was cut off. Raise 'Maximum output tokens' on the "
            "Admin page.")
    text = "".join(block.text for block in response.content if block.type == "text")
    if not text.strip():
        raise ModelError("Claude returned an empty reply.")
    return extract_json(text)


# ---------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------

def chat_once(config, messages, api_key=""):
    """One call to the model described by ``config``; returns the reply text."""

    provider = config["provider"]
    model = config["model"]
    if provider == "ollama":
        return _ollama_chat(config, model, messages)
    if provider == "openai_compatible":
        return _openai_chat(config, model, messages, api_key)
    if provider == "anthropic":
        return _anthropic_chat(config, model, messages, api_key)
    raise ModelError(f"Unknown model provider '{provider}'.")


def build_callables(config, api_key=""):
    """Return ``(generate, chat)`` for ``StructuredPlanningAgent``.

    The ``model`` argument the agent passes is honoured, so the agent's own
    model name stays the single source of truth for the trace line.
    """

    def chat(model, messages):
        return chat_once(dict(config, model=model), messages, api_key)

    def generate(model, question, instructions):
        return chat(model, [
            {"role": "system", "content": instructions},
            {"role": "user", "content": question},
        ])

    return generate, chat


def test_connection(config, api_key=""):
    """Send a tiny request. Returns ``(ok, message)`` for the Admin page."""

    import time

    messages = [
        {"role": "system", "content":
            'Reply with exactly this JSON object and nothing else: {"ok": true}'},
        {"role": "user", "content": "ping"},
    ]
    started = time.time()
    try:
        reply = chat_once(config, messages, api_key)
    except ModelError as exc:
        return False, str(exc)
    except Exception as exc:  # shown to the admin, never raised into the page
        return False, f"{type(exc).__name__}: {exc}"
    seconds = time.time() - started
    try:
        json.loads(reply)
        note = "valid JSON"
    except ValueError:
        note = "reply was not JSON, so plans from this model would be rejected"
    return True, f"Answered in {seconds:.1f} s ({note}): {reply[:120]}"
