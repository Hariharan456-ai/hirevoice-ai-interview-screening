import json
from .. import database as db, config

def _ai_flag(review, flagged, n, avg_ai):
    """HR's manual review always wins over the automatic detector."""
    if review == "cleared": return 0
    if review == "confirmed": return 1
    return 1 if (flagged >= max(1, n / 2) or avg_ai >= config.AI_SUSPECT_THRESHOLD) else 0

def compute_rankings():
    """Ranks candidates WITHIN their job role (different roles are never compared)."""
    all_rows = []
    for role in db.query("SELECT id FROM job_roles"):
        qs = db.query("SELECT * FROM interview_questions WHERE role_id=?", (role["id"],))
        rows = []
        for c in db.query("SELECT * FROM candidates WHERE role_id=?", (role["id"],)):
            ans = db.query("SELECT * FROM answers WHERE candidate_id=? AND status='done'", (c["id"],))
            if not ans: continue
            answered = {a["question_id"] for a in ans}      # max score = questions that are asked now + ones this candidate answered
            max_total = sum(q["maximum_score"] for q in qs if q["active"] or q["id"] in answered) or 100
            total = sum(a["score"] or 0 for a in ans)
            tech = ps = 0.0
            for a in ans:
                try:
                    ev = json.loads(a["evaluation_json"] or "{}"); tech += ev.get("technical_knowledge", 0); ps += ev.get("problem_solving", 0)
                except Exception: pass
            rts = [a["response_time"] for a in ans if a["response_time"] is not None]
            flagged = sum(1 for a in ans if a["ai_suspected"])
            avg_ai = sum((a["ai_likelihood"] or 0) for a in ans) / len(ans)
            rows.append(dict(cid=c["id"], maxt=max_total, total=round(total, 2), pct=round(total / max_total * 100, 2),
                             tech=tech / len(ans), ps=ps / len(ans), rt=(sum(rts) / len(rts)) if rts else None,
                             ai=_ai_flag(c["ai_review"], flagged, len(ans), avg_ai), n=len(ans)))
        rows.sort(key=lambda r: (-r["pct"], -r["tech"], -r["ps"]))
        prev, rank = None, 0
        for i, r in enumerate(rows, 1):
            key = (r["pct"], round(r["tech"], 2), round(r["ps"], 2))
            if key != prev: rank = i
            r["rank"], prev = rank, key
            db.execute("""INSERT INTO evaluations(candidate_id,total_score,max_score,percentage,rank,ai_suspected,avg_response_time,summary)
                          VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(candidate_id) DO UPDATE SET total_score=excluded.total_score,
                          max_score=excluded.max_score,percentage=excluded.percentage,rank=excluded.rank,
                          ai_suspected=excluded.ai_suspected,avg_response_time=excluded.avg_response_time""",
                       (r["cid"], r["total"], r["maxt"], r["pct"], r["rank"], r["ai"], r["rt"], ""))
        all_rows += rows
    return all_rows

def ranking_table(role_id=None):
    where, args = ("WHERE c.role_id=?", (role_id,)) if role_id else ("", ())
    return db.query(f"""SELECT c.*, jr.name AS role_name, e.total_score, e.max_score, e.percentage, e.rank, e.ai_suspected AS ai_flag,
        e.avg_response_time, e.pdf_path, e.drive_link,
        (SELECT COUNT(*) FROM answers a WHERE a.candidate_id=c.id AND a.status='done') AS done_count,
        (SELECT COUNT(*) FROM answers a WHERE a.candidate_id=c.id AND a.status='processing') AS proc_count
        FROM candidates c LEFT JOIN evaluations e ON e.candidate_id=c.id LEFT JOIN job_roles jr ON jr.id=c.role_id
        {where} ORDER BY jr.name, (e.rank IS NULL), e.rank, c.name""", args)
