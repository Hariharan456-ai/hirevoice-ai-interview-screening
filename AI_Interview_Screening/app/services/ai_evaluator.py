"""Answer grading + AI-generated-answer detection using a local LLM (Ollama) plus a
statistical 'style' detector. No paid APIs."""
import json, re, statistics
from pathlib import Path
import requests
from .. import config

class EvaluatorError(Exception): pass

FEW_SHOT_FILE = config.DATA_DIR / "calibration_examples.json"

def _few_shot():
    try:
        ex = json.loads(FEW_SHOT_FILE.read_text(encoding="utf-8"))[:3]
    except Exception:
        return ""
    out = ["Calibration examples from HR (follow this strictness):"]
    for e in ex:
        out.append(f"Q: {e['question']}\nA: {e['answer']}\nGraded JSON: {json.dumps(e['scores'])}")
    return "\n\n".join(out) + "\n\n"

def build_prompt(question, transcript, role_name="", answer_key=""):
    rubric = ", ".join(f'"{k}" (0-{v})' for k, v in config.RUBRIC.items())
    return f"""You are a strict, fair interview evaluator for a job screening. Grade ONLY the job-relevant content of the answer.
Never consider or infer gender, religion, caste, race, ethnicity, disability, age, accent, appearance or background.
Do not invent facts. Base every point on the transcript.

{_few_shot()}Job role being interviewed for: {role_name or 'general'}
Rubric meaning: technical_knowledge = depth of role-specific knowledge/experience; communication = clarity and structure;
problem_solving = judgement and how problems were handled; role_relevance = fit to this role; completeness = covers the question and gives a REAL example.
A bare "Yes" or generic claim without a concrete example, personal role, action and result must score LOW on technical_knowledge and completeness.
The text is a conversation: lines starting "AI (question)" / "AI (follow-up)" are the interviewer; every other line is the candidate's spoken reply.
Grade ONLY the candidate's replies, using the follow-up replies as extra evidence of real experience.
{('What a strong answer typically includes: ' + answer_key) if answer_key else ''}
Interview question: {question}
Candidate answer (speech transcript): {transcript}

Return ONLY valid JSON with integer fields: {rubric}, "total_score" (sum, 0-100),
"strengths" (list of short strings), "weaknesses" (list), "evidence" (list of short quotes/paraphrases from the answer),
"summary" (one sentence), "ai_generated_likelihood" (0-100: how likely this text was produced by an AI chatbot rather than
spoken naturally - consider over-polished structure, generic buzzwords, no personal detail), "ai_reasons" (list)."""

def _extract_json(text):
    try: return json.loads(text)
    except Exception: pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        s = m.group(0)
        for cand in (s, re.sub(r",\s*([}\]])", r"\1", s)):
            try: return json.loads(cand)
            except Exception: continue
    raise EvaluatorError("The local AI returned invalid JSON. The transcript was saved; click Re-evaluate.")

def call_llm(question, transcript, role_name="", answer_key=""):
    try:
        r = requests.post(config.OLLAMA_URL, json={"model": config.OLLAMA_MODEL, "prompt": build_prompt(question, transcript, role_name, answer_key),
                          "stream": False, "format": "json", "keep_alive": config.OLLAMA_KEEP_ALIVE, "options": {"temperature": 0.1, "num_predict": 500}}, timeout=240)
    except requests.exceptions.RequestException:
        raise EvaluatorError("Local AI model is not running. Start Ollama and make sure the selected model is installed.")
    if r.status_code != 200:
        raise EvaluatorError("Local AI model is not running. Start Ollama and make sure the selected model is installed. "
                             f"(Ollama said: {r.text[:120]})")
    return _extract_json(r.json().get("response", ""))

# ---------------- statistical AI-style detector ----------------
BUZZ = ["furthermore", "moreover", "additionally", "in conclusion", "in summary", "delve", "leverage", "robust",
        "multifaceted", "tapestry", "crucial", "pivotal", "seamless", "holistic", "foster", "comprehensive",
        "it is important to note", "stakeholders", "synergy", "paradigm", "utilize", "facilitate", "streamline"]
FILLERS = ["um", "uh", "hmm", "you know", "i mean", "like", "kind of", "sort of", "actually", "basically", "so yeah"]

def heuristic_ai_score(transcript, response_time=None, duration=None):
    """Returns (0-100, reasons). Heuristic signal only - never proof."""
    t = transcript.lower()
    words = re.findall(r"[a-z']+", t)
    n = len(words)
    if n < 25: return 5.0, ["answer too short to judge"]
    score, why = 0.0, []
    buzz = sum(t.count(b) for b in BUZZ)
    if buzz >= 2: score += min(30, buzz * 8); why.append(f"{buzz} formal/AI-typical phrases")
    fill = sum(len(re.findall(r"\b" + f + r"\b", t)) for f in FILLERS)
    rate = fill / n * 100
    if n >= 60 and rate < 0.5: score += 20; why.append("no natural fillers / hesitations in a long answer")
    sents = [s for s in re.split(r"[.!?]+", transcript) if len(s.split()) > 2]
    if len(sents) >= 5:
        lens = [len(s.split()) for s in sents]
        cv = statistics.pstdev(lens) / (statistics.mean(lens) or 1)
        if cv < 0.35: score += 15; why.append("very uniform sentence length")
    if re.search(r"\b(firstly|secondly|thirdly|first,|second,|third,|finally,)", t): score += 12; why.append("enumerated textbook structure")
    if not re.search(r"\b(i|my|me|we|our)\b", t) and n > 50: score += 10; why.append("no personal first-person detail")
    if n >= 80 and response_time is not None and response_time > config.PENALTY_GRACE_SEC * 1.5:
        score += 18; why.append(f"long pause ({response_time:.0f}s) followed by a polished answer")
    if duration and n / max(duration, 1) * 60 > 190: score += 8; why.append("unusually fast, even delivery (reading pace)")
    return float(min(100, score)), why

def penalty_for(response_time):
    if response_time is None: return 0.0
    extra = max(0.0, response_time - config.PENALTY_GRACE_SEC)
    return min(config.PENALTY_MAX_PCT, extra * config.PENALTY_PER_SEC)

def evaluate_answer(question, transcript, response_time=None, duration=None, max_score=100, role_name="", answer_key="", candidate_text=""):
    """Full evaluation -> dict ready to store."""
    own = candidate_text or transcript          # only the candidate's own words are judged for AI-style
    words = len(own.split())
    if words < 3:
        ev = {k: 0 for k in config.RUBRIC}
        ev.update(total_score=0, strengths=[], weaknesses=["No answer / not audible"], evidence=[],
                  summary="No usable answer was given.", ai_generated_likelihood=0, ai_reasons=[])
        llm_ai = 0
    else:
        ev = call_llm(question, transcript, role_name, answer_key)
        for k, mx in config.RUBRIC.items():
            ev[k] = max(0, min(mx, int(float(ev.get(k, 0) or 0))))
        ev["total_score"] = sum(ev[k] for k in config.RUBRIC)         # recomputed - never trust LLM arithmetic
        llm_ai = float(ev.get("ai_generated_likelihood", 0) or 0)
    h, why = heuristic_ai_score(own, response_time, duration)
    combined = round(0.5 * min(100, max(0, llm_ai)) + 0.5 * h, 1)
    pen = penalty_for(response_time)
    raw = ev["total_score"] / 100.0 * max_score
    ev.update(ai_likelihood_combined=combined, ai_heuristic=h, ai_heuristic_reasons=why,
              penalty_pct=round(pen, 1), response_time=response_time)
    return {"evaluation": ev, "raw_score": round(raw, 2), "penalty_pct": round(pen, 1),
            "score": round(raw * (1 - pen / 100.0), 2), "ai_likelihood": combined,
            "ai_suspected": 1 if combined >= config.AI_SUSPECT_THRESHOLD else 0}


def generate_answer_key(question, role_name):
    """One-time: ask the local LLM what a strong answer to this question should contain (used as grading guidance)."""
    prompt = (f"Job role: {role_name}. Interview question: {question}\n"
              "In 2 short sentences, list the specific things a strong, genuine answer would include "
              "(concrete experience, tools/methods, measurable result). No preamble.")
    r = requests.post(config.OLLAMA_URL, json={"model": config.OLLAMA_MODEL, "prompt": prompt, "stream": False,
                      "options": {"temperature": 0.2, "num_predict": 120}}, timeout=120)
    return r.json().get("response", "").strip()
