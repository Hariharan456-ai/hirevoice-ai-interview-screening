"""Extract name / phone / email / education from a resume PDF (text PDFs). Uses regex + heuristics,
and (optionally) the local Ollama model as a fallback for the name."""
import re
from pathlib import Path
from pypdf import PdfReader
from .. import config

PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{2,5}\)?[\s-]?)?\d{3,5}[\s-]?\d{4,6}(?!\d)")
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
BAD_NAME_WORDS = {"resume", "curriculum", "vitae", "cv", "profile", "contact", "objective", "summary", "email",
                  "phone", "mobile", "address", "education", "skills", "experience", "projects", "declaration",
                  "career", "personal", "details", "linkedin", "github", "engineer", "developer", "student"}
DEGREE_PATTERNS = [r"Ph\.?\s?D\.?", r"M\.?\s?Tech", r"M\.?\s?Sc\.?", r"M\.?\s?P\.?\s?Ed\.?", r"MBA", r"M\.?\s?C\.?\s?A", r"M\.?\s?Com", r"M\.?\s?E\.?",
                   r"B\.?\s?Tech", r"B\.?\s?P\.?\s?Ed\.?", r"B\.?\s?Sc\.?", r"B\.?\s?C\.?\s?A", r"B\.?\s?Com", r"B\.?\s?E\.?", r"B\.?\s?A\.?", r"Diploma"]
SPECS = ["Sports Science", "Physical Education", "Exercise Science", "Library Science", "Psychology", "Computer Science", "Information Technology", "Electronics and Communication", "Electronics",
         "Electrical", "Mechanical", "Civil", "Artificial Intelligence", "Data Science", "Machine Learning",
         "Cyber Security", "Commerce", "Business Administration", "Chemical", "Biotechnology"]

_ocr_engine = None
OCR_USED = {"flag": False}

def _ocr(pdf_path):
    """Reads scanned/photo resumes with a local OCR model (RapidOCR, runs on CPU). Optional install."""
    global _ocr_engine
    try:
        import numpy as np, pypdfium2 as pdfium
        from rapidocr_onnxruntime import RapidOCR
    except Exception:
        return ""
    try:
        _ocr_engine = _ocr_engine or RapidOCR()
        pdf = pdfium.PdfDocument(str(pdf_path)); lines = []
        for i in range(min(2, len(pdf))):
            arr = np.array(pdf[i].render(scale=2).to_pil().convert("RGB"))
            res, _ = _ocr_engine(arr)
            if res: lines += [r[1] for r in res]
        return "\n".join(lines)
    except Exception as e:
        print("[OCR] failed:", e); return ""

def extract_text(pdf_path):
    reader = PdfReader(str(pdf_path))
    text = "\n".join((p.extract_text() or "") for p in reader.pages[:4])
    OCR_USED["flag"] = False
    if len(text.strip()) < 40:                      # scanned image PDF -> OCR
        t2 = _ocr(pdf_path)
        if t2.strip(): OCR_USED["flag"] = True; return t2
    return text

def _clean_phone(raw):
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 12 and digits.startswith(config.DEFAULT_COUNTRY_CODE): return "+" + digits
    if len(digits) == 10: return digits
    if 11 <= len(digits) <= 13: return "+" + digits
    return None

def find_phone(text):
    labelled = re.findall(r"(?:phone|mobile|mob|contact|cell|tel)[^\d+]{0,12}([+\d][\d\s().-]{8,18})", text, re.I)
    for cand in labelled + [m.group(0) for m in PHONE_RE.finditer(text)]:
        p = _clean_phone(cand)
        if p and not re.fullmatch(r"(19|20)\d{2}\d*", re.sub(r"\D", "", cand)[:4] + "x"):  # skip year ranges
            return p
    return ""

def find_name(text, email, filename):
    lines = [l.strip() for l in text.splitlines() if l.strip()][:15]
    for l in lines:
        if re.search(r"[\d@:/|]", l): continue
        words = re.findall(r"[A-Za-z][A-Za-z.'-]*", l)
        if not 1 <= len(words) <= 4 or len(" ".join(words)) < 3: continue
        if any(w.lower().strip(".") in BAD_NAME_WORDS for w in words): continue
        if l.isupper() or all(w[0].isupper() for w in words):
            return " ".join(w.capitalize() if l.isupper() else w for w in words)
    name = _llm_name(text)
    if name: return name
    if email:
        local = re.sub(r"\d+", " ", email.split("@")[0])
        parts = [p for p in re.split(r"[._-]+|\s+", local) if p]
        if parts: return " ".join(p.capitalize() for p in parts)
    return re.sub(r"[_-]+", " ", Path(filename).stem).title()

def _llm_name(text):
    try:
        import requests
        r = requests.post(config.OLLAMA_URL, json={"model": config.OLLAMA_MODEL, "stream": False,
            "prompt": "Extract only the candidate's full name from this resume text. Reply with the name only.\n\n" + text[:800],
            "options": {"temperature": 0, "num_predict": 12}}, timeout=25)
        n = r.json().get("response", "").strip().split("\n")[0]
        return n if 1 <= len(n.split()) <= 4 and not re.search(r"\d", n) else ""
    except Exception:
        return ""

def parse_resume(pdf_path, filename=""):
    text = extract_text(pdf_path)
    emails = EMAIL_RE.findall(text)
    email = emails[0] if emails else ""
    d = {"ocr_used": OCR_USED["flag"], "text_found": bool(text.strip()), "email": email, "phone": find_phone(text)}
    d["name"] = find_name(text, email, filename or Path(pdf_path).name)
    d["degree"] = ""
    for pat in DEGREE_PATTERNS:                      # highest degree first, whole words only
        m = re.search(r"(?<![A-Za-z])" + pat + r"(?![A-Za-z])", text)
        if m: d["degree"] = re.sub(r"\s+", " ", m.group(0)).strip(); break
    d["specialization"] = next((s for s in SPECS if s.lower() in text.lower()), "")
    col = re.search(r"-\s*([^-\n(]*\b(?:University|College|Institute|Institution)\b[^-\n(]*)", text, re.I) or \
          re.search(r"^(.*\b(?:University|College|Institute|Institution|School of)\b.*)$", text, re.I | re.M)
    d["college"] = col.group(1).strip()[:90] if col else ""
    yr = re.findall(r"\b(20[12]\d)\b", text)
    d["graduation_year"] = max(yr) if yr else ""
    return d
