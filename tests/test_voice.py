import io
import wave
from types import SimpleNamespace

import numpy as np
import pytest

from src import voice


def make_wav(seconds=1.0, rate=16000, channels=1, width=2, amplitude=0.2):
    count = int(seconds * rate)
    tone = np.sin(2 * np.pi * 220 * np.arange(count) / rate) * amplitude
    if width == 2:
        pcm = (tone * 32767).astype(np.int16)
    elif width == 4:
        pcm = (tone * 2147483647).astype(np.int32)
    else:
        pcm = (tone * 127 + 128).astype(np.uint8)
    pcm = np.repeat(pcm[:, None], channels, axis=1)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        writer.writeframes(pcm.tobytes())
    return buffer.getvalue()


class FakeEngine:
    def __init__(self, texts):
        self.texts = texts
        self.calls = []

    def transcribe(self, samples, **params):
        self.calls.append((samples, params))
        return [SimpleNamespace(text=t) for t in self.texts]


class FakeUpload:
    def __init__(self, data):
        self._data = data

    def getvalue(self):
        return self._data


# ---- split_submission ------------------------------------------------

def test_split_submission_none_and_plain_string():
    assert voice.split_submission(None) == ("", None)
    assert voice.split_submission("  Net sales by region  ") == (
        "Net sales by region", None)


def test_split_submission_chat_input_value_shapes():
    typed = SimpleNamespace(text=" Units by brand ", audio=None)
    assert voice.split_submission(typed) == ("Units by brand", None)
    spoken = SimpleNamespace(text="", audio=FakeUpload(b"RIFF"))
    assert voice.split_submission(spoken) == ("", b"RIFF")
    empty_audio = SimpleNamespace(text="hi", audio=FakeUpload(b""))
    assert voice.split_submission(empty_audio) == ("hi", None)


# ---- decode_wav ------------------------------------------------------

def test_decode_wav_16k_mono():
    samples = voice.decode_wav(make_wav(1.0))
    assert samples.dtype == np.float32 and len(samples) == 16000
    assert 0.15 < float(np.abs(samples).max()) <= 0.21


@pytest.mark.parametrize("rate,channels,width", [
    (48000, 1, 2), (44100, 2, 2), (16000, 2, 4), (8000, 1, 1),
])
def test_decode_wav_converts_other_formats(rate, channels, width):
    samples = voice.decode_wav(
        make_wav(1.0, rate=rate, channels=channels, width=width))
    assert samples.dtype == np.float32
    assert abs(len(samples) - 16000) <= 2
    assert float(np.abs(samples).max()) <= 1.0


def test_decode_wav_rejects_bad_input():
    with pytest.raises(voice.VoiceError, match="could not be read"):
        voice.decode_wav(b"not a wav file")
    with pytest.raises(voice.VoiceError, match="too short"):
        voice.decode_wav(make_wav(0.1))
    with pytest.raises(voice.VoiceError, match="longer than 30"):
        voice.decode_wav(make_wav(31.0))


# ---- clean_transcript ------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (" Show  Net Sales by region. ", "Show Net Sales by region."),
    ("[BLANK_AUDIO]", ""),
    ("[sound of a machine running]", ""),
    ("(music) Units sold by brand [noise]", "Units sold by brand"),
    ("", ""),
    (None, ""),
])
def test_clean_transcript(raw, expected):
    assert voice.clean_transcript(raw) == expected


# ---- vocabulary_prompt -----------------------------------------------

def test_vocabulary_prompt_uses_model_terms_and_is_bounded():
    prompt = voice.vocabulary_prompt()
    assert prompt.startswith("Retail sales questions.")
    assert "Net Sales" in prompt and "Gross Profit" in prompt
    assert "Riyadh" in prompt
    assert len(prompt) <= voice.PROMPT_MAX_CHARS


def test_vocabulary_prompt_survives_missing_files(tmp_path):
    prompt = voice.vocabulary_prompt(
        tmp_path / "none.json", tmp_path / "none2.json")
    assert prompt == "Retail sales questions."


# ---- Transcriber / transcribe_wav ------------------------------------

def test_transcriber_passes_prompt_and_cleans_output():
    engine = FakeEngine([" Show Net Sales", " by region. ", "[BLANK_AUDIO]"])
    transcriber = voice.Transcriber(prompt="hint", engine=engine)
    assert voice.transcribe_wav(make_wav(1.0), transcriber) == (
        "Show Net Sales by region.")
    samples, params = engine.calls[0]
    assert samples.dtype == np.float32
    assert params == {"language": "en", "initial_prompt": "hint"}


def test_transcribe_wav_reports_silence():
    transcriber = voice.Transcriber(
        prompt="", engine=FakeEngine(["[BLANK_AUDIO]"]))
    with pytest.raises(voice.VoiceError, match="couldn't hear"):
        voice.transcribe_wav(make_wav(1.0), transcriber)


def test_transcriber_wraps_engine_failure():
    class Broken:
        def transcribe(self, samples, **params):
            raise RuntimeError("boom")

    transcriber = voice.Transcriber(prompt="", engine=Broken())
    with pytest.raises(voice.VoiceError, match="could not be transcribed"):
        voice.transcribe_wav(make_wav(1.0), transcriber)


# ---- settings --------------------------------------------------------

def test_settings_from_environment(monkeypatch):
    monkeypatch.delenv("INSIGHTPILOT_VOICE_MODEL", raising=False)
    monkeypatch.delenv("INSIGHTPILOT_VOICE_AUTOSEND", raising=False)
    assert voice.model_name() == "base.en"
    assert voice.autosend_enabled() is False
    monkeypatch.setenv("INSIGHTPILOT_VOICE_MODEL", "small.en")
    monkeypatch.setenv("INSIGHTPILOT_VOICE_AUTOSEND", "1")
    assert voice.model_name() == "small.en"
    assert voice.autosend_enabled() is True


# ---- quiet and silent recordings -------------------------------------

@pytest.mark.parametrize("raw", [". . .", "...", " . ", "?!", "-"])
def test_punctuation_only_output_counts_as_nothing_heard(raw):
    assert voice.clean_transcript(raw) == ""


def test_silent_recording_points_at_the_microphone_choice():
    transcriber = voice.Transcriber(prompt="", engine=FakeEngine(["hello"]))
    with pytest.raises(voice.VoiceError, match="wrong microphone"):
        voice.transcribe_wav(make_wav(1.0, amplitude=0.001), transcriber)
    assert transcriber._engine.calls == []  # the model is not even run


def test_quiet_recording_is_amplified_before_transcription():
    engine = FakeEngine(["Net sales by region"])
    transcriber = voice.Transcriber(prompt="", engine=engine)
    voice.transcribe_wav(make_wav(1.0, amplitude=0.03), transcriber)
    samples, _ = engine.calls[0]
    assert 0.85 < float(np.abs(samples).max()) <= 0.91


def test_loud_recording_is_left_alone():
    samples = voice.decode_wav(make_wav(1.0, amplitude=0.95))
    prepared, peak = voice.prepare_audio(samples)
    assert prepared is samples and peak > 0.9


def test_dots_from_the_model_are_reported_as_not_heard():
    transcriber = voice.Transcriber(prompt="", engine=FakeEngine([". . ."]))
    with pytest.raises(voice.VoiceError, match="couldn't hear"):
        voice.transcribe_wav(make_wav(1.0), transcriber)
