"""
Bulk Email Verifier - Streamlit app.
Local only, no login, no database. Upload CSV or paste emails, verify, export valid.
"""
import queue
import sys
import threading
from pathlib import Path

# So "import verifier" works when Streamlit Cloud runs from repo root (e.g. bulk-email-verifier/app.py)
_app_dir = Path(__file__).resolve().parent
if str(_app_dir) not in sys.path:
    sys.path.insert(0, str(_app_dir))

import pandas as pd
import streamlit as st

from verifier import verify_batch_to_queue, _DONE

st.set_page_config(page_title="Bulk Email Verifier", layout="wide")
st.title("Bulk Email Verifier")
st.caption("Upload a CSV or paste emails. Duplicates removed. Stealth verification (syntax → MX → SMTP).")

MAX_WORKERS = 5
# Stealth: longer delays to avoid scanner fingerprint (2.5–5 s)
STEALTH_DELAY_MIN, STEALTH_DELAY_MAX = 2.5, 5.0
NORMAL_DELAY_MIN, NORMAL_DELAY_MAX = 1.0, 2.0


def parse_emails_from_text(text: str) -> list[str]:
    """Extract email-like strings from pasted text (one per line or comma-separated)."""
    if not (text or "").strip():
        return []
    emails = []
    for line in text.replace(",", "\n").splitlines():
        for part in line.split():
            part = part.strip()
            if "@" in part and "." in part:
                emails.append(part.strip())
    return emails


def parse_emails_from_csv(uploaded_file) -> list[str]:
    """Try to find an email column in the CSV."""
    if uploaded_file is None:
        return []
    try:
        df = pd.read_csv(uploaded_file)
    except Exception:
        return []
    if df.empty:
        return []
    # Prefer column named email, Email, E-mail, etc.
    for col in df.columns:
        c = str(col).lower().strip()
        if c in ("email", "e-mail", "email address", "emails"):
            return [x for x in df[col].astype(str).str.strip().dropna().tolist() if "@" in x]
    # Otherwise use first column
    return [x for x in df.iloc[:, 0].astype(str).str.strip().dropna().tolist() if "@" in x]


# Input section
input_method = st.radio("Input method", ["Paste emails", "Upload CSV"], horizontal=True)
emails_raw: list[str] = []

if input_method == "Paste emails":
    paste = st.text_area("Paste emails (one per line or comma-separated)", height=120)
    emails_raw = parse_emails_from_text(paste or "")
else:
    csv_file = st.file_uploader("Upload CSV", type=["csv"])
    emails_raw = parse_emails_from_csv(csv_file)

# Normalize and dedupe
emails_raw = [e.strip().lower() for e in emails_raw if (e or "").strip()]
emails_unique = list(dict.fromkeys(emails_raw))

if not emails_unique:
    st.info("Enter or upload some emails to start.")
    st.stop()

st.success(f"**{len(emails_unique)}** unique emails (from {len(emails_raw)} after removing duplicates).")

# Stealth + result style
with st.expander("Verification options", expanded=False):
    stealth_mode = st.checkbox(
        "Stealth mode (recommended)",
        value=True,
        help="Realistic HELO/EHLO, same-domain MAIL FROM, longer delays. Reduces risk of being flagged as a scanner.",
    )
    result_style = st.radio(
        "Result",
        ["Deliverable / Dead / Unverifiable", "Detailed (Valid, Invalid, Risky, Unknown)"],
        horizontal=True,
        index=0,
    )
simple_result = result_style.startswith("Deliverable")  # Deliverable / Dead = simple
delay_min, delay_max = (STEALTH_DELAY_MIN, STEALTH_DELAY_MAX) if stealth_mode else (NORMAL_DELAY_MIN, NORMAL_DELAY_MAX)

if "verification_results" not in st.session_state:
    st.session_state.verification_results = None

if st.button("Start verification", type="primary"):
    result_queue = queue.Queue()
    results = []

    def run_batch():
        verify_batch_to_queue(
            emails_unique,
            result_queue,
            max_workers=MAX_WORKERS,
            delay_min=delay_min,
            delay_max=delay_max,
            stealth=stealth_mode,
            simple_result=simple_result,
        )

    thread = threading.Thread(target=run_batch)
    thread.start()

    progress_bar = st.progress(0.0, text="Verifying…")
    results_placeholder = st.empty()
    total = len(emails_unique)

    while True:
        try:
            item = result_queue.get(timeout=0.5)
        except queue.Empty:
            if not thread.is_alive():
                break
            n = len(results)
            progress_bar.progress(min(1.0, n / total), text=f"Verifying… {n}/{total}")
            if results:
                df_so_far = pd.DataFrame(results, columns=["Email", "Status", "Reason"])
                df_so_far["Status"] = df_so_far["Status"].replace("Not deliverable", "Dead")
                results_placeholder.dataframe(df_so_far, use_container_width=True)
            continue
        if item is _DONE:
            break
        email, status, reason = item
        results.append((email, status, reason))
        n = len(results)
        progress_bar.progress(min(1.0, n / total), text=f"Verifying… {n}/{total}")
        df_so_far = pd.DataFrame(results, columns=["Email", "Status", "Reason"])
        df_so_far["Status"] = df_so_far["Status"].replace("Not deliverable", "Dead")
        results_placeholder.dataframe(df_so_far, use_container_width=True)

    thread.join()
    progress_bar.progress(1.0, text="Done.")
    st.session_state.verification_results = results
    results_placeholder.empty()

if st.session_state.verification_results is not None:
    df_results = pd.DataFrame(
        st.session_state.verification_results,
        columns=["Email", "Status", "Reason"],
    )
    # Show "Dead" instead of "Not deliverable" in table (matches spreadsheet-sharing wording)
    display_df = df_results.copy()
    display_df["Status"] = display_df["Status"].replace("Not deliverable", "Dead")
    st.dataframe(display_df, use_container_width=True)

    # Infer result style: simple = Deliverable / Not deliverable / Unverifiable
    statuses = set(df_results["Status"].unique())
    is_simple = statuses.issubset({"Deliverable", "Not deliverable", "Unverifiable"})
    deliverable_col = "Deliverable" if is_simple else "Valid"
    deliverable_only = df_results[df_results["Status"] == deliverable_col]["Email"]
    dead_only = df_results[df_results["Status"] == "Not deliverable"]["Email"] if is_simple else pd.Series(dtype=object)
    unverifiable_only = df_results[df_results["Status"] == "Unverifiable"]["Email"] if is_simple else pd.Series(dtype=object)

    col1, col2 = st.columns(2)
    with col1:
        if not deliverable_only.empty:
            csv_bytes = deliverable_only.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Download deliverable emails (CSV)" if is_simple else "Download valid emails (CSV)",
                data=csv_bytes,
                file_name="deliverable_emails.csv" if is_simple else "valid_emails.csv",
                mime="text/csv",
                key="dl_deliverable",
            )
        else:
            st.info("No deliverable emails to download.")
    with col2:
        if is_simple and not dead_only.empty:
            dead_csv = dead_only.to_csv(index=False).encode("utf-8")
            st.download_button(
                "Download dead addresses (CSV)",
                data=dead_csv,
                file_name="dead_addresses.csv",
                mime="text/csv",
                key="dl_dead",
            )
        elif is_simple and dead_only.empty:
            st.caption("No dead addresses in this run.")

    # Copy for Excel: one email per line → paste in Excel = one column, one per row
    non_valid_emails = dead_only if is_simple else df_results[~df_results["Status"].isin(["Valid"])]["Email"]
    with st.expander("Copy emails for Excel (single column, one per row)"):
        copy_col1, copy_col2 = st.columns(2)
        with copy_col1:
            st.subheader("Valid (deliverable)")
            if not deliverable_only.empty:
                deliverable_text = "\n".join(deliverable_only.astype(str).tolist())
                st.code(deliverable_text, language=None)
                st.caption("Select all above → Ctrl+C (or Cmd+C), then paste in Excel. Each line = one row in column A.")
            else:
                st.caption("No deliverable emails.")
        with copy_col2:
            st.subheader("Non-valid (dead / invalid)")
            if not non_valid_emails.empty:
                non_valid_text = "\n".join(non_valid_emails.astype(str).tolist())
                st.code(non_valid_text, language=None)
                st.caption("Select all above → Ctrl+C (or Cmd+C), then paste in Excel. Each line = one row in column A.")
            else:
                st.caption("No non-valid emails in this run.")

    if is_simple and (not unverifiable_only.empty or "Unverifiable" in statuses):
        st.caption("**Unverifiable** = we couldn’t confirm (e.g. Yahoo/Gmail block checks). If you know the address works, keep it in your list.")

if st.session_state.verification_results is None:
    st.info("Click **Start verification**. Result: **Deliverable**, **Dead**, or **Unverifiable** (couldn’t confirm — keep if you know it works).")
