"""Helpers for manual (HR-uploaded) answers. The live conversation is handled by conversation.py."""
import secrets, uuid
from .. import database as db, config
from . import pipeline

def new_token(): return secrets.token_urlsafe(9)

def save_audio(candidate_id, question_id, audio_bytes, ext, tag=""):
    fname = f"c{candidate_id}_q{question_id}{tag}_{uuid.uuid4().hex[:6]}.{ext}"
    (config.UPLOAD_FOLDER / fname).write_bytes(audio_bytes)
    return fname

def record_answer(candidate_id, question_id, audio_bytes, ext, response_time, duration=None):
    fname = save_audio(candidate_id, question_id, audio_bytes, ext)
    db.execute("DELETE FROM answers WHERE candidate_id=? AND question_id=?", (candidate_id, question_id))
    return db.execute("""INSERT INTO answers(candidate_id,question_id,audio_file,response_time,answer_duration,status)
                         VALUES(?,?,?,?,?, 'processing')""", (candidate_id, question_id, fname, response_time, duration))

def process_answer(answer_id):
    pipeline.enqueue(answer_id)
