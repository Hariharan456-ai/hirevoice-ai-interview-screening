"""All settings live here. Change values (or set environment variables) - no code edits needed elsewhere."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

def _env(name, default):
    return os.environ.get(name, default)

# ---------- Local AI models (all run on your PC, no paid API) ----------
OLLAMA_URL = _env("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
OLLAMA_MODEL = _env("OLLAMA_MODEL", "qwen2.5:3b")      # try qwen2.5:7b for better grading if you have 16GB RAM
WHISPER_MODEL = _env("WHISPER_MODEL", "base.en")       # tiny.en (fastest) | base.en | small.en (most accurate)
WHISPER_DEVICE = _env("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = _env("WHISPER_COMPUTE_TYPE", "int8")
PIPER_VOICE = _env("PIPER_VOICE", "en_US-lessac-medium")   # neural text-to-speech voice
PIPER_MODEL_DIR = BASE_DIR / "models" / "piper"

# ---------- Folders / DB ----------
UPLOAD_FOLDER = DATA_DIR / "uploads"
RESUME_FOLDER = DATA_DIR / "resumes"
TRANSCRIPT_FOLDER = DATA_DIR / "transcripts"
REPORT_FOLDER = DATA_DIR / "reports"
EXPORT_FOLDER = DATA_DIR / "exports"
TTS_CACHE_FOLDER = DATA_DIR / "tts_cache"
DATABASE_PATH = DATA_DIR / "screening_v2.db"

# ---------- Interview behaviour ----------
DEFAULT_COUNTRY_CODE = _env("DEFAULT_COUNTRY_CODE", "91")   # used when a resume phone has 10 digits
NO_SPEECH_TIMEOUT_SEC = 45      # candidate silent this long -> question skipped
MAX_ANSWER_SEC = 120            # hard stop for one answer
SILENCE_END_SEC = 2.5           # this much silence after speaking ends the answer

# ---------- Response-time penalty (reading from ChatGPT/Google takes time) ----------
PENALTY_GRACE_SEC = 8           # no penalty if candidate starts speaking within this
PENALTY_PER_SEC = 1.0           # % of score lost per extra second
PENALTY_MAX_PCT = 40.0          # never remove more than this

# ---------- AI-generated-answer detection ----------
AI_SUSPECT_THRESHOLD = 65       # combined likelihood (0-100) at/above this -> shown RED (rank is kept)

# ---------- Scoring rubric (must add up to 100) ----------
RUBRIC = {
    "technical_knowledge": 30,
    "communication": 20,
    "problem_solving": 20,
    "role_relevance": 20,
    "completeness": 10,
}

# ---------- Human-friendly names for rubric keys (role-neutral) ----------
RUBRIC_LABELS = {
    "technical_knowledge": "Role knowledge & expertise",
    "communication": "Communication",
    "problem_solving": "Problem solving & judgement",
    "role_relevance": "Relevance to the role",
    "completeness": "Completeness & real examples",
}

# ---------- AI follow-up questions (from your role PDFs) ----------
FOLLOWUP_STEPS = 4              # max follow-ups after a strong "Yes / I have experience" answer
FOLLOWUP_MAX_QUESTIONS = 6      # max questions per interview that get follow-ups (keeps the call short)
ROLE_BANK_FILE = DATA_DIR / "role_question_banks.json"

# ---------- Optional Google Drive upload of the Q&A PDFs (free Google API, needs one-time setup - see README) ----------
GOOGLE_DIR = DATA_DIR / "google"
DRIVE_CREDENTIALS_FILE = GOOGLE_DIR / "credentials.json"   # OAuth "Desktop app" client you download from Google Cloud
DRIVE_TOKEN_FILE = GOOGLE_DIR / "token.json"               # created automatically after you click "Connect Google Drive"
DRIVE_FOLDER_NAME = "HireVoice Interview Reports"
DRIVE_ANYONE_WITH_LINK = False   # False = only your Google account can open the links (recommended: candidate data is private)

# ---------- HR login (change these, or change the password inside the app: Account page) ----------
HR_EMAIL = _env("HR_EMAIL", "hr@hirevoice.com")
HR_PASSWORD = _env("HR_PASSWORD", "Hr@12345")

# ---------- Human-like conversation engine ----------
INTERVIEW_MAX_MIN = 30          # hard time limit of one interview (minutes)
CLOSING_RESERVE_SEC = 60        # time kept free for the goodbye
MAX_FOLLOWUPS_PER_QUESTION = 3  # upper limit; the time budget may reduce it
STALE_MINUTES = 10              # no activity this long while "in progress" -> shown as "Stopped early"
LLM_FOLLOWUP_TIMEOUT = 25       # seconds to wait for the local LLM to write a follow-up (then a template is used)
OLLAMA_KEEP_ALIVE = "30m"       # keep the LLM in memory between turns (faster)

# ---------- Real phone calls (SIP) ----------
CALL_RING_TIMEOUT_SEC = 45      # give up if the student does not pick up
CALL_GAP_SEC = 8                # pause between two calls when "Call all" is used
DEFAULT_CALLER_NUMBER = "044-48135764"

ALLOWED_AUDIO = {"wav", "mp3", "m4a", "webm", "ogg", "mp4", "flac"}
ALLOWED_RESUME = {"pdf"}
MAX_UPLOAD_MB = 60
