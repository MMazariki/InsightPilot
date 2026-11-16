"""Local speech-to-text for the chat box.

The browser records a question with the microphone button in the chat input
(``st.chat_input(accept_audio=True)``) and Streamlit hands it over as a WAV
file. This module turns that WAV into text on this computer with whisper.cpp
(``pywhispercpp``). Nothing is sent to a cloud service.

It has no Streamlit import so it can be unit-tested on its own.
"""

import io
import json
import os
import re
import threading
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "models"
MODEL_SCHEMA_FILE = ROOT / "evaluation" / "semantic_model_schema.json"
VALUE_CATALOG_FILE = ROOT / "evaluation" / "semantic_value_catalog.json"

DEFAULT_MODEL = "base.en"
SAMPLE_RATE = 16000
MIN_SECONDS = 0.4
MAX_SECONDS = 30.0
PROMPT_MAX_CHARS = 600

# Catalog columns whose values are worth teaching the recogniser.
PROMPT_VALUE_COLUMNS = (
    "FactSales[Channel]",
    "DimStore[StoreType]",
    "DimStore[Region]",
    "DimStore[City]",
    "DimProduct[Category]",
    "DimCustomer[CustomerSegment]",
    "FactSales[PaymentMethod]",
)

_NON_SPEECH = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_SPACES = re.compile(r"\s+")


class VoiceError(Exception):
    """A problem the user should be told about, in plain language."""


def is_available():
    """True if the speech-to-text package is installed."""

    try:
        import pywhispercpp.model  # noqa: F401
    except Exception:
        return False
    return True


def model_name():
    return os.environ.get("INSIGHTPILOT_VOICE_MODEL", "").strip() or DEFAULT_MODEL


def autosend_enabled():
    """INSIGHTPILOT_VOICE_AUTOSEND=1 runs the heard question immediately."""

    value = os.environ.get("INSIGHTPILOT_VOICE_AUTOSEND", "").strip().lower()
    return value in ("1", "true", "yes", "on")


def split_submission(value):
    """Return ``(text, audio_bytes)`` from an ``st.chat_input`` result.

    With ``accept_audio=True`` the widget returns a ``ChatInputValue`` (with
    ``.text`` and ``.audio``) even for typed questions; without it, a plain
    string. ``None`` means nothing was submitted.
    """

    if value is None:
        return "", None
    if isinstance(value, str):
        return value.strip(), None
    text = (getattr(value, "text", "") or "").strip()
    audio = getattr(value, "audio", None)
    data = audio.getvalue() if audio is not None else None
    return text, (data or None)


def decode_wav(data):
    """WAV bytes -> mono float32 samples at 16 kHz in [-1, 1]."""

    try:
        with wave.open(io.BytesIO(data)) as reader:
            channels = reader.getnchannels()
            width = reader.getsampwidth()
            rate = reader.getframerate()
            frames = reader.readframes(reader.getnframes())
    except (wave.Error, EOFError) as exc:
        raise VoiceError("The recording could not be read. Please try again.") from exc

    if width == 2:
        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    elif width == 4:
        samples = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
    elif width == 1:
        samples = (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise VoiceError("The recording format is not supported. Please try again.")

    if channels > 1:
        samples = samples[: len(samples) // channels * channels]
        samples = samples.reshape(-1, channels).mean(axis=1)

    if rate != SAMPLE_RATE and len(samples):
        target = int(round(len(samples) * SAMPLE_RATE / rate))
        positions = np.linspace(0, len(samples) - 1, num=max(target, 1))
        samples = np.interp(positions, np.arange(len(samples)), samples)

    samples = np.ascontiguousarray(samples, dtype=np.float32)
    seconds = len(samples) / SAMPLE_RATE
    if seconds < MIN_SECONDS:
        raise VoiceError(
            "That recording was too short. Speak a little longer and try again."
        )
    if seconds > MAX_SECONDS:
        raise VoiceError(
            f"That recording was longer than {int(MAX_SECONDS)} seconds. "
            "Please ask one short question."
        )
    return samples


def vocabulary_prompt(schema_file=MODEL_SCHEMA_FILE, catalog_file=VALUE_CATALOG_FILE):
    """A short hint listing measure names and common values.

    whisper.cpp uses it to spell domain words correctly ("Riyadh", "Click &
    Collect", "Net Sales"). Missing files just give a shorter prompt.
    """

    terms = []
    try:
        model = json.loads(Path(schema_file).read_text(encoding="utf-8-sig"))
        for table in model.get("Tables", []):
            terms.extend(m["Name"] for m in table.get("Measures", []))
    except (OSError, ValueError, KeyError):
        pass
    try:
        catalog = json.loads(Path(catalog_file).read_text(encoding="utf-8-sig"))
        columns = catalog.get("columns", {})
        for column in PROMPT_VALUE_COLUMNS:
            terms.extend(str(v) for v in columns.get(column, []))
    except (OSError, ValueError):
        pass

    prompt = "Retail sales questions."
    seen = set()
    for term in terms:
        if term in seen:
            continue
        candidate = f"{prompt} {term},"
        if len(candidate) > PROMPT_MAX_CHARS:
            break
        seen.add(term)
        prompt = candidate
    return prompt.rstrip(",") + ("." if seen else "")


def clean_transcript(text):
    """Drop non-speech markers such as ``[BLANK_AUDIO]`` and tidy spaces."""

    text = _NON_SPEECH.sub(" ", text or "")
    return _SPACES.sub(" ", text).strip()


class Transcriber:
    """Loads the whisper.cpp model once and transcribes one clip at a time."""

    def __init__(self, name=None, models_dir=MODELS_DIR, prompt=None, engine=None):
        self.name = name or model_name()
        self.models_dir = Path(models_dir)
        self.prompt = vocabulary_prompt() if prompt is None else prompt
        self._engine = engine          # injectable for tests
        self._lock = threading.Lock()

    def _load(self):
        if self._engine is None:
            try:
                from pywhispercpp.model import Model
            except Exception as exc:
                raise VoiceError(
                    "Voice input is not installed on this computer."
                ) from exc
            self.models_dir.mkdir(parents=True, exist_ok=True)
            try:
                # Downloads ggml-<name>.bin into models/ on first use (~141 MB
                # for base.en), then works offline.
                self._engine = Model(
                    self.name,
                    models_dir=str(self.models_dir),
                    n_threads=max(1, min(6, os.cpu_count() or 4)),
                    print_realtime=False,
                    print_progress=False,
                    redirect_whispercpp_logs_to=None,
                )
            except Exception as exc:
                raise VoiceError(
                    "The voice model could not be loaded. Check the internet "
                    "connection for the first download, then try again."
                ) from exc
        return self._engine

    def transcribe(self, samples):
        with self._lock:
            engine = self._load()
            try:
                segments = engine.transcribe(
                    samples, language="en", initial_prompt=self.prompt)
            except Exception as exc:
                raise VoiceError("The recording could not be transcribed.") from exc
        return clean_transcript(" ".join(s.text for s in segments))


def transcribe_wav(data, transcriber):
    """WAV bytes -> question text. Raises ``VoiceError`` with a user message."""

    text = transcriber.transcribe(decode_wav(data))
    if not text:
        raise VoiceError(
            "I couldn't hear a question in that recording. Check the "
            "microphone and try again."
        )
    return text
