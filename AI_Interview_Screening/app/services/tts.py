"""Text-to-speech using Piper (pre-trained neural voice, runs on CPU). If Piper is not installed the
interview page automatically falls back to the browser's built-in speech voice."""
import hashlib, os, subprocess, wave
from pathlib import Path
from .. import config

_voice = None
_failed = False

def _load():
    global _voice, _failed
    if _voice or _failed: return _voice
    try:
        from piper import PiperVoice
        model = Path(config.PIPER_MODEL_DIR) / f"{config.PIPER_VOICE}.onnx"
        if not model.exists():
            raise FileNotFoundError(f"{model} missing - run: python download_models.py")
        _voice = PiperVoice.load(str(model))
    except Exception as e:
        print("[TTS] Piper unavailable, browser voice will be used:", e)
        _failed = True
    return _voice

def synthesize(text):
    """Returns path to a cached WAV, or None if Piper is unavailable."""
    key = hashlib.sha1((config.PIPER_VOICE + text).encode()).hexdigest()[:20]
    out = Path(config.TTS_CACHE_FOLDER) / f"{key}.wav"
    if out.exists(): return out
    voice = _load()
    if not voice: return _sapi(text, out)
    try:
        with wave.open(str(out), "wb") as wf:
            if hasattr(voice, "synthesize_wav"):
                voice.synthesize_wav(text, wf)
            else:
                voice.synthesize(text, wf)
        return out
    except Exception as e:
        print("[TTS] synthesis failed:", e)
        out.unlink(missing_ok=True)
        return None


def _sapi(text, out):
    """Fallback voice: Windows' built-in speech (System.Speech). Used when Piper is not installed."""
    if os.name != "nt": return None
    tmp = Path(str(out) + ".tmp.wav")
    script = ("Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "$s.SetOutputToWaveFile($env:TTS_OUT); $s.Speak($env:TTS_TEXT); $s.Dispose()")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=90, capture_output=True,
                       env={**os.environ, "TTS_TEXT": text, "TTS_OUT": str(tmp)})
        if tmp.exists() and tmp.stat().st_size > 1000:
            tmp.replace(out); return out
    except Exception as e:
        print("[TTS] Windows voice failed:", e)
    tmp.unlink(missing_ok=True)
    return None
