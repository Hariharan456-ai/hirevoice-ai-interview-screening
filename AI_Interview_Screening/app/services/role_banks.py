"""Loads the role-specific question banks (from your 10 PDFs) into the database and
handles the AI follow-up chain."""
import json, re
from .. import database as db, config

YESNO = re.compile(r"^\s*(have|do|are|did|is|has)\s+(you|your)\b", re.I)
NEGATIVE = re.compile(r"^\W*(no\b|nope|not really|never|i (have not|haven't|don't|do not|did not|didn't)|not yet)", re.I)

def needs_followup(question):
    return 1 if YESNO.match(question or "") else 0

def seed_roles(force=False):
    try: banks = json.loads(config.ROLE_BANK_FILE.read_text(encoding="utf-8"))
    except Exception: banks = []
    new_roles = []
    for b in banks:
        r = db.query("SELECT id FROM job_roles WHERE name=?", (b["name"],), one=True)
        if r and not force: continue
        rid = r["id"] if r else db.execute("INSERT INTO job_roles(name,description,followup_chain) VALUES(?,?,?)",
                                           (b["name"], b.get("description", ""), json.dumps(b["followup_chain"])))
        if not r: new_roles.append(rid)
        have = {x["question"] for x in db.query("SELECT question FROM interview_questions WHERE role_id=?", (rid,))}
        n = db.query("SELECT COALESCE(MAX(order_number),0) n FROM interview_questions WHERE role_id=?", (rid,), one=True)["n"]
        for q in b["questions"]:
            if q["question"] in have: continue
            n += 1
            db.execute("INSERT INTO interview_questions(question,category,maximum_score,order_number,role_id,needs_followup) VALUES(?,?,?,?,?,?)",
                       (q["question"], q["category"], 100, n, rid, needs_followup(q["question"])))
    for rid in new_roles: apply_default_selection(rid)
    # migrate legacy v2 data (no role) into a "General" role
    legacy_q = db.query("SELECT COUNT(*) n FROM interview_questions WHERE role_id IS NULL", one=True)["n"]
    legacy_c = db.query("SELECT COUNT(*) n FROM candidates WHERE role_id IS NULL", one=True)["n"]
    if legacy_q or legacy_c:
        g = db.query("SELECT id FROM job_roles WHERE name='General'", one=True)
        gid = g["id"] if g else db.execute("INSERT INTO job_roles(name,description,followup_chain) VALUES('General','Custom questions',?)",
                                           (json.dumps(config_default_chain()),))
        db.execute("UPDATE interview_questions SET role_id=? WHERE role_id IS NULL", (gid,))
        db.execute("UPDATE candidates SET role_id=? WHERE role_id IS NULL", (gid,))

def config_default_chain():
    return ["Can you give us a real example?", "What was your role?", "What challenge did you face?", "What was the result?"]

def chain_for(role_id):
    r = db.query("SELECT followup_chain FROM job_roles WHERE id=?", (role_id,), one=True)
    try: chain = json.loads(r["followup_chain"]) if r else config_default_chain()
    except Exception: chain = config_default_chain()
    n = config.FOLLOWUP_STEPS
    if len(chain) <= n: return chain
    keep = {0, 1}
    for kw in ("challenge", "issue", "situation"):
        i = next((i for i, t in enumerate(chain) if kw in t.lower()), None)
        if i is not None and len(keep) < n - 1: keep.add(i); break
    i = next((i for i, t in enumerate(chain) if re.search(r"result|outcome|impact", t, re.I)), len(chain) - 1)
    keep.add(i)
    for i in range(len(chain)):
        if len(keep) >= n: break
        keep.add(i)
    return [chain[i] for i in sorted(keep)][:n]

def is_strong_yes(transcript):
    t = (transcript or "").strip()
    return len(t.split()) >= 4 and not NEGATIVE.match(t)


def apply_default_selection(role_id=None):
    """Choose ONE question per section (rotating Q1/Q2/Q3) so a full interview fits in ~25-30 minutes.
    HR can change the selection on the Questions page."""
    ids = [role_id] if role_id else [r["id"] for r in db.query("SELECT id FROM job_roles")]
    for rid in ids:
        qs = db.query("SELECT * FROM interview_questions WHERE role_id=? ORDER BY order_number, id", (rid,))
        cats = []
        for q in qs:
            if q["category"] not in cats: cats.append(q["category"])
        if len(cats) < 3:                       # custom / small sets: ask everything
            db.execute("UPDATE interview_questions SET active=1 WHERE role_id=?", (rid,)); continue
        db.execute("UPDATE interview_questions SET active=0 WHERE role_id=?", (rid,))
        for i, cat in enumerate(cats):
            items = [q for q in qs if q["category"] == cat]
            db.execute("UPDATE interview_questions SET active=1 WHERE id=?", (items[min(i % 3, len(items) - 1)]["id"],))
