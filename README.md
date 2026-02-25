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

## Dependencies

- streamlit
- pandas
- dnspython

Uses only standard library `smtplib` for SMTP checks.
