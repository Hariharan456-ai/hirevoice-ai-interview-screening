"""Background worker: transcribe -> evaluate -> score -> rank -> PDF. One job at a time (CPU friendly)."""
import json, queue, threading, traceback
from pathlib import Path
from .. import database as db, config
from . import speech_to_text, ai_evaluator, ranking, report_generator

_q = queue.Queue()
_started = False
_finalize_lock = threading.Lock()

def start_worker():
    global _started
    if _started: return
    _started = True
    threading.Thread(target=_loop, daemon=True).start()

def enqueue(answer_id):
    _q.put(answer_id)

def _loop():
    while True:
        aid = _q.get()
        try: process_answer(aid)
        except Exception:
            traceback.print_exc()
        finally: _q.task_done()

def process_answer(answer_id):
    a = db.query("SELECT * FROM answers WHERE id=?", (answer_id,), one=True)
    if not a: return
    q = db.query("SELECT * FROM interview_questions WHERE id=?", (a["question_id"],), one=True)
    try:
        transcript = a["transcript"]
        if not transcript and a["audio_file"] and a["audio_file"] != "-":
            transcript, dur = speech_to_text.transcribe(config.UPLOAD_FOLDER / a["audio_file"])
            db.execute("UPDATE answers SET transcript=?, answer_duration=? WHERE id=?", (transcript, dur, answer_id))
        (config.TRANSCRIPT_FOLDER / f"cand{a['candidate_id']}_q{a['question_id']}.txt").write_text(
            f"Question: {q['question']}\n\nAnswer:\n{transcript}\n", encoding="utf-8")
        role = db.query("SELECT jr.name FROM candidates c LEFT JOIN job_roles jr ON jr.id=c.role_id WHERE c.id=?", (a["candidate_id"],), one=True)
        a = db.query("SELECT * FROM answers WHERE id=?", (answer_id,), one=True)
        r = ai_evaluator.evaluate_answer(q["question"], transcript or "", a["response_time"], a["answer_duration"], q["maximum_score"],
                                       (role["name"] if role else ""), q["answer_key"] or "", a["candidate_text"] or "")
        db.execute("""UPDATE answers SET raw_score=?, penalty_pct=?, score=?, ai_likelihood=?, ai_suspected=?,
                      evaluation_json=?, status='done', error=NULL WHERE id=?""",
                   (r["raw_score"], r["penalty_pct"], r["score"], r["ai_likelihood"], r["ai_suspected"],
                    json.dumps(r["evaluation"]), answer_id))
        (config.REPORT_FOLDER / f"cand{a['candidate_id']}_q{a['question_id']}_evaluation.json").write_text(
            json.dumps({"question": q["question"], "transcript": transcript, **r}, indent=2), encoding="utf-8")
    except Exception as e:
        db.execute("UPDATE answers SET status='error', error=? WHERE id=?", (str(e), answer_id))
    finalize_if_ready(a["candidate_id"])

def _drive(cid):
    try:
        from . import drive_uploader
        if drive_uploader.ready(): drive_uploader.upload_pdf(cid)
    except Exception as ex:
        print("[Drive] skipped:", ex)

def finalize_if_ready(candidate_id, force=False):
    """Re-rank everyone (cheap) and rebuild PDFs when a candidate has finished processing."""
    with _finalize_lock:
        ranking.compute_rankings()
        c = db.query("SELECT * FROM candidates WHERE id=?", (candidate_id,), one=True)
        pending = db.query("SELECT COUNT(*) n FROM answers WHERE candidate_id=? AND status='processing'", (candidate_id,), one=True)["n"]
        if pending == 0 and (force or (c and c["interview_status"] == "completed")):
            report_generator.generate_pdf(candidate_id)
            _drive(candidate_id)
            for e in db.query("SELECT candidate_id FROM evaluations WHERE candidate_id<>?", (candidate_id,)):
                try: report_generator.generate_pdf(e["candidate_id"])   # ranks may have shifted
                except Exception: pass
