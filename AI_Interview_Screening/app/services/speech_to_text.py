"""Speech-to-text with faster-whisper (pre-trained Whisper, CPU int8). Model is loaded ONCE."""
import threading
from .. import config

_model = None
_lock = threading.Lock()
_infer_lock = threading.Lock()

class STTError(Exception): pass

def _get_model():
    global _model
    with _lock:
        if _model is None:
            try:
                from faster_whisper import WhisperModel
                _model = WhisperModel(config.WHISPER_MODEL, device=config.WHISPER_DEVICE,
                                      compute_type=config.WHISPER_COMPUTE_TYPE)
            except Exception as e:
                raise STTError(f"Could not load Whisper model '{config.WHISPER_MODEL}': {e}. "
                               "Run: pip install faster-whisper (first run needs internet to download the model).")
        return _model

def transcribe(audio_path):
    """Returns (text, duration_seconds)."""
    model = _get_model()
    try:
        with _infer_lock:
          segments, info = model.transcribe(str(audio_path), language="en", beam_size=1,
                                          vad_filter=True, condition_on_previous_text=False)
          text = " ".join(s.text.strip() for s in segments).strip()
        return text, float(getattr(info, "duration", 0) or 0)
    except Exception as e:
        raise STTError(f"Transcription failed: {e}")
