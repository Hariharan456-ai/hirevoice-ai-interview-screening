"""Run ONCE (needs internet): downloads the Whisper speech model and the Piper neural voice, checks Ollama."""
import sys, urllib.request
from app import config

def main():
    print("1/3 Whisper speech-to-text model:", config.WHISPER_MODEL)
    try:
        from faster_whisper import WhisperModel
        WhisperModel(config.WHISPER_MODEL, device="cpu", compute_type="int8"); print("    OK")
    except Exception as e: print("    FAILED:", e)
    print("2/3 Piper voice:", config.PIPER_VOICE)
    name = config.PIPER_VOICE; lang, region = name.split("-")[0], name.split("-")[0].split("_")[0]
    spk, qual = name.split("-")[1], name.split("-")[2]
    base = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/{region}/{lang}/{spk}/{qual}/{name}"
    config.PIPER_MODEL_DIR.mkdir(parents=True, exist_ok=True)
    for ext in (".onnx", ".onnx.json"):
        dest = config.PIPER_MODEL_DIR / (name + ext)
        if dest.exists(): print("    already have", dest.name); continue
        try: urllib.request.urlretrieve(base + ext, dest); print("    downloaded", dest.name)
        except Exception as e: print("    FAILED", ext, e, "(browser voice will be used instead)")
    print("3/3 Ollama model:", config.OLLAMA_MODEL)
    try:
        import requests
        names = [m["name"] for m in requests.get(config.OLLAMA_URL.replace("/api/generate", "/api/tags"), timeout=4).json()["models"]]
        print("    OK" if any(n.startswith(config.OLLAMA_MODEL.split(":")[0]) for n in names) else f"    Not installed. Run:  ollama pull {config.OLLAMA_MODEL}")
    except Exception: print("    Ollama not running. Install from https://ollama.com then run: ollama pull", config.OLLAMA_MODEL)

if __name__ == "__main__": main()
