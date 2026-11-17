"""A "Read aloud" button that speaks a text with the browser's own voices.

Uses the Web Speech API (``speechSynthesis``): nothing to install, works
offline. Local voices (the Windows voices) are preferred, so the text stays
on this computer; a network voice is used only if the browser has no local
English voice. Long texts are spoken sentence by sentence (one long
utterance stops after about 15 seconds in Chromium browsers).
"""

import json
import re

import streamlit.components.v1 as components


def speakable(text):
    """Text without citation marks and Markdown, for speaking."""

    text = re.sub(r"\[\d+\]", "", str(text or ""))
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"[*_#>]+", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    text = re.sub(r"\s*\n\s*", ". ", text)
    text = re.sub(r"\.\s*\.", ".", text)
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s+([.,;:!?])", r"\1", text)


def sentences(text):
    parts = re.split(r"(?<=[.!?;:])\s+", speakable(text))
    return [p for p in parts if p.strip()]


_TEMPLATE = """
<style>
  body { margin: 0; font-family: "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif; }
  button {
    display: inline-flex; align-items: center; gap: 6px;
    background: #fff; color: #2251D1; border: 1px solid #D0D5DD; border-radius: 8px;
    padding: 5px 12px; font: inherit; font-size: 13px; font-weight: 600; cursor: pointer;
  }
  button:hover { border-color: #2251D1; }
  button.on { background: #EEF2FF; border-color: #2251D1; }
  button:disabled { color: #98A2B3; cursor: not-allowed; }
  .note { font-size: 12px; color: #667085; margin-left: 8px; }
</style>
<button id="b" type="button"><span id="i">&#128266;</span><span id="t">Read aloud</span></button>
<span class="note" id="n"></span>
<script>
(function () {
  const parts = __PARTS__;
  const id = __ID__;
  let synth = null;
  try { synth = window.parent.speechSynthesis; } catch (e) {}
  synth = synth || window.speechSynthesis;
  const button = document.getElementById("b");
  const label = document.getElementById("t");
  const icon = document.getElementById("i");
  const note = document.getElementById("n");
  if (!synth || !window.SpeechSynthesisUtterance) {
    button.disabled = true; note.textContent = "This browser cannot read aloud.";
    return;
  }
  // One answer speaks at a time across the page: remember who is speaking.
  const owner = () => { try { return window.parent.__ipSpeaking; } catch (e) { return null; } };
  const setOwner = (v) => { try { window.parent.__ipSpeaking = v; } catch (e) {} };

  function pickVoice() {
    const voices = synth.getVoices().filter(v => /^en(-|_|$)/i.test(v.lang));
    return voices.find(v => v.localService && /natural|aria|jenny|guy/i.test(v.name))
        || voices.find(v => v.localService && v.default)
        || voices.find(v => v.localService)
        || voices[0] || null;
  }
  function show(on) {
    button.classList.toggle("on", on);
    label.textContent = on ? "Stop" : "Read aloud";
    icon.innerHTML = on ? "&#9632;" : "&#128266;";
  }
  function stop() { synth.cancel(); setOwner(null); show(false); }
  function speak() {
    synth.cancel();
    setOwner(id);
    show(true);
    const voice = pickVoice();
    parts.forEach((text, index) => {
      const u = new SpeechSynthesisUtterance(text);
      if (voice) { u.voice = voice; u.lang = voice.lang; } else { u.lang = "en-US"; }
      u.rate = __RATE__;
      if (index === parts.length - 1) {
        u.onend = () => { if (owner() === id) { setOwner(null); } show(false); };
      }
      u.onerror = () => show(false);
      synth.speak(u);
    });
  }
  button.addEventListener("click", () => {
    if (owner() === id && synth.speaking) { stop(); } else { speak(); }
  });
  // Another answer started speaking: reset this button.
  setInterval(() => {
    if (button.classList.contains("on") && (owner() !== id || !synth.speaking)) show(false);
  }, 500);
  if (synth.onvoiceschanged !== undefined) synth.onvoiceschanged = () => {};
})();
</script>
"""


def read_aloud_button(text, key, rate=1.0):
    """Draw the button for ``text``. ``key`` identifies the answer; ``rate``
    is the speaking speed (1.0 = normal)."""

    parts = sentences(text)
    if not parts:
        return
    html = (_TEMPLATE.replace("__PARTS__", json.dumps(parts))
            .replace("__ID__", json.dumps(str(key)))
            .replace("__RATE__", json.dumps(min(max(float(rate), 0.5), 2.0))))
    components.html(html, height=40)
