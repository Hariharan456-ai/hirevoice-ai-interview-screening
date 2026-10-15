import csv, hmac, io, os, re, shutil, socket, json, threading, time
from pathlib import Path
from flask import (Blueprint, render_template, request, redirect, url_for, flash, jsonify, send_file,
                   send_from_directory, abort, Response, session)
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime
from . import database as db, config
from .services import (resume_parser, pipeline, ranking, report_generator, excel_generator, tts,
                       interview_session as sess, ai_evaluator, role_banks, speech_to_text, drive_uploader, conversation, phone_call)

bp = Blueprint("main", __name__)

# ------------------------------------------------------------------ HR login
_fails = {}

@bp.before_request
def require_login():
    path = request.path
    if request.endpoint == "main.login" or path.startswith("/interview/"): return None   # candidates use their private link
    if session.get("hr"): return None
    if path.startswith("/api/"): return jsonify(error="login required"), 401
    return redirect(url_for("main.login", next=path))

def _credentials_ok(email, pw):
    cur_email = (db.get_setting("hr_email") or config.HR_EMAIL).strip().lower()
    h = db.get_setting("hr_password_hash")
    pw_ok = check_password_hash(h, pw) if h else hmac.compare_digest(pw.encode(), config.HR_PASSWORD.encode())
    return hmac.compare_digest((email or "").strip().lower().encode(), cur_email.encode()) and pw_ok

@bp.route("/login", methods=["GET", "POST"])
def login():
    err = ""
    if request.method == "POST":
        ip = request.remote_addr or "?"
        n, t0 = _fails.get(ip, (0, 0))
        if n >= 5 and time.time() - t0 < 300:
            err = "Too many wrong attempts. Please wait 5 minutes."
        elif _credentials_ok(request.form.get("email", ""), request.form.get("password", "")):
            _fails.pop(ip, None); session.clear(); session["hr"] = True; session.permanent = True
            nxt = request.form.get("next") or request.args.get("next") or ""
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("main.dashboard"))
        else:
            _fails[ip] = ((n + 1, time.time()) if time.time() - t0 < 300 else (1, time.time()))
            err = "Wrong email or password."
    return render_template("login.html", err=err, nxt=request.args.get("next", ""))

@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("main.login"))

@bp.route("/account", methods=["GET", "POST"])
def account():
    if request.method == "POST":
        if not _credentials_ok(db.get_setting("hr_email") or config.HR_EMAIL, request.form.get("current", "")):
            flash("Current password is wrong.", "err")
        else:
            new_email = request.form.get("email", "").strip()
            new_pw = request.form.get("password", "")
            if new_email: db.set_setting("hr_email", new_email)
            if new_pw:
                if len(new_pw) < 8: flash("New password must have at least 8 characters.", "err"); return redirect(url_for("main.account"))
                db.set_setting("hr_password_hash", generate_password_hash(new_pw))
            flash("Login details updated.", "ok")
    return render_template("account.html", email=db.get_setting("hr_email") or config.HR_EMAIL)

# ------------------------------------------------------------------ live status
def derive_status(r):
    """pending | in_progress | incomplete (stopped early) | scoring | completed"""
    st = r["interview_status"]
    if st == "completed":
        return "scoring" if r["proc_count"] else "completed"
    if st == "in_progress":
        la = r["last_activity"]
        try:
            idle = (datetime.utcnow() - datetime.fromisoformat(la)).total_seconds() / 60 if la else 0
        except Exception: idle = 0
        return "incomplete" if idle > config.STALE_MINUTES else "in_progress"
    return "pending"

def _ext(fn): return fn.rsplit(".", 1)[-1].lower() if "." in fn else ""

def norm_phone(p):
    d = re.sub(r"\D", "", p or "")
    if len(d) == 10: d = config.DEFAULT_COUNTRY_CODE + d
    return d

def base_url():
    return (db.get_setting("public_url") or request.host_url).rstrip("/")

@bp.app_template_filter("fmt")
def fmt(v, nd=1):
    return "-" if v is None else f"{v:.{nd}f}"

def roles_list():
    return db.query("""SELECT jr.*, (SELECT COUNT(*) FROM candidates c WHERE c.role_id=jr.id) AS ncand,
                       (SELECT COUNT(*) FROM interview_questions q WHERE q.role_id=jr.id) AS nq FROM job_roles jr ORDER BY jr.name""")

def pick_role(arg=None):
    rid = request.args.get("role", type=int) if arg is None else arg
    roles = roles_list()
    if rid and any(r["id"] == rid for r in roles): return rid, roles
    withc = [r for r in roles if r["ncand"]]
    return ((withc or roles)[0]["id"] if roles else None), roles

# ------------------------------------------------------------------ HR pages
@bp.route("/")
def dashboard():
    rid, roles = pick_role()
    qcount = db.query("SELECT COUNT(*) n FROM interview_questions WHERE role_id=?", (rid,), one=True)["n"]
    return render_template("hr_dashboard.html", roles=roles, rid=rid, qcount=qcount, hr_phone=db.get_setting("hr_phone"))

@bp.route("/api/ranking")
def api_ranking():
    resp = _api_ranking()
    resp.headers["Cache-Control"] = "no-store"
    return resp

def _api_ranking():
    rid, _ = pick_role()
    rows = ranking.ranking_table(rid)
    scored = [r["percentage"] for r in rows if r["percentage"] is not None]
    dist = [0] * 5
    for p in scored: dist[min(4, int(p // 20))] += 1
    return jsonify(stats=dict(total=len(rows), completed=sum(1 for r in rows if derive_status(r) in ("completed", "scoring")),
                              live=sum(1 for r in rows if derive_status(r) == "in_progress"),
                              avg=round(sum(scored) / len(scored), 1) if scored else 0,
                              top=round(max(scored), 1) if scored else 0,
                              flagged=sum(1 for r in rows if r["ai_flag"]), dist=dist),
                   rows=[dict(id=r["id"], rank=r["rank"], name=r["name"], degree=r["degree"], spec=r["specialization"],
                              phone=r["phone"], status=derive_status(r), progress=(conversation.progress(r["id"]) if derive_status(r) == "in_progress" else None),
                              done=r["done_count"], proc=r["proc_count"],
                              total=r["total_score"], max=r["max_score"], pct=r["percentage"], ai=r["ai_flag"],
                              rt=r["avg_response_time"], pdf=bool(r["pdf_path"]), drive=r["drive_link"]) for r in rows])

@bp.route("/candidates")
def candidates():
    rows = ranking.ranking_table()
    qcount = len(db.query("SELECT id FROM interview_questions"))
    hr_phone = db.get_setting("hr_phone")
    invites = []
    for r in rows:
        link = f"{base_url()}/interview/{r['token']}"
        msg = (f"Hello {r['name']}, you are shortlisted for an interview screening. Please open this link on a phone/laptop with a "
               f"microphone and answer the spoken questions in English: {link}" + (f" . HR contact: {hr_phone}" if hr_phone else ""))
        from urllib.parse import quote
        invites.append(dict(row=r, status=derive_status(r), link=link, wa=f"https://wa.me/{norm_phone(r['phone'])}?text={quote(msg)}",
                            sms=f"sms:{r['phone']}?body={quote(msg)}", tel=f"tel:{r['phone']}"))
    return render_template("candidates.html", invites=invites, hr_phone=hr_phone, public_url=db.get_setting("public_url"),
                           qcount=qcount, roles=roles_list(), sel=pick_role()[0], drive_status=drive_uploader.status(), drive_ready=drive_uploader.ready(),
                           sip=phone_call.sip_settings(), phone_status=phone_call.status_text())

@bp.route("/settings", methods=["POST"])
def settings():
    db.set_setting("hr_phone", request.form.get("hr_phone", "").strip())
    db.set_setting("public_url", request.form.get("public_url", "").strip().rstrip("/"))
    flash("Settings saved.", "ok")
    return redirect(url_for("main.candidates"))

def _add_candidate(d, resume_path="", role_id=None):
    if role_id is None: role_id = request.form.get("role_id", type=int)
    return db.execute("""INSERT INTO candidates(name,phone,email,college,degree,specialization,graduation_year,resume_path,token,role_id)
        VALUES(?,?,?,?,?,?,?,?,?,?)""", (d.get("name", ""), d.get("phone", ""), d.get("email", ""), d.get("college", ""),
        d.get("degree", ""), d.get("specialization", ""), d.get("graduation_year", ""), resume_path, sess.new_token(), role_id))

@bp.route("/candidates/upload_resumes", methods=["POST"])
def upload_resumes():
    added, problems = 0, []
    for f in request.files.getlist("resumes"):
        if not f or not f.filename: continue
        if _ext(f.filename) not in config.ALLOWED_RESUME:
            problems.append(f"{f.filename}: only PDF resumes are supported"); continue
        safe = f"{os.urandom(3).hex()}_{secure_filename(f.filename)}"
        path = config.RESUME_FOLDER / safe
        f.save(path)
        try:
            d = resume_parser.parse_resume(path, f.filename)
            if d.get("ocr_used"): problems.append(f"{f.filename}: scanned resume - read with OCR, please double-check name/phone")
            elif not d["text_found"]:
                problems.append(f"{f.filename}: scanned image PDF and OCR is not installed. Run: pip install -r requirements-optional.txt  (name taken from file name; please edit phone)")
            _add_candidate(d, safe); added += 1
            if not d["phone"]: problems.append(f"{f.filename}: phone number not found in resume")
        except Exception as e:
            problems.append(f"{f.filename}: could not read PDF ({e})")
    if added: flash(f"{added} resume(s) processed - name and phone extracted automatically. Please check them below.", "ok")
    for p in problems: flash(p, "warn")
    return redirect(url_for("main.candidates"))

@bp.route("/candidates/add", methods=["POST"])
def add_candidate():
    d = {k: request.form.get(k, "").strip() for k in ("name", "phone", "email", "college", "degree", "specialization", "graduation_year")}
    if not d["name"]: flash("Name is required.", "err")
    else: _add_candidate(d); flash("Candidate added.", "ok")
    return redirect(url_for("main.candidates"))

@bp.route("/candidates/import_csv", methods=["POST"])
def import_csv():
    f = request.files.get("csv")
    if not f: flash("Choose a CSV file.", "err"); return redirect(url_for("main.candidates"))
    n = 0
    for row in csv.DictReader(io.StringIO(f.read().decode("utf-8-sig"))):
        if (row.get("name") or "").strip(): _add_candidate({k: (v or "").strip() for k, v in row.items() if k}); n += 1
    flash(f"Imported {n} candidate(s).", "ok")
    return redirect(url_for("main.candidates"))

@bp.route("/candidates/<int:cid>/role", methods=["POST"])
def set_role(cid):
    db.execute("UPDATE candidates SET role_id=? WHERE id=?", (request.form.get("role_id", type=int), cid))
    db.execute("DELETE FROM evaluations WHERE candidate_id=?", (cid,))
    ranking.compute_rankings()
    return redirect(url_for("main.candidates"))

@bp.route("/candidates/<int:cid>/edit", methods=["POST"])
def edit_candidate(cid):
    f = request.form
    db.execute("UPDATE candidates SET name=?, phone=?, email=?, college=?, degree=?, specialization=?, graduation_year=? WHERE id=?",
               (f.get("name", ""), f.get("phone", ""), f.get("email", ""), f.get("college", ""), f.get("degree", ""),
                f.get("specialization", ""), f.get("graduation_year", ""), cid))
    flash("Candidate updated.", "ok")
    return redirect(request.referrer or url_for("main.candidates"))

@bp.route("/candidates/<int:cid>")
def candidate_details(cid):
    c = db.query("SELECT * FROM candidates WHERE id=?", (cid,), one=True) or abort(404)
    e = db.query("SELECT * FROM evaluations WHERE candidate_id=?", (cid,), one=True)
    import json
    ans = []
    for a in db.query("""SELECT a.*, q.id AS qid, q.question, q.category, q.order_number, q.maximum_score FROM interview_questions q
                         LEFT JOIN answers a ON a.question_id=q.id AND a.candidate_id=? WHERE q.role_id=? ORDER BY q.order_number, q.id""", (cid, c["role_id"])):
        d = dict(a)
        try: d["fus"] = json.loads(a["followup_json"] or "[]")
        except Exception: d["fus"] = []
        try: d["ev"] = json.loads(a["evaluation_json"] or "{}")
        except Exception: d["ev"] = {}
        ans.append(d)
    turns = db.query("SELECT * FROM turns WHERE candidate_id=? ORDER BY id", (cid,))
    return render_template("candidate_details.html", c=c, e=e, ans=ans, rubric=config.RUBRIC, labels=config.RUBRIC_LABELS,
                           turns=turns, has_rec=(config.UPLOAD_FOLDER / f"full_{cid}.webm").exists() or (config.UPLOAD_FOLDER / f"full_{cid}.wav").exists(),
                           status=derive_status(next((r for r in ranking.ranking_table(c["role_id"]) if r["id"] == cid), c)),
                           role=db.query("SELECT name FROM job_roles WHERE id=?", (c["role_id"],), one=True),
                           link=f"{base_url()}/interview/{c['token']}", thr=config.AI_SUSPECT_THRESHOLD)

def _wipe_conversation(cid):
    for t in db.query("SELECT audio_file FROM turns WHERE candidate_id=?", (cid,)):
        if t["audio_file"]: (config.UPLOAD_FOLDER / Path(t["audio_file"]).name).unlink(missing_ok=True)
    (config.UPLOAD_FOLDER / f"full_{cid}.webm").unlink(missing_ok=True)
    (config.UPLOAD_FOLDER / f"full_{cid}.wav").unlink(missing_ok=True)
    (config.TRANSCRIPT_FOLDER / f"candidate_{cid}_full_conversation.txt").unlink(missing_ok=True)
    for f in config.REPORT_FOLDER.glob(f"cand{cid}_q*_evaluation.json"): f.unlink(missing_ok=True)
    db.execute("DELETE FROM turns WHERE candidate_id=?", (cid,))
    db.execute("DELETE FROM interview_state WHERE candidate_id=?", (cid,))

@bp.route("/candidates/<int:cid>/delete", methods=["POST"])
def delete_candidate(cid):
    c = db.query("SELECT * FROM candidates WHERE id=?", (cid,), one=True) or abort(404)
    for a in db.query("SELECT * FROM answers WHERE candidate_id=?", (cid,)):
        if a["audio_file"]: (config.UPLOAD_FOLDER / Path(a["audio_file"]).name).unlink(missing_ok=True)
        try:
            for fu in json.loads(a["followup_json"] or "[]"): (config.UPLOAD_FOLDER / Path(fu["audio"]).name).unlink(missing_ok=True)
        except Exception: pass
        (config.TRANSCRIPT_FOLDER / f"cand{cid}_q{a['question_id']}.txt").unlink(missing_ok=True)
    if c["resume_path"]: (config.RESUME_FOLDER / Path(c["resume_path"]).name).unlink(missing_ok=True)
    (config.REPORT_FOLDER / f"candidate_{cid}_report.pdf").unlink(missing_ok=True)
    _wipe_conversation(cid)
    db.execute("DELETE FROM answers WHERE candidate_id=?", (cid,))
    db.execute("DELETE FROM evaluations WHERE candidate_id=?", (cid,))
    db.execute("DELETE FROM candidates WHERE id=?", (cid,))
    ranking.compute_rankings()
    flash("Candidate and all their data were deleted.", "ok")
    return redirect(url_for("main.candidates"))

@bp.route("/candidates/<int:cid>/reset", methods=["POST"])
def reset_interview(cid):
    _wipe_conversation(cid)
    db.execute("DELETE FROM answers WHERE candidate_id=?", (cid,))
    db.execute("DELETE FROM evaluations WHERE candidate_id=?", (cid,))
    db.execute("UPDATE candidates SET interview_status='pending', started_at=NULL, finished_at=NULL, last_activity=NULL WHERE id=?", (cid,))
    ranking.compute_rankings()
    flash("Interview reset - the same link can be used again.", "ok")
    return redirect(url_for("main.candidate_details", cid=cid))

@bp.route("/candidates/<int:cid>/manual_audio/<int:qid>", methods=["POST"])
def manual_audio(cid, qid):
    f = request.files.get("audio")
    if not f or _ext(f.filename) not in config.ALLOWED_AUDIO:
        flash("Unsupported audio file. Allowed: " + ", ".join(sorted(config.ALLOWED_AUDIO)), "err")
        return redirect(url_for("main.candidate_details", cid=cid))
    aid = sess.record_answer(cid, qid, f.read(), _ext(f.filename), None)
    sess.process_answer(aid)
    flash("Audio uploaded - transcription and scoring are running (page refreshes automatically).", "ok")
    return redirect(url_for("main.candidate_details", cid=cid))

@bp.route("/answers/<int:aid>/reeval", methods=["POST"])
def reeval(aid):
    a = db.query("SELECT * FROM answers WHERE id=?", (aid,), one=True) or abort(404)
    db.execute("UPDATE answers SET status='processing', error=NULL WHERE id=?", (aid,))
    pipeline.enqueue(aid)
    return redirect(url_for("main.candidate_details", cid=a["candidate_id"]))

@bp.route("/audio/<int:aid>")
def audio(aid):
    a = db.query("SELECT audio_file FROM answers WHERE id=?", (aid,), one=True) or abort(404)
    return send_from_directory(config.UPLOAD_FOLDER, Path(a["audio_file"]).name)

# ------------------------------------------------------------------ real phone calls (SIP)
@bp.route("/phone/settings", methods=["POST"])
def phone_settings():
    for k in ("sip_server", "sip_port", "sip_user", "sip_password", "caller_number", "dial_format", "sip_my_ip", "sip_local_port"):
        v = request.form.get(k, "").strip()
        if k == "sip_password" and not v: continue            # empty = keep the saved password
        db.set_setting(k, v)
    phone_call.reset_phone()
    flash("Phone-call settings saved.", "ok")
    return redirect(url_for("main.candidates"))

def _can_call():
    if not phone_call.available(): flash("Phone calls need pyVoIP: run  pip install -r requirements-optional.txt  and restart.", "warn"); return False
    if not phone_call.configured(): flash("Enter the SIP server, username and password first (Phone call settings).", "warn"); return False
    return True

@bp.route("/candidates/<int:cid>/call", methods=["POST"])
def call_candidate(cid):
    c = db.query("SELECT * FROM candidates WHERE id=?", (cid,), one=True) or abort(404)
    if _can_call():
        if not c["phone"]: flash("This candidate has no phone number.", "err")
        elif c["interview_status"] == "completed": flash("Interview already completed. Press Reset on the candidate page to call again.", "warn")
        else:
            phone_call.enqueue(cid); flash(f"Calling {c['name']} now. Watch the status on this page and on the Dashboard.", "ok")
    return redirect(url_for("main.candidates"))

@bp.route("/phone/call_all", methods=["POST"])
def call_all():
    if _can_call():
        n = 0
        for c in db.query("SELECT id, phone FROM candidates WHERE interview_status='pending' AND phone<>'' AND role_id=?",
                          (request.form.get("role_id", type=int),)):
            phone_call.enqueue(c["id"]); n += 1
        flash(f"{n} candidate(s) queued. They are called one after another.", "ok")
    return redirect(url_for("main.candidates"))

@bp.route("/api/calls")
def api_calls():
    return jsonify(phone_call.CALLS)

@bp.route("/candidates/<int:cid>/force_finish", methods=["POST"])
def force_finish(cid):
    conversation.finish(cid)
    flash("Interview closed - the answers collected so far are being scored.", "ok")
    return redirect(url_for("main.candidate_details", cid=cid))

@bp.route("/candidates/<int:cid>/transcript.txt")
def transcript_download(cid):
    c = db.query("SELECT name FROM candidates WHERE id=?", (cid,), one=True) or abort(404)
    return Response(conversation.transcript_text(cid), mimetype="text/plain",
                    headers={"Content-Disposition": f"attachment; filename=conversation_{cid}.txt"})

@bp.route("/turn_audio/<int:tid>")
def turn_audio(tid):
    t = db.query("SELECT * FROM turns WHERE id=?", (tid,), one=True) or abort(404)
    if t["speaker"] == "AI":
        p = tts.synthesize(t["text"])
        return send_file(p, mimetype="audio/wav") if p else Response(status=204)
    if not t["audio_file"]: abort(404)
    return send_from_directory(config.UPLOAD_FOLDER, Path(t["audio_file"]).name)

@bp.route("/full_recording/<int:cid>")
def full_recording(cid):
    f = config.UPLOAD_FOLDER / f"full_{cid}.webm"
    if f.exists(): return send_file(f, mimetype="audio/webm")
    f = config.UPLOAD_FOLDER / f"full_{cid}.wav"
    if f.exists(): return send_file(f, mimetype="audio/wav")
    abort(404)

@bp.route("/followup_audio/<int:aid>/<int:i>")
def followup_audio(aid, i):
    a = db.query("SELECT followup_json FROM answers WHERE id=?", (aid,), one=True) or abort(404)
    try: fu = json.loads(a["followup_json"] or "[]")[i]
    except Exception: abort(404)
    return send_from_directory(config.UPLOAD_FOLDER, Path(fu["audio"]).name)

@bp.route("/candidates/<int:cid>/ai_review", methods=["POST"])
def ai_review(cid):
    v = request.form.get("value", "")
    if v not in ("", "cleared", "confirmed"): abort(400)
    db.execute("UPDATE candidates SET ai_review=? WHERE id=?", (v, cid))
    ranking.compute_rankings()
    report_generator.generate_pdf(cid)
    flash({"cleared": "Marked as verified human - red flag removed (rank unchanged).",
           "confirmed": "Marked as confirmed AI-assisted.", "": "Review cleared - automatic detector is used again."}[v], "ok")
    return redirect(url_for("main.candidate_details", cid=cid))

@bp.route("/drive/connect", methods=["POST"])
def drive_connect():
    if not drive_uploader.available() or not drive_uploader.has_credentials():
        flash("Google Drive: " + drive_uploader.status(), "warn"); return redirect(url_for("main.candidates"))
    def work():
        try: drive_uploader.connect(); drive_uploader.sync_all()
        except Exception as e: print("[Drive] connect failed:", e)
    threading.Thread(target=work, daemon=True).start()
    flash("A Google sign-in window is opening on this PC. Approve it, then reports upload automatically.", "ok")
    return redirect(url_for("main.candidates"))

@bp.route("/drive/sync", methods=["POST"])
def drive_sync():
    if not drive_uploader.ready(): flash("Google Drive: " + drive_uploader.status(), "warn")
    else:
        for e in db.query("SELECT candidate_id FROM evaluations"): report_generator.generate_pdf(e["candidate_id"])
        flash(f"{drive_uploader.sync_all()} report(s) uploaded to Google Drive.", "ok")
    return redirect(request.referrer or url_for("main.dashboard"))

@bp.route("/resume/<int:cid>")
def resume(cid):
    c = db.query("SELECT resume_path FROM candidates WHERE id=?", (cid,), one=True)
    if not c or not c["resume_path"]: abort(404)
    return send_from_directory(config.RESUME_FOLDER, Path(c["resume_path"]).name)

@bp.route("/report/<int:cid>.pdf")
def report(cid):
    p = report_generator.generate_pdf(cid) or abort(404)
    return send_file(p, mimetype="application/pdf")

@bp.route("/recalculate", methods=["POST"])
def recalc():
    ranking.compute_rankings()
    for e in db.query("SELECT candidate_id FROM evaluations"): report_generator.generate_pdf(e["candidate_id"])
    flash("Ranking recalculated.", "ok")
    return redirect(url_for("main.dashboard"))

@bp.route("/export/excel")
def export_excel():
    ranking.compute_rankings()
    for e in db.query("SELECT candidate_id FROM evaluations"): report_generator.generate_pdf(e["candidate_id"])
    return send_file(excel_generator.generate_excel(), as_attachment=True, download_name="candidate_ranking.xlsx")

@bp.route("/questions", methods=["GET", "POST"])
def questions():
    if request.method == "POST":
        text = request.form.get("question", "").strip()
        rid = request.form.get("role_id", type=int)
        if text and rid:
            n = db.query("SELECT COALESCE(MAX(order_number),0)+1 n FROM interview_questions WHERE role_id=?", (rid,), one=True)["n"]
            db.execute("INSERT INTO interview_questions(question,category,maximum_score,order_number,role_id,needs_followup) VALUES(?,?,?,?,?,?)",
                       (text, request.form.get("category", "General"), float(request.form.get("maximum_score") or 100), n, rid,
                        role_banks.needs_followup(text)))
            tts.synthesize(text)
            flash("Question added.", "ok")
        return redirect(url_for("main.questions", role=rid))
    rid, roles = pick_role()
    return render_template("questions.html", qs=db.query("SELECT * FROM interview_questions WHERE role_id=? ORDER BY order_number, id", (rid,)),
                           roles=roles, rid=rid, rubric=config.RUBRIC, labels=config.RUBRIC_LABELS, max_min=config.INTERVIEW_MAX_MIN,
                           chain=role_banks.chain_for(rid) if rid else [])

@bp.route("/questions/<int:qid>/toggle", methods=["POST"])
def toggle_question(qid):
    q = db.query("SELECT role_id, active FROM interview_questions WHERE id=?", (qid,), one=True) or abort(404)
    db.execute("UPDATE interview_questions SET active=? WHERE id=?", (0 if q["active"] else 1, qid))
    return redirect(url_for("main.questions", role=q["role_id"]))

@bp.route("/questions/auto_select", methods=["POST"])
def auto_select():
    rid = request.form.get("role_id", type=int)
    role_banks.apply_default_selection(rid)
    flash("Selected one question per section (about 25-30 minutes).", "ok")
    return redirect(url_for("main.questions", role=rid))

@bp.route("/questions/select_all", methods=["POST"])
def select_all():
    rid = request.form.get("role_id", type=int)
    db.execute("UPDATE interview_questions SET active=1 WHERE role_id=?", (rid,))
    return redirect(url_for("main.questions", role=rid))

@bp.route("/questions/reload_banks", methods=["POST"])
def reload_banks():
    role_banks.seed_roles(force=True)
    flash("Role question banks (from your PDFs) loaded / refreshed.", "ok")
    return redirect(url_for("main.questions"))

_keys_state = {"running": False}
@bp.route("/questions/generate_keys", methods=["POST"])
def generate_keys():
    rid = request.form.get("role_id", type=int)
    if _keys_state["running"]: flash("Answer-key generation is already running.", "warn"); return redirect(url_for("main.questions", role=rid))
    def work():
        _keys_state["running"] = True
        try:
            role = db.query("SELECT name FROM job_roles WHERE id=?", (rid,), one=True)["name"]
            for q in db.query("SELECT * FROM interview_questions WHERE role_id=? AND (answer_key IS NULL OR answer_key='')", (rid,)):
                try: db.execute("UPDATE interview_questions SET answer_key=? WHERE id=?", (ai_evaluator.generate_answer_key(q["question"], role), q["id"]))
                except Exception: break
        finally: _keys_state["running"] = False
    threading.Thread(target=work, daemon=True).start()
    flash("Generating grading guides with the local AI in the background (needs Ollama running). Refresh in a few minutes.", "ok")
    return redirect(url_for("main.questions", role=rid))

@bp.route("/questions/<int:qid>/delete", methods=["POST"])
def delete_question(qid):
    q = db.query("SELECT role_id FROM interview_questions WHERE id=?", (qid,), one=True)
    db.execute("DELETE FROM answers WHERE question_id=?", (qid,))
    db.execute("DELETE FROM interview_questions WHERE id=?", (qid,))
    ranking.compute_rankings()
    return redirect(url_for("main.questions", role=q["role_id"] if q else None))

@bp.route("/system_check")
def system_check():
    import requests as rq
    out = {}
    try:
        r = rq.get(config.OLLAMA_URL.replace("/api/generate", "/api/tags"), timeout=3)
        names = [m["name"] for m in r.json().get("models", [])]
        out["ollama"] = "OK" if any(n.startswith(config.OLLAMA_MODEL.split(":")[0]) for n in names) else f"running but model {config.OLLAMA_MODEL} not installed"
    except Exception: out["ollama"] = "NOT RUNNING"
    try: import faster_whisper; out["whisper"] = f"installed ({config.WHISPER_MODEL})"
    except Exception: out["whisper"] = "faster-whisper NOT installed"
    out["drive"] = drive_uploader.status()
    out["tts"] = "Piper ready" if (config.PIPER_MODEL_DIR / f"{config.PIPER_VOICE}.onnx").exists() else "Piper voice missing - browser voice fallback"
    return jsonify(out)

# ------------------------------------------------------------------ candidate live interview (public, token protected)
def _cand_by_token(token):
    return db.query("SELECT * FROM candidates WHERE token=?", (token,), one=True) or abort(404)

def _audio_ext(f):
    m = f.mimetype or ""
    return "ogg" if "ogg" in m else ("mp4" if "mp4" in m else "webm")

def _say(token, m):
    if m: m["audio"] = f"/interview/{token}/say/{m['id']}"
    return m

@bp.route("/interview/<token>")
def interview(token):
    c = _cand_by_token(token)
    role = db.query("SELECT name FROM job_roles WHERE id=?", (c["role_id"],), one=True)
    n = db.query("SELECT COUNT(*) n FROM interview_questions WHERE role_id=? AND active=1", (c["role_id"],), one=True)["n"]
    return render_template("interview.html", c=c, token=token, role=role["name"] if role else "", nq=n,
                           done=c["interview_status"] == "completed", max_min=config.INTERVIEW_MAX_MIN)

@bp.route("/interview/<token>/say/<int:tid>")
def interview_say(token, tid):
    c = _cand_by_token(token)
    t = db.query("SELECT * FROM turns WHERE id=? AND candidate_id=? AND speaker='AI'", (tid, c["id"]), one=True) or abort(404)
    p = tts.synthesize(t["text"])
    return send_file(p, mimetype="audio/wav") if p else Response(status=204)

@bp.route("/interview/<token>/start", methods=["POST"])
def interview_start(token):
    c = _cand_by_token(token)
    if c["interview_status"] == "completed": return jsonify(ok=False, error="This interview is already completed."), 409
    try: m = conversation.start(c["id"])
    except ValueError as e: return jsonify(ok=False, error=str(e)), 400
    if m is None: return jsonify(ok=False, error="This interview is already completed."), 409
    return jsonify(ok=True, say=_say(token, m))

@bp.route("/interview/<token>/reply", methods=["POST"])
def interview_reply(token):
    c = _cand_by_token(token)
    st = conversation.get_state(c["id"])
    if not st or st["stage"] == "done": return jsonify(ok=False, error="Interview is not active."), 409
    f = request.files.get("audio")
    spoke = request.form.get("spoke") == "1" and f is not None
    rt = float(request.form.get("response_time", 0) or 0)
    dur = float(request.form.get("duration", 0) or 0)
    r = conversation.submit_reply(c["id"], f.read() if spoke else None, _audio_ext(f) if f else "webm", rt, dur, spoke)
    if r["ack"]: r["ack"]["audio"] = f"/interview/{token}/say/{r['ack']['id']}"
    return jsonify(ok=True, **r)

@bp.route("/interview/<token>/next")
def interview_next(token):
    c = _cand_by_token(token)
    r = conversation.job_result(request.args.get("job", ""))
    if not r or not r.get("ready"): return jsonify(ready=False)
    if r.get("error"): return jsonify(ready=True, error=r["error"])
    return jsonify(ready=True, say=_say(token, r["say"]))

@bp.route("/interview/<token>/rec", methods=["POST"])
def interview_rec(token):
    """Receives chunks of the whole-conversation recording (AI voice + candidate voice)."""
    c = _cand_by_token(token)
    f = request.files.get("chunk")
    if not f: return jsonify(ok=False), 400
    path = config.UPLOAD_FOLDER / f"full_{c['id']}.webm"
    if path.exists() and path.stat().st_size > 300 * 1024 * 1024: return jsonify(ok=False), 413
    with open(path, "ab") as fh: fh.write(f.read())
    return jsonify(ok=True)
