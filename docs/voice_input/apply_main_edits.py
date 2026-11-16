"""Apply the voice-input edits to src/main.py (see docs/VOICE_INPUT_PLAN.md).

Run from the project root:

    .venv\\Scripts\\python.exe docs\\voice_input\\apply_main_edits.py

Every edit must match exactly once, otherwise nothing is written and the
script says which edit failed. It refuses to run twice.
"""

import sys
from pathlib import Path

TARGET = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("src/main.py")

EDITS = [
    # 1. import the voice module
    (
        "from powerbi_embed_component import powerbi_embed_component\n",
        "from powerbi_embed_component import powerbi_embed_component\n"
        "from src import voice\n",
    ),
    # 2. session state, feature switch and the cached speech model
    (
        'if "queued_prompt" not in st.session_state:\n'
        "    st.session_state.queued_prompt = None\n",
        'if "queued_prompt" not in st.session_state:\n'
        "    st.session_state.queued_prompt = None\n"
        'if "voice_draft" not in st.session_state:\n'
        "    st.session_state.voice_draft = None\n"
        'if "voice_notice" not in st.session_state:\n'
        "    st.session_state.voice_notice = None\n"
        "\n"
        "# The microphone button is shown only when speech-to-text is installed.\n"
        "VOICE_ENABLED = voice.is_available()\n"
        "\n"
        "\n"
        "@st.cache_resource(show_spinner=False)\n"
        "def get_transcriber():\n"
        "    return voice.Transcriber()\n",
    ),
    # 3. the chat input: microphone button, transcription, review-then-send
    (
        "    prompt = st.chat_input(\n"
        '        "Ask a business question...",\n'
        '        key="agent_chat_input",\n'
        "    )\n",
        "    notice_slot = st.empty()\n"
        "    if st.session_state.voice_notice:\n"
        "        notice = st.session_state.voice_notice\n"
        "        st.session_state.voice_notice = None\n"
        '        if notice["kind"] == "error":\n'
        '            notice_slot.warning(notice["text"], icon=":material/mic_off:")\n'
        "        else:\n"
        '            notice_slot.info(notice["text"], icon=":material/mic:")\n'
        "\n"
        "    # A heard question waiting for review goes into the input box. This\n"
        "    # must be set before the widget is created in this run.\n"
        "    if st.session_state.voice_draft:\n"
        '        st.session_state["agent_chat_input"] = st.session_state.voice_draft\n'
        "        st.session_state.voice_draft = None\n"
        "\n"
        "    if VOICE_ENABLED:\n"
        "        submission = st.chat_input(\n"
        '            "Ask a business question, or use the microphone...",\n'
        '            key="agent_chat_input",\n'
        "            accept_audio=True,\n"
        "            audio_sample_rate=voice.SAMPLE_RATE,\n"
        "        )\n"
        "    else:\n"
        "        submission = st.chat_input(\n"
        '            "Ask a business question...",\n'
        '            key="agent_chat_input",\n'
        "        )\n"
        "\n"
        "    # With the microphone on, even typed questions arrive as an object\n"
        "    # with .text and .audio, not as a plain string.\n"
        "    typed_text, audio_bytes = voice.split_submission(submission)\n"
        "    prompt = typed_text or None\n"
        "\n"
        "    if audio_bytes:\n"
        '        heard = ""\n'
        '        with chat_container, st.spinner("Transcribing your voice..."):\n'
        "            try:\n"
        "                heard = voice.transcribe_wav(audio_bytes, get_transcriber())\n"
        "            except voice.VoiceError as exc:\n"
        "                st.session_state.voice_notice = {\n"
        '                    "kind": "error", "text": str(exc)}\n'
        '        combined = " ".join(part for part in (typed_text, heard) if part)\n'
        "        if heard and voice.autosend_enabled():\n"
        "            prompt = combined\n"
        "        else:\n"
        "            if heard:\n"
        "                st.session_state.voice_notice = {\n"
        '                    "kind": "info",\n'
        '                    "text": "Check the question in the box, then press "\n'
        '                            "Enter to send it.",\n'
        "                }\n"
        "            st.session_state.voice_draft = combined or None\n"
        "            st.rerun()\n"
        "\n",
    ),
]


def main():
    source = TARGET.read_text(encoding="utf-8")
    if "from src import voice" in source:
        sys.exit(f"{TARGET} already has the voice edits; nothing to do.")
    for number, (old, new) in enumerate(EDITS, start=1):
        count = source.count(old)
        if count != 1:
            sys.exit(
                f"Edit {number} expected exactly 1 match in {TARGET}, found "
                f"{count}. Nothing was written. Open the plan, section "
                '"If the edit script fails".'
            )
        source = source.replace(old, new)
    TARGET.write_text(source, encoding="utf-8", newline="")
    print(f"Applied {len(EDITS)} edits to {TARGET}.")


if __name__ == "__main__":
    main()
