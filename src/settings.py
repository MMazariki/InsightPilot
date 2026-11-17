"""Admin settings for the InsightPilot app.

Everything that can be tuned from the Admin page lives here: the schema with
defaults, validation, and load/save.

* Settings are stored in ``config/settings.json`` (not committed).
* API keys are stored separately in ``config/secrets.bin``, encrypted for the
  current Windows user (the same mechanism as the Power BI sign-in cache).
  They are never written to ``settings.json`` or to the logs.

The defaults reproduce the app exactly as it was measured in
``docs/EVALUATION.md`` (pipeline v11, local ``qwen2.5-coder:7b``). Changing a
model or a check means those measured results no longer describe the app.

This module has no Streamlit import so it can be unit-tested on its own.
"""

import copy
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
SECRETS_FILE = CONFIG_DIR / "secrets.bin"

PROVIDERS = ("ollama", "openai_compatible", "anthropic")
PROVIDER_LABELS = {
    "ollama": "Ollama (local, free)",
    "openai_compatible": "OpenAI-compatible API (OpenAI, OpenRouter, Groq, Gemini, LM Studio ...)",
    "anthropic": "Anthropic Claude API",
}
AGENT_MODES = ("launcher", "structured", "foundry")
EFFORT_LEVELS = ("", "low", "medium", "high", "xhigh", "max")
TOKEN_PARAMETERS = ("max_tokens", "max_completion_tokens")

DEFAULT_BASE_URLS = {
    "ollama": "http://localhost:11434",
    "openai_compatible": "https://api.openai.com/v1",
    "anthropic": "",
}
DEFAULT_MODELS = {
    "ollama": "qwen2.5-coder:7b",
    "openai_compatible": "",
    "anthropic": "claude-opus-5-5",
}

# One block of model settings; used for the planner and for the verifier.
MODEL_DEFAULTS = {
    "provider": "ollama",
    "model": "qwen2.5-coder:7b",
    "base_url": "http://localhost:11434",
    "timeout_seconds": 240,
    # Sampling. None means "do not send; use the provider's default".
    "temperature": 0.0,
    "top_p": None,
    "seed": None,
    "max_output_tokens": None,
    # Ollama only.
    "num_ctx": 8192,
    "keep_alive": "",
    # OpenAI-compatible only.
    "json_mode": True,
    "token_parameter": "max_tokens",
    "reasoning_effort": "",
    # Anthropic only.
    "effort": "",
    "refusal_fallback": True,
}

DEFAULTS = {
    "agent": {
        "mode": "launcher",
    },
    "planner": dict(MODEL_DEFAULTS),
    "verifier": dict(MODEL_DEFAULTS, same_as_planner=True),
    "pipeline": {
        "request_guard": True,
        "repair_turn": True,
        "answerability_check": True,
        "no_metric_trigger": True,
        "strict_metric_words": False,
        "verifier_fail_closed": False,
        "measure_name_check": True,
        "interpret_wording": True,
        "max_table_rows": 50,
    },
    "powerbi": {
        "dataset_id": "",
        "workspace_id": "",
        "username": "",
        "timeout_seconds": 60,
        "report_id": "",
        "page_id": "",
    },
    "foundry": {
        "endpoint": "",
        "model": "",
    },
    "interface": {
        "starter_questions": [
            "Show gross profit by sales channel.",
            "Compare net sales across store types.",
            "Which channel sold the most units?",
            "Margin % by customer segment.",
        ],
        "chat_height": 735,
        "show_query_details": True,
        "create_charts": True,
    },
    "voice": {
        "autosend": False,
        "model": "base.en",
    },
    # Documents page (answers from PDFs): docs/RAG_PLAN.md.
    "documents": {
        "folder": "documents",          # relative to the project, or absolute
        "recursive": True,
        "ollama_url": "http://localhost:11434",
        "embedding_model": "nomic-embed-text",
        "answer_model": "qwen2.5:7b-instruct",
        "answer_timeout": 240,
        "chunk_chars": 1000,
        "overlap_chars": 150,
        "top_k": 6,
        "min_score": 0.3,
        "show_excerpts": True,
    },
}

# (minimum, maximum) for numeric settings; None in the data means "not set".
LIMITS = {
    "timeout_seconds": (5, 3600),
    "temperature": (0.0, 2.0),
    "top_p": (0.0, 1.0),
    "seed": (0, 2**31 - 1),
    "max_output_tokens": (16, 128000),
    "num_ctx": (1024, 1048576),
    "max_table_rows": (1, 5000),
    "chat_height": (300, 2000),
    "answer_timeout": (10, 3600),
    "chunk_chars": (300, 5000),
    "overlap_chars": (0, 1000),
    "top_k": (1, 20),
    "min_score": (0.0, 1.0),
}

SECRET_NAMES = ("planner_api_key", "verifier_api_key")


def defaults():
    return copy.deepcopy(DEFAULTS)


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------

def _number(value, name, kind, optional, errors, label):
    if value is None or value == "":
        if optional:
            return None
        errors.append(f"{label}: a value is required.")
        return None
    if isinstance(value, bool):
        errors.append(f"{label}: must be a number.")
        return None
    try:
        number = kind(value)
    except (TypeError, ValueError):
        errors.append(f"{label}: must be a number.")
        return None
    low, high = LIMITS[name]
    if not low <= number <= high:
        errors.append(f"{label}: must be between {low} and {high}.")
        return None
    return number


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _clean_model(raw, base, label, errors):
    raw = raw if isinstance(raw, dict) else {}
    clean = dict(base)

    provider = raw.get("provider", base["provider"])
    if provider not in PROVIDERS:
        errors.append(f"{label}: unknown provider {provider!r}.")
        provider = base["provider"]
    clean["provider"] = provider

    clean["model"] = _text(raw.get("model", base["model"]))
    if not clean["model"]:
        errors.append(f"{label}: a model name is required.")
        clean["model"] = DEFAULT_MODELS[provider] or base["model"]

    clean["base_url"] = _text(raw.get("base_url", base["base_url"])).rstrip("/")
    if provider != "anthropic" and not clean["base_url"]:
        clean["base_url"] = DEFAULT_BASE_URLS[provider]
    if clean["base_url"] and not clean["base_url"].startswith(("http://", "https://")):
        errors.append(f"{label}: the address must start with http:// or https://.")
        clean["base_url"] = DEFAULT_BASE_URLS[provider]

    for name, kind, optional in (
        ("timeout_seconds", int, False),
        ("temperature", float, True),
        ("top_p", float, True),
        ("seed", int, True),
        ("max_output_tokens", int, True),
        ("num_ctx", int, False),
    ):
        value = _number(raw.get(name, base[name]), name, kind, optional,
                        errors, f"{label} / {name}")
        clean[name] = base[name] if (value is None and not optional) else value

    clean["keep_alive"] = _text(raw.get("keep_alive", base["keep_alive"]))
    clean["json_mode"] = bool(raw.get("json_mode", base["json_mode"]))
    clean["refusal_fallback"] = bool(
        raw.get("refusal_fallback", base["refusal_fallback"]))
    clean["reasoning_effort"] = _text(
        raw.get("reasoning_effort", base["reasoning_effort"]))

    token_parameter = raw.get("token_parameter", base["token_parameter"])
    clean["token_parameter"] = (
        token_parameter if token_parameter in TOKEN_PARAMETERS
        else base["token_parameter"])

    effort = raw.get("effort", base["effort"])
    if effort not in EFFORT_LEVELS:
        errors.append(f"{label}: unknown effort level {effort!r}.")
        effort = base["effort"]
    clean["effort"] = effort
    return clean


def validate(raw):
    """Return ``(clean_settings, errors)``.

    ``clean_settings`` always has the full schema: missing or invalid values
    fall back to the defaults, unknown keys are dropped. ``errors`` lists what
    was wrong, in words a person can act on.
    """

    raw = raw if isinstance(raw, dict) else {}
    errors = []
    clean = defaults()

    agent = raw.get("agent") if isinstance(raw.get("agent"), dict) else {}
    mode = agent.get("mode", "launcher")
    if mode not in AGENT_MODES:
        errors.append(f"Agent: unknown mode {mode!r}.")
        mode = "launcher"
    clean["agent"]["mode"] = mode

    clean["planner"] = _clean_model(
        raw.get("planner"), MODEL_DEFAULTS, "Planner model", errors)
    verifier_raw = raw.get("verifier") if isinstance(raw.get("verifier"), dict) else {}
    same = bool(verifier_raw.get("same_as_planner", True))
    # Problems in an unused verifier block are not reported.
    verifier_errors = [] if same else errors
    clean["verifier"] = _clean_model(
        verifier_raw, MODEL_DEFAULTS, "Verifier model", verifier_errors)
    clean["verifier"]["same_as_planner"] = same

    pipeline = raw.get("pipeline") if isinstance(raw.get("pipeline"), dict) else {}
    for name in ("request_guard", "repair_turn", "answerability_check",
                 "no_metric_trigger", "strict_metric_words",
                 "verifier_fail_closed", "measure_name_check",
                 "interpret_wording"):
        clean["pipeline"][name] = bool(
            pipeline.get(name, DEFAULTS["pipeline"][name]))
    rows = _number(pipeline.get("max_table_rows", 50), "max_table_rows", int,
                   False, errors, "Rows shown per answer")
    clean["pipeline"]["max_table_rows"] = 50 if rows is None else rows

    powerbi = raw.get("powerbi") if isinstance(raw.get("powerbi"), dict) else {}
    for name in ("dataset_id", "workspace_id", "username", "report_id", "page_id"):
        clean["powerbi"][name] = _text(powerbi.get(name, ""))
    timeout = _number(powerbi.get("timeout_seconds", 60), "timeout_seconds",
                      int, False, errors, "Power BI timeout")
    clean["powerbi"]["timeout_seconds"] = 60 if timeout is None else timeout

    foundry = raw.get("foundry") if isinstance(raw.get("foundry"), dict) else {}
    clean["foundry"]["endpoint"] = _text(foundry.get("endpoint", ""))
    clean["foundry"]["model"] = _text(foundry.get("model", ""))

    interface = raw.get("interface") if isinstance(raw.get("interface"), dict) else {}
    questions = interface.get("starter_questions")
    if isinstance(questions, list):
        questions = [q.strip() for q in questions if isinstance(q, str) and q.strip()]
        clean["interface"]["starter_questions"] = questions[:8]
    height = _number(interface.get("chat_height", 735), "chat_height", int,
                     False, errors, "Chat panel height")
    clean["interface"]["chat_height"] = 735 if height is None else height
    clean["interface"]["show_query_details"] = bool(
        interface.get("show_query_details", True))
    clean["interface"]["create_charts"] = bool(
        interface.get("create_charts", True))

    voice = raw.get("voice") if isinstance(raw.get("voice"), dict) else {}
    clean["voice"]["autosend"] = bool(voice.get("autosend", False))
    clean["voice"]["model"] = _text(voice.get("model", "base.en")) or "base.en"

    docs = raw.get("documents") if isinstance(raw.get("documents"), dict) else {}
    base = DEFAULTS["documents"]
    for name in ("folder", "ollama_url", "embedding_model", "answer_model"):
        clean["documents"][name] = _text(docs.get(name, base[name])) or base[name]
    for name in ("recursive", "show_excerpts"):
        clean["documents"][name] = bool(docs.get(name, base[name]))
    labels = {"answer_timeout": ("Answer timeout", int),
              "chunk_chars": ("Passage size", int),
              "overlap_chars": ("Passage overlap", int),
              "top_k": ("Passages per answer", int),
              "min_score": ("Minimum relevance", float)}
    for name, (label, kind) in labels.items():
        value = _number(docs.get(name, base[name]), name, kind, False, errors,
                        f"Documents: {label}")
        clean["documents"][name] = base[name] if value is None else value
    if clean["documents"]["overlap_chars"] >= clean["documents"]["chunk_chars"]:
        errors.append("Documents: the overlap must be smaller than the passage size.")
        clean["documents"]["overlap_chars"] = base["overlap_chars"]

    return clean, errors


def is_default(settings):
    """True when the Power BI assistant runs as measured. The Documents
    page settings are not part of that measurement."""

    clean = validate(settings)[0]
    return ({k: v for k, v in clean.items() if k != "documents"}
            == {k: v for k, v in DEFAULTS.items() if k != "documents"})


def effective_verifier(settings):
    """The model block the answerability verifier actually uses."""

    if settings["verifier"].get("same_as_planner", True):
        return dict(settings["planner"])
    return {k: v for k, v in settings["verifier"].items() if k != "same_as_planner"}


def uses_cloud(settings):
    """True if any question text would leave this computer."""

    blocks = [settings["planner"], effective_verifier(settings)]
    return any(_is_remote(block) for block in blocks)


def _is_remote(block):
    if block["provider"] == "anthropic":
        return True
    url = block.get("base_url", "")
    return not any(host in url for host in ("//localhost", "//127.0.0.1", "//[::1]"))


# ---------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------

def load(path=None):
    """Read settings; a missing or unreadable file gives the defaults."""

    path = Path(path) if path else SETTINGS_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raw = {}
    return validate(raw)[0]


def save(settings, path=None):
    """Validate and write settings. Raises ``ValueError`` listing problems."""

    clean, errors = validate(settings)
    if errors:
        raise ValueError("\n".join(errors))
    path = Path(path) if path else SETTINGS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(clean, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return clean


def reset(path=None):
    path = Path(path) if path else SETTINGS_FILE
    if path.exists():
        path.unlink()
    return defaults()


def stamp(path=None):
    """Changes whenever the saved settings or keys change (for cache keys)."""

    marks = []
    for file in (Path(path) if path else SETTINGS_FILE, SECRETS_FILE):
        try:
            marks.append(file.stat().st_mtime_ns)
        except OSError:
            marks.append(0)
    return tuple(marks)


# ---------------------------------------------------------------------
# API keys (encrypted for the current Windows user)
# ---------------------------------------------------------------------

def _persistence(path=None):
    from msal_extensions import build_encrypted_persistence

    path = Path(path) if path else SECRETS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    return build_encrypted_persistence(str(path))


def load_secrets(path=None):
    target = Path(path) if path else SECRETS_FILE
    if not target.exists():
        return {}
    try:
        data = json.loads(_persistence(target).load())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items()
            if k in SECRET_NAMES and isinstance(v, str) and v}


def save_secret(name, value, path=None):
    """Store one API key; an empty value removes it."""

    if name not in SECRET_NAMES:
        raise ValueError(f"Unknown secret {name!r}")
    target = Path(path) if path else SECRETS_FILE
    secrets = load_secrets(target)
    value = (value or "").strip()
    if value:
        secrets[name] = value
    else:
        secrets.pop(name, None)
    _persistence(target).save(json.dumps(secrets))


def mask(value):
    """``sk-ab...wxyz`` style preview; never shows the middle of a key."""

    if not value:
        return ""
    if len(value) <= 10:
        return "*" * len(value)
    return f"{value[:5]}...{value[-4:]}"


def api_key_for(role, settings, secrets=None):
    """The key for ``role`` ("planner" or "verifier"), or "" if none saved."""

    secrets = load_secrets() if secrets is None else secrets
    if role == "verifier" and settings["verifier"].get("same_as_planner", True):
        role = "planner"
    return secrets.get(f"{role}_api_key", "")
