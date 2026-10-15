# HireVoice AI — Interview Screening (v6: link interview + real phone calls)

HR uploads student resumes → the system reads **name + phone** from each PDF → each candidate gets a personal **live voice-interview link** →
an AI voice asks the questions → the candidate answers by voice → **Whisper** transcribes → a **local LLM** grades → **response-time penalty** and
**AI-answer detection** are applied → live ranking on the HR dashboard → **PDF record** of every Q&A + **Excel** ranking.
Everything runs on your own PC. No paid APIs, no GPU.

## Models used (all pre-trained, run locally)
| Job | Model | Runs via |
|---|---|---|
| Voice → text | Whisper `base.en` (faster-whisper, int8 CPU) | Python |
| Text → voice (asks questions) | Piper neural voice `en_US-lessac-medium` (falls back to your browser's voice) | Python |
| Grading, best-answer scoring, AI-likelihood | `qwen2.5:3b` | Ollama |
| Resume reading | PDF text + regex; LLM fallback for the name | pypdf / Ollama |

## ⚠ Honest note about "phone calls"
Calling a real mobile number needs a telephony provider (Twilio, Exotel…) which is paid — no local model can dial a phone.
So this project does the same job **without the phone network**: HR sends each candidate a link (📞 Call button, WhatsApp, SMS, or copy-link, using the
candidate phone read from the resume). The candidate opens it on a phone/laptop and the AI talks to them live, like a call. HR's number is stored and included in the invite message.


## NEW in v6 — real phone calls (AI rings the student)
Everything else works exactly as in v5.1 (the link interview still works). Phone mode uses the same AI interviewer, follow-ups, 30-minute limit, scoring, ranking, red AI flag, transcripts and PDF.

**What you need (code cannot ring a phone without a phone line):** SIP details for your calling number **044-48135764** from the company that provides it:
`SIP server/domain`, `port` (usually 5060), `username`, `password`, and confirmation that (a) outbound calls are allowed, (b) codec **G.711 (PCMU/PCMA)** is supported, (c) the dial format for mobiles (+91XXXXXXXXXX or 0XXXXXXXXXX). Ask them: "Please give me SIP trunk / SIP account credentials for outbound calls."

**Setup**
1. `pip install -r requirements-optional.txt` (installs pyVoIP), then restart `python run.py`.
2. Candidates page → **Real phone calls (SIP)** → enter server, port, username, password, dial format → **Save**. Status must say *ready*.
3. **First test on YOUR OWN mobile:** add yourself as a candidate (your number) → press **🤖📞 AI call**. Pick up. The AI says: "This call is recorded… is this a good time?" Say "yes".
4. For many candidates: choose the role → **Call all "Not started" candidates** (they are called one after another, 8 s apart).
5. Live status on the Candidates page (dialing → ringing → in call → finished / no answer / hung up) and on the Dashboard (Live now Q3/8).

**Good to know**
* The PC must stay on and online. Allow Python through Windows Firewall (UDP 5060 and 10000-20000). If the call connects but you hear nothing (one-way audio), your router/NAT is blocking the audio: enter this PC's local IP in "This PC's IP", or ask the provider for a NAT-friendly / TCP-less setup.
* Voice: Piper if installed, otherwise the Windows built-in voice (automatic).
* Recording: the whole call (AI + student) is saved as one wav, plus each answer and the labelled transcript.
* If the student says "I'm busy / call later", the AI politely ends and the candidate stays "Not started". If the student hangs up midway, the answers so far are scored.
* A phone number alone is not enough: if the number is a normal landline with no SIP account, ask the provider for SIP/“SIP trunk”, or use a cloud-telephony company that provides it.
* Telephone lines only carry 8 kHz audio, so speech recognition is a little less accurate than the link interview. `WHISPER_MODEL=base.en` is better than `tiny.en` for calls.
* Tell candidates in advance that the call is AI-run and recorded.

## NEW in v5
1. **HR login.** Default: **email `hr@hirevoice.com`  password `Hr@12345`**. Change it after first sign-in (sidebar → Account). Everything except the candidate's private interview link needs this login. (Override with env vars `HR_EMAIL` / `HR_PASSWORD`.) 5 wrong tries lock the login for 5 minutes.
2. **Live status fixed.** Not started → **● Live now (Q3/8)** → Scoring… → **Completed**. The status is now set by the server (not the browser), the dashboard refreshes every 3 seconds, and if a candidate disappears for 10 minutes it shows **Stopped early** — open the candidate and press **Finish & score now**.
3. **Human-like interviewer.** The AI greets the candidate and makes small talk, then asks each question. After every answer it replies naturally ("Okay, I see.") and, when useful, asks a **follow-up written from what the candidate just said** — e.g. if they list tools (Python, Excel, KOHA…) it picks **one at random** and asks about it ("Why did you choose Excel for this?"). It does not always follow up; it says "Okay, let's move to the next question / topic", and closes with a thank-you. If the candidate is silent it says "I couldn't hear an answer, that's okay" and moves on.
4. **30-minute limit.** `INTERVIEW_MAX_MIN` (config.py). The AI budgets follow-ups from the time left, keeps 1 minute for goodbye, and ends politely if time runs out. By default **one question per section** is asked (about 6–10 questions ≈ 25 min). On the **Questions** page tick/untick exactly which questions are asked (the page shows the estimated minutes).
5. **Everything is recorded.** (a) one audio file of the **whole conversation** (AI voice + candidate), (b) every candidate answer as separate audio, (c) a labelled transcript:
   `AI (question): …` / `Subramanian (reply): …` / `AI (follow-up): …` — saved in `data/transcripts/`, shown as a chat on the candidate page, downloadable as .txt, and included in the PDF. (Whole-conversation audio contains the AI voice only when Piper is installed; with the browser voice it contains the candidate only.)
6. **Ranking:** highest score first, ranked inside each job role. AI-suspected candidates are shown in **red with their score and rank**; HR can verify and remove the flag. Tune sensitivity with `AI_SUSPECT_THRESHOLD` (lower = flags more).
7. Speed tips for a CPU-only PC: `set WHISPER_MODEL=tiny.en`, keep Ollama running, and the AI replies in about 5–15 seconds (it says an acknowledgement while thinking).

## NEW in v4
1. **Google Drive links** for every candidate's Q&A PDF (shown on the dashboard ☁ and as a clickable link in Excel). One-time setup (free):
   1. Go to https://console.cloud.google.com → create a project → *APIs & Services → Library* → enable **Google Drive API**.
   2. *OAuth consent screen* → External → add **your own Gmail as Test user**.
   3. *Credentials → Create credentials → OAuth client ID → Desktop app* → **Download JSON** → save it as `data/google/credentials.json`.
   4. `pip install -r requirements-optional.txt`, restart, open **Candidates** → **Connect Google Drive** → approve the Google window. A folder "HireVoice Interview Reports" is created and PDFs upload automatically after each interview. Links open only for your Google account (set `DRIVE_ANYONE_WITH_LINK=True` in config to allow anyone-with-link).
2. **Scanned / photo resumes** are read with a local OCR model (RapidOCR) — installed by `requirements-optional.txt`. Always double-check name/phone shown after upload.
3. **AI-answer flag review:** the red flag is a hint. Open the candidate → *AI-assistance check* → **Verified human** (removes red), **Confirm AI-assisted**, or back to automatic. Your decision overrides the detector; ranks never change.
4. **Follow-up audio** is now playable on the candidate page (under each answer) with the reply text.

## How the system works (simple words)
1. **HR prepares:** picks the job role (questions are already loaded from your PDFs) and types HR's phone number.
2. **HR uploads resumes** (PDF, many at once). The system reads each resume and fills name, phone, email, degree, college. HR checks them.
3. **HR sends each candidate a link** (Call / WhatsApp / SMS buttons).
4. **Candidate opens the link** and allows the microphone. An AI voice asks the questions one by one.
5. **Candidate speaks the answer.** The system times how fast they started. If they said "Yes, I have experience", the AI asks follow-ups: "give a real example → what was your role → what challenge → what result".
6. **The computer listens and writes it down** (speech → text) and saves the text.
7. **The AI marks the answer** (knowledge, communication, problem solving, relevance, completeness), cuts marks for slow replies, and checks if the answer sounds copied from an AI tool.
8. **Ranking:** candidates for the same role are ranked by score. The dashboard updates live. A red name means "possibly AI-assisted" — rank is still shown.
9. **HR reviews:** opens a candidate, listens to audio, reads the transcript, can clear or confirm the AI flag.
10. **Records:** a PDF of every question + answer per candidate (also on Google Drive if connected) and an Excel ranking sheet (one sheet per role).

## How to run — step by step (Windows)
1. Download `AI_Interview_Screening_v4.zip` → right-click → **Extract All** (e.g. `C:\AI_Interview_Screening`).
2. Install **Python 3.11/3.12** (python.org, tick "Add to PATH") and **Ollama** (ollama.com). In Command Prompt: `ollama pull qwen2.5:3b`
3. Open the folder in **VS Code** → *Terminal → New Terminal* and run:
```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-optional.txt
python download_models.py
python run.py
```
4. Open **http://127.0.0.1:5000**. Next time just double-click `start.bat` (keep Ollama running).
5. Test yourself: Candidates → add a test candidate → click **Open** → allow the microphone → answer.
6. For candidates on their own phones use `python run.py --https` (same Wi-Fi) or a free cloudflared tunnel (see "Candidates on their own phones" below).

## NEW in v3 — loaded with your 10 role PDFs
* **11 job roles, 312 questions** are pre-loaded automatically on first start (Accounts/Finance, Assistant Director K-GEO, Librarian KCLAS, Leadership – Institute of Agriculture, Management Faculty Associate + Assistant Professor, Operations Executive RIG, Program Manager Entrepreneurship, Social Impact, Sports Science, Student Counsellor). Edit them in `data/role_question_banks.json` or on the **Questions** page, then press *Load / refresh role question banks*.
* **Choose the role when uploading resumes.** Each candidate is interviewed with *their role's* questions and **ranked only against candidates of the same role** (dashboard has a role selector; Excel has one sheet per role).
* **AI follow-up engine (from your PDFs):** after a strong "Yes / I have experience" answer the AI voice asks the role's follow-up chain — *real example → role → challenge/issue → result* (up to `FOLLOWUP_STEPS`=4, max `FOLLOWUP_MAX_QUESTIONS`=6 questions per interview, both in `app/config.py`). Follow-up replies are added to the answer and graded as evidence; a bare "Yes" with no example scores low.
* **Grading guides:** Questions page → *Generate grading guides (local AI)* creates a "what a strong answer includes" note for every question (one-time, uses Ollama); it is shown to the grader. This is how the PDFs "train" the system — see below.
* Per-answer `*_evaluation.json` files are saved in `data/reports/`; transcripts in `data/transcripts/`; PDF Q&A record per candidate.

## What "training with your data" means here
No neural network is trained (needs GPU + hundreds of labelled interviews). Instead your PDFs are used as: (1) role question banks, (2) role-specific follow-up chains, (3) auto-generated grading guides per question, (4) your own calibration examples in `data/calibration_examples.json` (give 3–5 real answers + the scores you would give — the strictness of the AI follows them). After 20–30 real interviews, compare the AI ranking with HR's own ranking and add examples where they differ.

## Setup (Windows) — step by step
1. **Download & unzip** `AI_Interview_Screening.zip` (right-click → Extract All), e.g. to `C:\AI_Interview_Screening`.
2. **Install Python 3.11 or 3.12** from python.org (tick "Add Python to PATH").
3. **Install Ollama** from https://ollama.com, then open Command Prompt:  `ollama pull qwen2.5:3b`
4. Open the folder in VS Code → Terminal → New Terminal, then:
```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python download_models.py
python run.py
```
   (`download_models.py` needs internet once: downloads Whisper + Piper voice and checks Ollama. If `piper-tts` fails to install, delete that line from requirements.txt — the browser voice is used automatically.)
5. Open **http://127.0.0.1:5000**. Next time just double-click `start.bat`.

## Using it
1. **Questions** page → add your questions (English).
2. **Candidates** page → *Upload resumes* (select many PDFs) → check the extracted names/phones → enter **HR phone number** → *Save*.
3. For each candidate press **📞 Call / WhatsApp / SMS / Copy link** to send the interview link. To try it yourself, press **Open**.
4. Candidate listens to each question, answers by voice (ends automatically after 2.5 s of silence).
5. **Dashboard** updates live (every 4 s): rank, score, reply time, status. **Red name = AI-suspected** (rank still shown).
6. Click a candidate → audio, transcript, rubric bars, strengths/weaknesses, penalty. **Q&A PDF** button / **Export Excel** (Excel has a clickable PDF link per row).
7. Delete a candidate (🗑) removes resume, audio, transcripts, report and scores.

## Candidates on their own phones (microphone needs HTTPS)
Browsers only allow the microphone on `localhost` or HTTPS. Choose one:
* **Same Wi-Fi:** `python run.py --https` → open the "Same Wi-Fi" address shown; candidates accept the browser's certificate warning once.
* **Anywhere (free):** install *cloudflared* and run `cloudflared tunnel --url http://localhost:5000` → paste the `https://….trycloudflare.com` address in **Public link base** on the Candidates page.

## Scoring rules
* Per answer: Technical 30, Communication 20, Problem solving 20, Role relevance 20, Completeness 10 (edit `RUBRIC` in `app/config.py`).
* **Response time:** measured from the end of the AI's question to the first word. Free for 8 s, then −1 % per second, max −40 % (`PENALTY_*` in config).
* **AI-answer detection:** LLM judgement + statistical style signals (buzzwords, no natural fillers, uniform sentences, enumerated structure, long pause then polished answer). At ≥ 65 % the name turns red. This is a **hint for HR, not proof** — fluent honest candidates can be flagged; always verify before rejecting anyone.
* Ties broken by technical score, then problem solving.

## "Do I need to train it?"  No — but you can tune it
1. **Calibrate grading (recommended, 10 minutes):** edit `data/calibration_examples.json` with real answers and the scores *you* would give. The first 3 are shown to the LLM as examples (few-shot learning) — no GPU needed.
2. **Better accuracy:** `ollama pull qwen2.5:7b` and set `OLLAMA_MODEL=qwen2.5:7b` (needs ~16 GB RAM, slower); `WHISPER_MODEL=small.en` for accents.
3. **Speed:** `WHISPER_MODEL=tiny.en`, keep `qwen2.5:3b`.
4. Fine-tuning a model needs a GPU and hundreds of labelled interviews — not needed for this project.
Set values with environment variables, e.g. `set OLLAMA_MODEL=qwen2.5:7b` before `python run.py`, or edit `app/config.py`.

## Troubleshooting
* **"Local AI model is not running"** → start Ollama and run `ollama pull qwen2.5:3b`. Dashboard sidebar shows a 🟢/🟠 status.
* **First answer is slow** → Whisper loads once (a few seconds); each answer then takes ~5–20 s on an i7.
* **Phone not found in resume** → install the OCR extras (`pip install -r requirements-optional.txt`) or edit the number in the candidate list.
* **Mic blocked on phone** → use HTTPS (see above).
* **Voice sounds robotic** → Piper not installed; the browser voice is being used. Run `pip install piper-tts` then `python download_models.py`.
* **Port busy** → `python run.py --port 5001`.
* Only one interview is processed at a time (queue) — with 30 candidates, results appear as each finishes.

## Folder map
`app/` code (`routes.py`, `services/`: resume_parser, speech_to_text, tts, ai_evaluator, ranking, report_generator, excel_generator, pipeline, interview_session) · `templates/` · `static/` · `data/` (uploads, resumes, transcripts, reports, exports) · `models/piper/` voices.
