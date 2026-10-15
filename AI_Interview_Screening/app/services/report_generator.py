"""PDF record of every candidate's questions, answers (transcripts), scores and flags (ReportLab)."""
import json
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from .. import database as db, config

def generate_pdf(candidate_id):
    c = db.query("SELECT * FROM candidates WHERE id=?", (candidate_id,), one=True)
    e = db.query("SELECT * FROM evaluations WHERE candidate_id=?", (candidate_id,), one=True)
    if not c: return None
    ans = db.query("""SELECT a.*, q.question, q.order_number FROM answers a JOIN interview_questions q ON q.id=a.question_id
                      WHERE a.candidate_id=? ORDER BY q.order_number""", (candidate_id,))
    ss = getSampleStyleSheet()
    h = ParagraphStyle("h", parent=ss["Title"], textColor=colors.HexColor("#0b1f44"), fontSize=20)
    q = ParagraphStyle("q", parent=ss["Heading3"], textColor=colors.HexColor("#1d4ed8"))
    body = ss["BodyText"]; small = ParagraphStyle("s", parent=body, fontSize=8.5, textColor=colors.HexColor("#475569"))
    flagged = bool(e and e["ai_suspected"])
    path = config.REPORT_FOLDER / f"candidate_{candidate_id}_report.pdf"
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=2*cm, rightMargin=2*cm, topMargin=1.8*cm, bottomMargin=1.8*cm)
    role = db.query("SELECT name FROM job_roles WHERE id=?", (c["role_id"],), one=True)
    S = [Paragraph("Interview Screening Report", h), Paragraph("Role: " + escape(role["name"] if role else "-"), ss["Heading4"])]
    nm = escape(c["name"] or "")
    S.append(Paragraph(f'<font color="{"#dc2626" if flagged else "#0b1f44"}"><b>{nm}</b></font>'
                       + (' &nbsp;<font color="#dc2626">[AI-ASSISTED ANSWERS SUSPECTED - HR to verify]</font>' if flagged else ""), ss["Heading2"]))
    info = [["Phone", c["phone"] or "-", "Email", c["email"] or "-"],
            ["College", c["college"] or "-", "Degree", f'{c["degree"] or "-"} {c["specialization"] or ""}'.strip()]]
    if e: info.append(["Rank", f'#{e["rank"]}', "Score", f'{e["total_score"]:.1f} / {e["max_score"]:.0f}  ({e["percentage"]:.1f}%)'])
    t = Table([[Paragraph(escape(str(x)), small) for x in r] for r in info], colWidths=[2.2*cm, 6.3*cm, 2.2*cm, 6.3*cm])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), .3, colors.lightgrey), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2ff")),
                           ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#eef2ff"))]))
    S += [t, Spacer(1, 10), HRFlowable(width="100%", color=colors.HexColor("#cbd5e1"))]
    for a in ans:
        try: ev = json.loads(a["evaluation_json"] or "{}")
        except Exception: ev = {}
        S.append(Paragraph(f'Q{a["order_number"]}. {escape(a["question"])}', q))
        S.append(Paragraph("<b>Conversation:</b><br/>" + escape(a["transcript"] or "(no answer)").replace("\n", "<br/>"), body))
        rt = f'{a["response_time"]:.1f}s' if a["response_time"] is not None else "n/a"
        S.append(Paragraph(f'Response time: {rt} &nbsp;|&nbsp; Raw score: {a["raw_score"] or 0:.1f} &nbsp;|&nbsp; '
                           f'Time penalty: {a["penalty_pct"] or 0:.0f}% &nbsp;|&nbsp; <b>Final: {a["score"] or 0:.1f}</b> &nbsp;|&nbsp; '
                           f'AI-likelihood: {a["ai_likelihood"] or 0:.0f}%', small))
        if ev:
            S.append(Paragraph("Rubric: " + ", ".join(f'{config.RUBRIC_LABELS.get(k, k)} {ev.get(k, 0)}/{v}' for k, v in config.RUBRIC.items()), small))
            for lbl, key in (("Strengths", "strengths"), ("Weaknesses", "weaknesses")):
                if ev.get(key): S.append(Paragraph(f"<b>{lbl}:</b> " + escape("; ".join(map(str, ev[key]))), small))
            if ev.get("summary"): S.append(Paragraph("<b>AI summary:</b> " + escape(str(ev["summary"])), small))
            if ev.get("ai_heuristic_reasons"): S.append(Paragraph("AI-style signals: " + escape("; ".join(ev["ai_heuristic_reasons"])), small))
        S += [Spacer(1, 6), HRFlowable(width="100%", color=colors.HexColor("#e2e8f0"))]
    turns = db.query("SELECT * FROM turns WHERE candidate_id=? ORDER BY id", (candidate_id,))
    if turns:
        S.append(Spacer(1, 12)); S.append(Paragraph("Full interview transcript", q))
        for t in turns:
            if t["speaker"] == "AI": lab = {"question": "AI (question)", "followup": "AI (follow-up)"}.get(t["kind"], "AI")
            else: lab = (c["name"] or "Candidate") + " (reply)"
            S.append(Paragraph(f"<b>{escape(lab)}:</b> {escape(t['text'] or '')}", small))
        S.append(Spacer(1, 8))
    S.append(Paragraph("Scores and AI-suspicion flags are decision-support only; a human must make the final decision.", small))
    doc.build(S)
    if e: db.execute("UPDATE evaluations SET pdf_path=? WHERE candidate_id=?", (str(path), candidate_id))
    return path
