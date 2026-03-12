# Bulk Email Verifier

Local bulk email verifier built with Python and Streamlit. No login, no database.

## Features

- **Input**: Upload CSV or paste emails into a text area
- **Deduplication**: Duplicates removed automatically
- **Verification**: For each email:
  - Valid syntax (RFC-style)
  - MX records (via dnspython)
  - SMTP handshake (smtplib, RCPT TO only)
- **Live progress bar** during verification
- **Results table**: Email, Status, Reason
- **Statuses**: Valid, Invalid, Risky, Unknown
- **Export**: Download only valid emails as CSV
- **Rate limiting**: 1–2 second random delay between SMTP checks
- **Concurrency**: Max 5 threads

## Setup

From the **workspace root** (`google-maps-extractor-main`), or from the `bulk-email-verifier` folder:

```powershell
cd bulk-email-verifier
pip install -r requirements.txt
```

If your terminal is currently in `email-verifier`, go up one level first:

```powershell
cd ..\bulk-email-verifier
pip install -r requirements.txt
```

## Run

```powershell
streamlit run app.py
```

Opens in the browser at `http://localhost:8501`.

## Deploy on Streamlit Cloud

1. Push this folder to GitHub (either as the **root** of its own repo, or inside a repo).
2. Go to [share.streamlit.io](https://share.streamlit.io), sign in, **New app**.
3. **Repo**: `your-username/your-repo`
4. **Branch**: `main` (or your default).
5. **Main file path**:
   - If the repo root is `bulk-email-verifier`: use `app.py`
   - If the repo root is the parent (e.g. `google-maps-extractor-main`): use `bulk-email-verifier/app.py`
6. **Requirements file** (if not at repo root): set to `bulk-email-verifier/requirements.txt` when the repo root is the parent folder.
7. **Advanced settings** → Python version: `3.10` (optional; add a `runtime.txt` with `python-3.10` in the app folder if you want to pin it).
8. Deploy. The app adds the correct path so `verifier` is found when run from the repo root.

## Dependencies

- streamlit
- pandas
- dnspython

Uses only standard library `smtplib` for SMTP checks.
