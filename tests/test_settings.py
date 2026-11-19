import json

import pytest

from src import settings as app_settings


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(app_settings, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(app_settings, "SECRETS_FILE", tmp_path / "secrets.bin")
    return tmp_path


@pytest.mark.understand
def test_defaults_are_valid_and_match_measured_configuration():
    clean, errors = app_settings.validate(app_settings.defaults())
    assert errors == []
    assert clean == app_settings.DEFAULTS
    assert clean["planner"]["provider"] == "ollama"
    assert clean["planner"]["model"] == "qwen2.5-coder:7b"
    assert clean["planner"]["temperature"] == 0
    assert clean["planner"]["num_ctx"] == 8192
    assert clean["planner"]["timeout_seconds"] == 240
    assert clean["pipeline"] == {
        "request_guard": True, "repair_turn": True,
        "answerability_check": True, "no_metric_trigger": True,
        "strict_metric_words": False, "verifier_fail_closed": False,
        "measure_name_check": True, "interpret_wording": True,
        "understand": True, "understand_model": "qwen2.5:7b-instruct",
        "max_table_rows": 50,
    }
    assert app_settings.is_default(clean)
    assert not app_settings.uses_cloud(clean)


def test_pipeline_defaults_match_the_agent():
    from src.structured_agent import DEFAULT_OPTIONS

    # create_visuals is an interface setting ("create_charts"), not a check.
    agent_checks = {k: v for k, v in DEFAULT_OPTIONS.items()
                    if k != "create_visuals"}
    assert app_settings.DEFAULTS["pipeline"] == agent_checks
    assert DEFAULT_OPTIONS["create_visuals"] is False


def test_missing_and_unknown_keys_fall_back_to_defaults():
    clean, errors = app_settings.validate(
        {"planner": {"model": "llama3"}, "nonsense": 1, "pipeline": "bad"})
    assert errors == []
    assert clean["planner"]["model"] == "llama3"
    assert clean["planner"]["num_ctx"] == 8192
    assert "nonsense" not in clean
    assert clean["pipeline"] == app_settings.DEFAULTS["pipeline"]


@pytest.mark.parametrize("patch,fragment", [
    ({"planner": {"provider": "bard"}}, "unknown provider"),
    ({"planner": {"model": "  "}}, "model name is required"),
    ({"planner": {"temperature": 9}}, "between 0.0 and 2.0"),
    ({"planner": {"timeout_seconds": 1}}, "between 5 and 3600"),
    ({"planner": {"num_ctx": "big"}}, "must be a number"),
    ({"planner": {"base_url": "localhost:11434"}}, "http://"),
    ({"planner": {"effort": "extreme"}}, "unknown effort"),
    ({"agent": {"mode": "magic"}}, "unknown mode"),
    ({"pipeline": {"max_table_rows": 0}}, "between 1 and 5000"),
    ({"interface": {"chat_height": 50}}, "between 300 and 2000"),
])
def test_invalid_values_are_reported(patch, fragment):
    clean, errors = app_settings.validate(patch)
    assert any(fragment in error for error in errors), errors
    # The result is still a complete, usable settings object.
    assert set(clean) == set(app_settings.DEFAULTS)


def test_optional_numbers_may_be_empty():
    clean, errors = app_settings.validate(
        {"planner": {"temperature": None, "top_p": "", "seed": None,
                     "max_output_tokens": None}})
    assert errors == []
    assert clean["planner"]["temperature"] is None
    assert clean["planner"]["top_p"] is None


def test_unused_verifier_block_is_not_validated():
    clean, errors = app_settings.validate(
        {"verifier": {"same_as_planner": True, "model": ""}})
    assert errors == []
    _, errors = app_settings.validate(
        {"verifier": {"same_as_planner": False, "model": ""}})
    assert any("Verifier model" in error for error in errors)


def test_effective_verifier():
    settings = app_settings.defaults()
    settings["planner"]["model"] = "planner-model"
    assert app_settings.effective_verifier(settings)["model"] == "planner-model"
    settings["verifier"].update(same_as_planner=False, model="checker")
    verifier = app_settings.effective_verifier(settings)
    assert verifier["model"] == "checker"
    assert "same_as_planner" not in verifier


def test_uses_cloud():
    settings = app_settings.defaults()
    settings["planner"].update(
        provider="openai_compatible", base_url="http://localhost:1234/v1")
    assert not app_settings.uses_cloud(settings)
    settings["planner"]["base_url"] = "https://api.openai.com/v1"
    assert app_settings.uses_cloud(settings)
    settings = app_settings.defaults()
    settings["verifier"].update(same_as_planner=False, provider="anthropic")
    assert app_settings.uses_cloud(settings)


def test_starter_questions_are_trimmed_and_capped():
    clean, _ = app_settings.validate(
        {"interface": {"starter_questions": [" a ", "", 3] + ["q"] * 20}})
    assert clean["interface"]["starter_questions"] == ["a"] + ["q"] * 7


def test_save_load_reset_round_trip(files):
    assert app_settings.load() == app_settings.DEFAULTS
    before = app_settings.stamp()

    settings = app_settings.defaults()
    settings["planner"]["model"] = "qwen2.5-coder:3b"
    settings["pipeline"]["strict_metric_words"] = True
    app_settings.save(settings)

    assert app_settings.stamp() != before
    loaded = app_settings.load()
    assert loaded["planner"]["model"] == "qwen2.5-coder:3b"
    assert loaded["pipeline"]["strict_metric_words"] is True
    assert not app_settings.is_default(loaded)

    assert app_settings.reset() == app_settings.DEFAULTS
    assert app_settings.load() == app_settings.DEFAULTS


def test_save_rejects_invalid_settings(files):
    settings = app_settings.defaults()
    settings["planner"]["temperature"] = 99
    with pytest.raises(ValueError, match="between 0.0 and 2.0"):
        app_settings.save(settings)
    assert not app_settings.SETTINGS_FILE.exists()


def test_corrupt_settings_file_gives_defaults(files):
    app_settings.SETTINGS_FILE.write_text("{not json", encoding="utf-8")
    assert app_settings.load() == app_settings.DEFAULTS


def test_api_keys_are_stored_encrypted_and_never_in_settings(files):
    assert app_settings.load_secrets() == {}
    app_settings.save_secret("planner_api_key", "sk-test-1234567890abcdef")
    app_settings.save(app_settings.defaults())

    assert app_settings.load_secrets() == {
        "planner_api_key": "sk-test-1234567890abcdef"}
    assert b"sk-test-1234567890abcdef" not in app_settings.SECRETS_FILE.read_bytes()
    assert "sk-test" not in app_settings.SETTINGS_FILE.read_text(encoding="utf-8")
    assert "api_key" not in json.dumps(app_settings.load())

    app_settings.save_secret("planner_api_key", "")
    assert app_settings.load_secrets() == {}
    with pytest.raises(ValueError):
        app_settings.save_secret("other", "x")


def test_api_key_for_roles():
    settings = app_settings.defaults()
    secrets = {"planner_api_key": "P", "verifier_api_key": "V"}
    assert app_settings.api_key_for("planner", settings, secrets) == "P"
    assert app_settings.api_key_for("verifier", settings, secrets) == "P"
    settings["verifier"]["same_as_planner"] = False
    assert app_settings.api_key_for("verifier", settings, secrets) == "V"


def test_mask_never_shows_a_whole_key():
    assert app_settings.mask("") == ""
    assert app_settings.mask("short") == "*****"
    masked = app_settings.mask("sk-ant-REALSECRETVALUE-9999")
    assert masked == "sk-an...9999"
    assert "REALSECRET" not in masked
