"""Real phone-call interviews over SIP (pure-Python pyVoIP, no paid cloud AI).

The app logs in to YOUR SIP line (the one that owns the calling number, e.g. 044-48135764), dials the student, and runs the
same human-like conversation engine as the web interview: TTS voice -> student speaks -> Whisper -> follow-ups -> scoring.
You need SIP credentials (server, port, username, password) from the company that provides the number.
"""
import array, io, queue, re, sys, threading, time, uuid, wave
from .. import database as db, config
from . import conversation, tts

CALLS = {}                       # candidate_id -> {"state": ..., "detail": ...}
TICK = 0.02                      # real-time pacing of the audio loop (seconds); tests set 0
FRAME = 160                      # 20 ms of 8 kHz audio
_q = queue.Queue()
_worker = None
_phone = None
_phone_lock = threading.Lock()


class CallEnded(Exception): pass


def _set(cid, state, detail=""):
    CALLS[cid] = {"state": state, "detail": detail}


# ------------------------------------------------------------------ audio conversion (pure python)
def wav_to_u8_8k(path):
    """Any PCM wav -> 8 kHz, 8-bit unsigned, mono bytes (what pyVoIP sends)."""
    with wave.open(str(path), "rb") as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 2:
        s = array.array("h"); s.frombytes(raw)
        if sys.byteorder == "big": s.byteswap()
    elif sw == 1:
        s = array.array("h", [(b - 128) << 8 for b in raw])
    else:
        raise ValueError("Unsupported wav format")
    if ch > 1: s = array.array("h", [sum(s[i:i + ch]) // ch for i in range(0, len(s), ch)])
    if sr != 8000:
        ratio = sr / 8000.0; m = int(len(s) / ratio); out = array.array("h", [0]) * m
        for i in range(m):
            pos = i * ratio; j = int(pos); f = pos - j; a = s[j]; b = s[j + 1] if j + 1 < len(s) else a
            out[i] = int(a + (b - a) * f)
        s = out
    return bytes((v >> 8) + 128 for v in s)

def u8_to_wav_bytes(data):
    """8-bit unsigned 8 kHz -> 16-bit wav bytes (for Whisper / playback in the dashboard)."""
    s = array.array("h", [(b - 128) << 8 for b in data])
    if sys.byteorder == "big": s.byteswap()
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(s.tobytes())
    return bio.getvalue()

def mix_wav(ai_u8, student_u8):
    """Both sides of the call mixed into one small 8-bit 8 kHz mono wav (~8 KB per second)."""
    n = min(len(ai_u8), len(student_u8))
    mixed = bytes(min(255, max(0, x + y - 128)) for x, y in zip(ai_u8[:n], student_u8[:n]))
    bio = io.BytesIO()
    with wave.open(bio, "wb") as w:
        w.setnchannels(1); w.setsampwidth(1); w.setframerate(8000); w.writeframes(mixed)
    return bio.getvalue()


# ------------------------------------------------------------------ live call audio loop
class CallIO:
    """Runs a 20 ms audio clock: reads the student's audio, sends ours, records both sides."""
    def __init__(self, call, call_state):
        self.call, self.CS = call, call_state
        self.inb, self.outb, self.q = bytearray(), bytearray(), bytearray()
        self._next = time.monotonic()

    def tick(self):
        if self.call.state != self.CS.ANSWERED: raise CallEnded()
        try: frame = self.call.read_audio(FRAME, False) or b""
        except Exception: raise CallEnded()
        frame = (frame + b"\x80" * FRAME)[:FRAME]
        out = bytes(self.q[:FRAME]); del self.q[:FRAME]
        if out:
            out = (out + b"\x80" * FRAME)[:FRAME]
            try: self.call.write_audio(out)
            except Exception: raise CallEnded()
        else:
            out = b"\x80" * FRAME
        self.inb += frame; self.outb += out
        self._next += TICK
        d = self._next - time.monotonic()
        if d > 0: time.sleep(d)
        else: self._next = time.monotonic()
        return frame

    def say(self, data):
        self.q += data
        while self.q: self.tick()
        for _ in range(8): self.tick()                    # short tail so the last word is not cut

    def listen(self, cfg):
        """Waits for the student to speak; measures how fast they started; stops after silence."""
        frames, noise, started, last, vf, i = [], [], None, None, 0, 0
        silence = config.SILENCE_END_SEC
        while True:
            f = self.tick(); frames.append(f); i += 1
            dev = sum(abs(b - 128) for b in f) / len(f)
            if i <= 20: noise.append(dev); continue
            thr = min(max(5.0, (sum(noise) / len(noise)) * 3), 20.0)   # capped so early speech cannot hide itself
            if dev > thr:
                vf += 1; last = i
                if started is None and vf >= 4: started = i - 4
            else: vf = max(0, vf - 1)
            if started is None:
                if i * 0.02 > cfg["noSpeech"]: break
            elif (i - last) * 0.02 > silence or (i - started) * 0.02 > cfg["maxAns"]:
                break
        rt = started * 0.02 if started is not None else float(cfg["noSpeech"])
        dur = (i - started) * 0.02 if started is not None else 0.0
        return b"".join(frames), rt, dur, started is not None


# ------------------------------------------------------------------ SIP phone
def sip_settings():
    g = db.get_setting
    return {"server": g("sip_server"), "port": g("sip_port") or "5060", "user": g("sip_user"), "password": g("sip_password"),
            "caller": g("caller_number") or config.DEFAULT_CALLER_NUMBER, "dial_format": g("dial_format") or "plus91",
            "my_ip": g("sip_my_ip"), "local_port": g("sip_local_port") or "5060"}

def available():
    try:
        import pyVoIP  # noqa
        return True
    except Exception:
        return False

def configured():
    s = sip_settings()
    return bool(s["server"] and s["user"] and s["password"])

def status_text():
    if not available(): return "pyVoIP not installed (pip install -r requirements-optional.txt)"
    if not configured(): return "enter the SIP details below"
    return "ready"

def reset_phone():
    global _phone
    with _phone_lock:
        if _phone:
            try: _phone.stop()
            except Exception: pass
        _phone = None

def _get_phone():
    global _phone
    with _phone_lock:
        if _phone: return _phone
        from pyVoIP.VoIP import VoIPPhone
        s = sip_settings()
        kw = dict(sipPort=int(s["local_port"]), rtpPortLow=10000, rtpPortHigh=20000)
        if s["my_ip"]: kw["myIP"] = s["my_ip"]
        _phone = VoIPPhone(s["server"], int(s["port"]), s["user"], s["password"], **kw)
        _phone.start()
        return _phone

def dial_number(phone):
    d = re.sub(r"\D", "", phone or "")
    if len(d) > 10: d = d[-10:]
    fmt = sip_settings()["dial_format"]
    return {"plus91": "+91" + d, "zero": "0" + d, "91": "91" + d}.get(fmt, d)


def _speak(io, text):
    wav = tts.synthesize(text)
    if not wav: raise RuntimeError("No voice engine available. Install Piper (python download_models.py) or use Windows.")
    io.say(wav_to_u8_8k(wav))

def run_call(cid):
    from pyVoIP.VoIP import CallState
    cand = db.query("SELECT * FROM candidates WHERE id=?", (cid,), one=True)
    if not cand or not cand["phone"]: _set(cid, "failed", "no phone number"); return
    number = dial_number(cand["phone"])
    _set(cid, "dialing", number)
    call = _get_phone().call(number)
    t0 = time.time()
    while call.state != CallState.ANSWERED:
        if call.state == CallState.ENDED or time.time() - t0 > config.CALL_RING_TIMEOUT_SEC:
            try: call.hangup()
            except Exception: pass
            _set(cid, "no_answer", "not answered"); return
        _set(cid, "ringing", number); time.sleep(0.5)
    _set(cid, "in_call", number)
    io = CallIO(call, CallState)
    pending = None
    try:
        m = conversation.start(cid, channel="phone")
        if m is None: return
        while True:
            _speak(io, m["text"])
            if m["done"]: break
            data, rt, dur, spoke = io.listen(m.get("listen") or {"noSpeech": 45, "maxAns": 120})
            sub = conversation.submit_reply(cid, u8_to_wav_bytes(data) if spoke else None, "wav", rt, dur, spoke)
            pending = sub["job"]
            if sub["ack"]: _speak(io, sub["ack"]["text"])
            waited, filler = time.time(), False
            while True:
                r = conversation.job_result(sub["job"])
                if r and r.get("ready"): break
                io.tick()
                if not filler and time.time() - waited > 6: _speak(io, "Just a moment, please."); filler = True
            pending = None
            if r.get("error"):
                _speak(io, "Sorry, there was a technical problem. HR will contact you. Goodbye.")
                conversation.finish(cid); break
            m = r["say"]
        _set(cid, "completed", "")
    except CallEnded:
        _set(cid, "ended_early", "student hung up")
        if pending:                                          # let the last answer finish processing
            for _ in range(80):
                r = conversation.job_result(pending)
                if r and r.get("ready"): break
                time.sleep(0.5)
        st = conversation.get_state(cid)
        if st and st["stage"] != "done": conversation.finish(cid)
    except Exception as e:
        _set(cid, "failed", str(e)); print("[CALL] failed:", e)
        st = conversation.get_state(cid)
        if st and st["stage"] != "done": conversation.finish(cid)
    finally:
        try: call.hangup()
        except Exception: pass
        try:
            (config.UPLOAD_FOLDER / f"full_{cid}.wav").write_bytes(mix_wav(bytes(io.outb), bytes(io.inb)))   # AI + student in one file
        except Exception as e: print("[CALL] recording not saved:", e)


# ------------------------------------------------------------------ queue ("Call all")
def _loop():
    while True:
        cid = _q.get()
        try:
            c = db.query("SELECT interview_status FROM candidates WHERE id=?", (cid,), one=True)
            if c and c["interview_status"] != "completed": run_call(cid)
        except Exception as e:
            _set(cid, "failed", str(e)); print("[CALL]", e)
        time.sleep(config.CALL_GAP_SEC if TICK else 0)

def enqueue(cid):
    global _worker
    if _worker is None:
        _worker = threading.Thread(target=_loop, daemon=True); _worker.start()
    _set(cid, "queued", "")
    _q.put(cid)
