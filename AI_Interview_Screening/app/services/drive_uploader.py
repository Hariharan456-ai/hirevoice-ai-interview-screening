"""Optional: upload each candidate's Q&A PDF to YOUR Google Drive and store the shareable link.
Uses the free Google Drive API with a one-time OAuth login (no paid service). Everything fails softly if not set up."""
from .. import config, database as db

SCOPES = ["https://www.googleapis.com/auth/drive.file"]

def available():
    try:
        import googleapiclient, google_auth_oauthlib  # noqa
        return True
    except Exception:
        return False

def has_credentials(): return config.DRIVE_CREDENTIALS_FILE.exists()
def connected(): return config.DRIVE_TOKEN_FILE.exists()
def ready(): return available() and connected()

def status():
    if not available(): return "not installed (pip install -r requirements-optional.txt)"
    if not has_credentials(): return "credentials.json missing (see README, step 'Google Drive')"
    return "connected" if connected() else "not connected - click Connect Google Drive"

def _creds():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    c = Credentials.from_authorized_user_file(str(config.DRIVE_TOKEN_FILE), SCOPES)
    if c.expired and c.refresh_token:
        c.refresh(Request()); config.DRIVE_TOKEN_FILE.write_text(c.to_json())
    return c

def connect():
    """Opens the Google login in the browser of THIS PC (blocking until you approve)."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(config.DRIVE_CREDENTIALS_FILE), SCOPES)
    c = flow.run_local_server(port=0, prompt="consent")
    config.DRIVE_TOKEN_FILE.write_text(c.to_json())

def _service():
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=_creds(), cache_discovery=False)

def _folder(svc):
    fid = db.get_setting("drive_folder_id")
    if fid: return fid
    f = svc.files().create(body={"name": config.DRIVE_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"}, fields="id").execute()
    db.set_setting("drive_folder_id", f["id"])
    return f["id"]

def upload_pdf(candidate_id):
    """Uploads (or updates) the candidate's PDF; returns the Drive link or None."""
    if not ready(): return None
    e = db.query("SELECT * FROM evaluations WHERE candidate_id=?", (candidate_id,), one=True)
    if not e or not e["pdf_path"]: return None
    from googleapiclient.http import MediaFileUpload
    c = db.query("SELECT name FROM candidates WHERE id=?", (candidate_id,), one=True)
    svc = _service()
    media = MediaFileUpload(e["pdf_path"], mimetype="application/pdf")
    name = f"Rank{e['rank']}_{c['name']}_interview_record.pdf"
    try:
        if e["drive_file_id"]:
            f = svc.files().update(fileId=e["drive_file_id"], body={"name": name}, media_body=media, fields="id,webViewLink").execute()
        else:
            raise KeyError
    except Exception:
        f = svc.files().create(body={"name": name, "parents": [_folder(svc)]}, media_body=media, fields="id,webViewLink").execute()
        if config.DRIVE_ANYONE_WITH_LINK:
            svc.permissions().create(fileId=f["id"], body={"role": "reader", "type": "anyone"}).execute()
    db.execute("UPDATE evaluations SET drive_file_id=?, drive_link=? WHERE candidate_id=?", (f["id"], f["webViewLink"], candidate_id))
    return f["webViewLink"]

def sync_all():
    n = 0
    for e in db.query("SELECT candidate_id FROM evaluations WHERE pdf_path IS NOT NULL"):
        try:
            if upload_pdf(e["candidate_id"]): n += 1
        except Exception as ex:
            print("[Drive] upload failed:", ex)
    return n
