"""Human-like, time-boxed interview engine (runs on the server, one conversation per candidate).

Flow:  greeting -> small talk -> [ question -> candidate reply -> (0..3 smart follow-ups) -> "let's move on" ] x N -> thank-you.
* Follow-ups are written by the local LLM from what the candidate JUST said (e.g. picks ONE of the tools they mentioned at
  random). If the LLM is slow/unavailable a template follow-up is used.
* A time budget (INTERVIEW_MAX_MIN, default 30 min) decides how many follow-ups are affordable.
* Every spoken line (AI and candidate) is stored in the `turns` table -> full labelled transcript + audio.
"""
import json, random, re, threading, time, traceback, uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import requests
from .. import database as db, config
from . import speech_to_text, role_banks, pipeline

_pool = ThreadPoolExecutor(max_workers=2)
_jobs = {}
_jobs_lock = threading.Lock()

ACKS = ["Thank you.", "Okay, I see.", "Alright, thanks for sharing that.", "Got it.", "That's helpful, thank you.",
        "Understood.", "Mm-hmm, okay.", "Okay, great."]
SMALLTALK_ACKS = ["Great, thank you.", "Good, thanks.", "Okay, thank you."]
TRANSITIONS = ["Okay, let's move to the next question.", "Alright, let's move on.", "Okay, next question.",
               "Good. Let's move ahead.", "Alright, moving on to the next one."]
TOPIC_TRANSITIONS = ["Now let's talk about {cat}.", "Okay, let's move to the next topic: {cat}.",
                     "Alright, now a few questions on {cat}."]
NUDGES = ["Could you tell me a little more about that?", "Can you explain that with a specific example?",
          "Could you add some more detail?"]
GENERIC_FU = ["Can you tell me more about your own role in that?", "What was the main challenge you faced there?",
              "What result did you achieve in the end?", "What did you learn from that experience?",
              "Why did you choose that approach?"]
ITEM_FU = ["You mentioned {x}. How did you use it in practice?", "Why did you choose {x} for this?",
           "What was the biggest challenge you faced while working with {x}?",
           "Can you walk me through how {x} helped you get the result?", "How did {x} make the work easier or better?"]
FOCUS_HINTS = ["a specific detail they mentioned", "what exactly their own personal role was", "the biggest challenge in what they described",
               "the result or outcome they achieved", "why they chose that approach", "a concrete example with numbers"]
NO_HEAR = ["I couldn't hear an answer, but that's okay.", "I didn't catch that, no problem.", "That's alright, we can skip that one."]

STOPWORDS = {"i", "we", "the", "a", "an", "it", "this", "that", "my", "our", "and", "or", "with", "for", "to", "of", "in", "on", "at",
             "by", "as", "is", "was", "were", "be", "have", "had", "has", "also", "some", "many", "all", "using", "used", "other",
             "such", "like", "etc", "yes", "no", "so", "well", "okay", "sure", "because", "however", "but", "then", "there",
             "thank", "thanks", "hello", "hi", "when", "what", "how", "why", "which", "who", "one", "two", "three", "they", "he", "she"}


# ------------------------------------------------------------------ small helpers
def _now(): return datetime.utcnow().isoformat(timespec="seconds")

def get_state(cid):
    r = db.query("SELECT state_json FROM interview_state WHERE candidate_id=?", (cid,), one=True)
    return json.loads(r["state_json"]) if r else None

def save_state(cid, st):
    db.execute("""INSERT INTO interview_state(candidate_id,state_json,updated_at) VALUES(?,?,?)
                  ON CONFLICT(candidate_id) DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at""",
               (cid, json.dumps(st), _now()))

def _turn(cid, speaker, kind, text, qid=None, audio=None, rt=None, dur=None):
    db.execute("UPDATE candidates SET last_activity=? WHERE id=?", (_now(), cid))
    return db.execute("INSERT INTO turns(candidate_id,question_id,speaker,kind,text,audio_file,response_time,duration) VALUES(?,?,?,?,?,?,?,?)",
                      (cid, qid, speaker, kind, text, audio, rt, dur))

def _cand(cid): return db.query("SELECT * FROM candidates WHERE id=?", (cid,), one=True)
def _first(name): return ((name or "").strip().split() or ["there"])[0]
def _elapsed(st): return time.time() - st["started"]

def _msg(st, tid, text, kind, done=False):
    listen = {"greeting": {"noSpeech": 20, "maxAns": 25},
              "question": {"noSpeech": config.NO_SPEECH_TIMEOUT_SEC, "maxAns": config.MAX_ANSWER_SEC},
              "followup": {"noSpeech": 30, "maxAns": 90}}.get(kind)
    return {"id": tid, "text": text, "kind": kind, "done": done, "listen": None if done else listen,
            "progress": {"q": min(st["qpos"] + 1, len(st["plan"])), "total": len(st["plan"])},
            "elapsed_min": round(_elapsed(st) / 60, 1), "max_min": config.INTERVIEW_MAX_MIN}


# ------------------------------------------------------------------ follow-up intelligence
def extract_items(text):
    """Tools / methods / names the candidate mentioned (used to ask about ONE of them at random)."""
    items = []
    for m in re.finditer(r"(?:\busing\b|\bused\b|\btools?\s+(?:like|such as|were|are|include[sd]?)|\bsuch as\b|\bincluding\b|"
                         r"\bsoftware\b|\bplatforms?\b|\bsystems?\b)\s+([^.;?!\n]{2,90})", text, re.I):
        for part in re.split(r",|\band\b|&|/|\bor\b", m.group(1)):
            p = re.sub(r"^(the|a|an|our|my|some)\s+", "", part.strip(" ."), flags=re.I)
            w = p.split()
            if 1 <= len(w) <= 3 and w[0].lower() not in STOPWORDS and len(p) > 1: items.append(p)
    for m in re.finditer(r"\b[A-Z][A-Za-z0-9+#\-]{1,}\b", text):
        w = m.group(0)
        if w.lower() in STOPWORDS: continue
        before = text[:m.start()].rstrip()
        if (not before or before[-1] in ".!?") and not (w.isupper() and len(w) >= 2): continue   # sentence-start words
        items.append(w)
    seen, out = set(), []
    for i in items:
        k = i.lower()
        if k not in seen: seen.add(k); out.append(i)
    return out[:8]

def _llm_followup(role, question, convo, reply, items, st):
    focus = random.choice([h for h in FOCUS_HINTS if h not in st.get("focus_used", [])] or FOCUS_HINTS)
    if items:
        pick = random.choice(items)
        guide = (f"The candidate mentioned: {', '.join(items)}. Ask specifically about this ONE item: {pick} "
                 f"(for example how they used it, why they chose it, or a problem they faced with it).")
    else:
        pick = None
        guide = f"Ask about {focus}."
    prompt = (f'You are a warm, professional human interviewer for the role "{role}".\n'
              f"Main question: {question}\nConversation on this question so far:\n{convo}\n"
              f'Candidate\'s latest reply: "{reply}"\n{guide}\n'
              "Write exactly ONE short, natural follow-up question (under 22 words) based on what the candidate just said. "
              "Do not repeat earlier questions. Do not greet or praise. "
              "If the reply is empty, a refusal, or says they have no experience, output only: NONE\nFollow-up question:")
    try:
        r = requests.post(config.OLLAMA_URL, json={"model": config.OLLAMA_MODEL, "prompt": prompt, "stream": False,
                          "keep_alive": config.OLLAMA_KEEP_ALIVE, "options": {"temperature": 0.8, "num_predict": 60}},
                          timeout=config.LLM_FOLLOWUP_TIMEOUT)
        out = (r.json().get("response") or "").strip()
    except Exception:
        return None, None
    if not out or out.upper().startswith("NONE"): return "NONE", None
    qs = re.findall(r"[^.?!]*\?", out.replace("\n", " "))
    if not qs: return None, None
    q = qs[-1].strip().strip('"').strip()
    q = re.sub(r"^(follow-up question|question)\s*:\s*", "", q, flags=re.I)
    if len(q.split()) > 35 or len(q.split()) < 3: return None, None
    return q, focus

def _template_followup(role_id, st, reply, items):
    words = len(reply.split())
    asked = {a.lower() for a in st.get("asked_fu", [])}
    if words < 6:
        return random.choice(NUDGES)
    if items:
        return random.choice(ITEM_FU).format(x=random.choice(items))
    chain = [c for c in role_banks.chain_for(role_id) if c.lower() not in asked] + [g for g in GENERIC_FU if g.lower() not in asked]
    return chain[st["fu"]] if st["fu"] < len(chain) else (chain[0] if chain else None)

def _followups_allowed(st):
    """Time budget: how many follow-ups can we afford on this question?"""
    total = config.INTERVIEW_MAX_MIN * 60
    remaining = total - _elapsed(st) - config.CLOSING_RESERVE_SEC
    left_after = len(st["plan"]) - st["qpos"] - 1
    per = remaining / (left_after + 1)
    if per < 65: return 0
    if per < 110: return 1
    if per < 160: return 2
    return config.MAX_FOLLOWUPS_PER_QUESTION

def _maybe_followup(cid, st, qrow, reply):
    if st["fu"] >= _followups_allowed(st): return None
    words = len(reply.split())
    if role_banks.NEGATIVE.match(reply) and words < 12: return None          # "No, I haven't" -> just move on
    p = (0.9 if words < 60 else 0.7) if st["fu"] == 0 else 0.55               # "at times it is not necessary"
    if random.random() > p: return None
    cand = _cand(cid)
    role = db.query("SELECT name FROM job_roles WHERE id=?", (cand["role_id"],), one=True)
    items = extract_items(reply)
    convo = _convo_text(st, cand["name"], qrow["question"])
    text, focus = _llm_followup(role["name"] if role else "", qrow["question"], convo, reply, items, st)
    if text == "NONE": return None
    if not text: text, focus = _template_followup(cand["role_id"], st, reply, items), None
    if not text: return None
    if text.lower() in {a.lower() for a in st.get("asked_fu", [])} | {qrow["question"].lower()}: return None
    if focus: st.setdefault("focus_used", []).append(focus)
    if not text.endswith("?"): text += "?"
    return text


# ------------------------------------------------------------------ conversation text
def _convo_text(st, name, question):
    lines = [f"AI (question): {question}"]
    for b in st["buf"]:
        lines.append(f"{_first(name)}: {b['text']}" if b["who"] == "C" else f"AI (follow-up): {b['text']}")
    return "\n".join(lines)

def _save_answer(cid, st):
    """Store the finished question (labelled conversation) as an answer row, scored later."""
    qid = st["plan"][st["qpos"]]
    q = db.query("SELECT * FROM interview_questions WHERE id=?", (qid,), one=True)
    cand = _cand(cid)
    buf = st["buf"]
    cands = [b for b in buf if b["who"] == "C"]
    own = [b["text"] for b in cands if b["text"] != "(no answer)"]
    rts = [b["rt"] for b in cands if b.get("rt") is not None]
    fus, pending = [], None
    for b in buf:
        if b["who"] == "AI": pending = b["text"]
        elif pending is not None:
            fus.append({"question": pending, "audio": b.get("audio"), "reply": b["text"]}); pending = None
    first_audio = cands[0].get("audio") if cands else None
    db.execute("DELETE FROM answers WHERE candidate_id=? AND question_id=?", (cid, qid))
    db.execute("""INSERT INTO answers(candidate_id,question_id,audio_file,transcript,candidate_text,response_time,answer_duration,
                  status,followups,followup_json) VALUES(?,?,?,?,?,?,?,'pending_score',?,?)""",
               (cid, qid, first_audio or "-", _convo_text(st, cand["name"], q["question"]), " ".join(own),
                (sum(rts) / len(rts)) if rts else None, sum((b.get("dur") or 0) for b in cands), len(fus), json.dumps(fus)))
    st["buf"], st["fu"] = [], 0


# ------------------------------------------------------------------ the conversation steps
def _question_text(st, qpos, prefix=""):
    q = db.query("SELECT * FROM interview_questions WHERE id=?", (st["plan"][qpos],), one=True)
    if qpos == 0:
        return f"{prefix}Great, let's begin. {q['question']}", q
    prev = db.query("SELECT category FROM interview_questions WHERE id=?", (st["plan"][qpos - 1],), one=True)
    if prev and prev["category"] != q["category"] and random.random() < 0.6:
        trans = random.choice(TOPIC_TRANSITIONS).format(cat=q["category"])
    else:
        trans = random.choice(TRANSITIONS)
    return f"{prefix}{trans} {q['question']}", q

def _ask_next(cid, st, prefix=""):
    text, q = _question_text(st, st["qpos"], prefix)
    st["stage"], st["fu"], st["buf"] = "await_answer", 0, []
    tid = _turn(cid, "AI", "question", text, qid=q["id"])
    save_state(cid, st)
    return _msg(st, tid, text, "question")

def _closing(cid, st, timed_out=False):
    name = _first(_cand(cid)["name"])
    if timed_out:
        text = (f"We've reached the time limit for this interview, so let's wrap up here. Thank you so much for your time, "
                f"{name}. HR will contact you with the next steps. Have a great day!")
    else:
        text = (f"That brings us to the end of the interview. Thank you so much for your time, {name}. "
                f"HR will contact you with the next steps. Have a great day!")
    tid = _turn(cid, "AI", "closing", text)
    st["stage"] = "done"
    save_state(cid, st)
    finish(cid)
    return _msg(st, tid, text, "closing", done=True)

def _finish_question_and_next(cid, st, prefix=""):
    _save_answer(cid, st)
    st["qpos"] += 1
    more = st["qpos"] < len(st["plan"])
    out_of_time = _elapsed(st) > config.INTERVIEW_MAX_MIN * 60 - config.CLOSING_RESERVE_SEC
    if not more or out_of_time:
        timed_out = more and out_of_time
        st["qpos"] = min(st["qpos"], len(st["plan"]) - 1)
        return _closing(cid, st, timed_out=timed_out)
    return _ask_next(cid, st, prefix)

def start(cid, channel="web"):
    """First call (or a reload): returns the current AI utterance."""
    cand = _cand(cid)
    st = get_state(cid)
    if st and st["stage"] == "done": return None
    if st:                                                       # page reloaded -> repeat the last AI line
        last = db.query("SELECT * FROM turns WHERE candidate_id=? AND speaker='AI' AND kind IN ('greeting','question','followup') "
                        "ORDER BY id DESC LIMIT 1", (cid,), one=True)
        if last: return _msg(st, last["id"], last["text"], last["kind"])
    plan = [r["id"] for r in db.query("SELECT id FROM interview_questions WHERE role_id=? AND active=1 ORDER BY order_number, id", (cand["role_id"],))]
    if not plan: raise ValueError("No questions are selected for this role.")
    role = db.query("SELECT name FROM job_roles WHERE id=?", (cand["role_id"],), one=True)
    st = {"started": time.time(), "plan": plan, "qpos": 0, "stage": "await_smalltalk", "fu": 0, "asked_fu": [], "buf": [], "focus_used": [], "channel": channel}
    mins = min(config.INTERVIEW_MAX_MIN, max(5, round(len(plan) * 2.5)))
    if channel == "phone":
        text = (f"Hello {_first(cand['name'])}, this is the AI interviewer calling on behalf of HR for the {role['name'] if role else ''} position. "
                f"This call is recorded for evaluation. It will take about {mins} minutes. Is this a good time to talk?")
    else:
        text = (f"Hello {_first(cand['name'])}, welcome! I'm the AI interviewer for the {role['name'] if role else ''} position. "
                f"I'll ask you some questions and sometimes follow up on your answers. This will take about {mins} minutes. "
                f"Before we start, how are you doing today?")
    db.execute("UPDATE candidates SET interview_status='in_progress', started_at=? WHERE id=?", (_now(), cid))
    tid = _turn(cid, "AI", "greeting", text)
    save_state(cid, st)
    return _msg(st, tid, text, "greeting")

def _advance(cid, text, audio, rt, dur, spoke):
    st = get_state(cid)
    stage = st["stage"]
    if stage == "await_smalltalk":                               # small talk is recorded but never scored
        if st.get("channel") == "phone" and re.search(r"\b(busy|not now|later|call (me )?back|no time|bad time)\b", text or "", re.I):
            msg = "No problem at all. We will call you again at a better time. Thank you, goodbye!"
            tid = _turn(cid, "AI", "closing", msg)
            m = _msg(st, tid, msg, "closing", done=True)
            db.execute("UPDATE candidates SET interview_status='pending', started_at=NULL WHERE id=?", (cid,))
            db.execute("DELETE FROM interview_state WHERE candidate_id=?", (cid,))
            return m
        return _ask_next(cid, st)
    qrow = db.query("SELECT * FROM interview_questions WHERE id=?", (st["plan"][st["qpos"]],), one=True)
    if not spoke or not text.strip():
        st["buf"].append({"who": "C", "text": "(no answer)", "rt": rt, "dur": 0, "audio": None})
        prefix = random.choice(NO_HEAR) + " " if stage == "await_answer" else "No problem. "
        return _finish_question_and_next(cid, st, prefix)
    st["buf"].append({"who": "C", "text": text, "rt": rt, "dur": dur, "audio": audio})
    fu = _maybe_followup(cid, st, qrow, text)
    if fu:
        st["buf"].append({"who": "AI", "text": fu}); st["asked_fu"].append(fu); st["fu"] += 1; st["stage"] = "await_followup"
        tid = _turn(cid, "AI", "followup", fu, qid=qrow["id"])
        save_state(cid, st)
        return _msg(st, tid, fu, "followup")
    return _finish_question_and_next(cid, st)


# ------------------------------------------------------------------ async reply handling
def submit_reply(cid, audio_bytes, ext, rt, dur, spoke):
    """Called when the candidate finishes speaking. Returns immediately with a short spoken acknowledgement
    (so there is no dead air) while transcription + the next question are prepared in the background."""
    st = get_state(cid)
    fname, cturn = None, None
    qid = st["plan"][st["qpos"]] if st["stage"] != "await_smalltalk" else None
    if spoke and audio_bytes:
        fname = f"c{cid}_t{uuid.uuid4().hex[:8]}.{ext}"
        (config.UPLOAD_FOLDER / fname).write_bytes(audio_bytes)
    cturn = _turn(cid, "CANDIDATE", "smalltalk" if st["stage"] == "await_smalltalk" else "answer", "", qid=qid, audio=fname, rt=rt, dur=dur)
    ack = None
    if spoke and fname:
        pool = SMALLTALK_ACKS if st["stage"] == "await_smalltalk" else ACKS
        text = random.choice(pool)
        ack = {"id": _turn(cid, "AI", "ack", text, qid=qid), "text": text}
    job = uuid.uuid4().hex
    with _jobs_lock: _jobs[job] = {"ready": False}
    _pool.submit(_work, job, cid, cturn, fname, rt, dur, bool(spoke and fname))
    return {"job": job, "ack": ack}

def _work(job, cid, cturn, fname, rt, dur, spoke):
    try:
        text = ""
        if spoke:
            try: text, _ = speech_to_text.transcribe(config.UPLOAD_FOLDER / fname)
            except Exception as e:
                print("[STT]", e); text = "(transcription failed)"
        db.execute("UPDATE turns SET text=? WHERE id=?", (text or "(no speech detected)", cturn))
        res = _advance(cid, text if text != "(transcription failed)" else "", fname, rt, dur, spoke)
        with _jobs_lock: _jobs[job] = {"ready": True, "say": res}
    except Exception as e:
        traceback.print_exc()
        with _jobs_lock: _jobs[job] = {"ready": True, "error": str(e)}

def job_result(job):
    with _jobs_lock:
        r = _jobs.get(job)
        if r and r.get("ready"): _jobs.pop(job, None)
        return r


# ------------------------------------------------------------------ finishing + records
def transcript_text(cid):
    cand = _cand(cid)
    name = cand["name"] or "Candidate"
    lines = [f"INTERVIEW TRANSCRIPT - {name}", "=" * 60]
    for t in db.query("SELECT * FROM turns WHERE candidate_id=? ORDER BY id", (cid,)):
        if t["speaker"] == "AI":
            label = {"question": "AI (question)", "followup": "AI (follow-up)"}.get(t["kind"], "AI")
        else:
            label = f"{name} (reply)"
        rt = f"  [replied after {t['response_time']:.1f}s]" if t["speaker"] != "AI" and t["response_time"] is not None else ""
        lines.append(f"{label}: {t['text']}{rt}")
    return "\n".join(lines) + "\n"

def finish(cid):
    """Mark completed (server-side, never depends on the browser), save transcripts, start scoring."""
    st = get_state(cid)
    if st and st["buf"] and st["stage"] != "done":
        _save_answer(cid, st)
    if st:
        st["stage"] = "done"; save_state(cid, st)
    db.execute("UPDATE candidates SET interview_status='completed', finished_at=? WHERE id=?", (_now(), cid))
    try:
        (config.TRANSCRIPT_FOLDER / f"candidate_{cid}_full_conversation.txt").write_text(transcript_text(cid), encoding="utf-8")
    except Exception: pass
    for a in db.query("SELECT id FROM answers WHERE candidate_id=? AND status='pending_score'", (cid,)):
        db.execute("UPDATE answers SET status='processing' WHERE id=?", (a["id"],))
        pipeline.enqueue(a["id"])
    pipeline.finalize_if_ready(cid)

def progress(cid):
    st = get_state(cid)
    return {"q": min(st["qpos"] + 1, len(st["plan"])), "total": len(st["plan"])} if st else None
