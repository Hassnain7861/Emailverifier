"""
Email verification: syntax, MX records, SMTP handshake.
Uses smtplib, dnspython, no database. Status: Valid, Invalid, Risky, Unknown.
"""
import re
import random
import smtplib
import socket
import queue
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Tuple, Optional

import dns.resolver

# RFC 5321/5322 limits
MAX_EMAIL_LENGTH = 254
MAX_LOCAL_LENGTH = 64
MAX_DOMAIN_LENGTH = 255
LOCAL_ATEXT = re.compile(r"^[a-zA-Z0-9!#$%&'*+/=?^_`{|}~.-]+$")
DOMAIN_LABEL = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?$")
SMTP_TIMEOUT = 10


def validate_syntax(email: str) -> Tuple[bool, str]:
    """Check email syntax. Returns (valid, reason)."""
    s = (email or "").strip()
    if not s:
        return False, "Empty"
    if len(s) > MAX_EMAIL_LENGTH:
        return False, "Too long"
    at = s.rfind("@")
    if at <= 0:
        return False, "Missing or invalid @"
    if at == len(s) - 1:
        return False, "Missing domain"
    local, domain = s[:at], s[at + 1:]
    if len(local) > MAX_LOCAL_LENGTH:
        return False, "Local part too long"
    if len(domain) > MAX_DOMAIN_LENGTH:
        return False, "Domain too long"
    if ".." in local:
        return False, "Consecutive dots"
    if not LOCAL_ATEXT.match(local):
        return False, "Invalid local part"
    labels = domain.split(".")
    for label in labels:
        if len(label) > 63:
            return False, "Label too long"
        if not DOMAIN_LABEL.match(label):
            return False, "Invalid domain label"
    return True, ""


def get_mx_hosts(domain: str) -> Tuple[bool, list, str]:
    """Resolve MX for domain. Returns (success, list of (priority, hostname), reason)."""
    try:
        answers = dns.resolver.resolve(domain, "MX")
        mx_list = [(r.preference, str(r.exchange).rstrip(".")) for r in answers]
        mx_list.sort(key=lambda x: x[0])
        if not mx_list:
            return False, [], "No MX records"
        return True, mx_list, ""
    except dns.resolver.NXDOMAIN:
        return False, [], "Domain does not exist"
    except dns.resolver.NoAnswer:
        return False, [], "No MX records"
    except Exception as e:
        return False, [], str(e) or "MX lookup failed"


def smtp_verify(email: str, mx_host: str) -> Tuple[bool, str]:
    """
    Perform SMTP handshake (RCPT TO). Returns (accepts, reason).
    Does not send actual mail.
    """
    try:
        with smtplib.SMTP(timeout=SMTP_TIMEOUT) as smtp:
            smtp.set_debuglevel(0)
            smtp.connect(mx_host, 25)
            smtp.helo("localhost")
            smtp.mail("verify@localhost")
            code, msg = smtp.rcpt(email)
            smtp.rset()
        # 250 = accepted
        if 200 <= code < 300:
            return True, "Accepted"
        return False, msg.decode("utf-8", errors="replace") if isinstance(msg, bytes) else str(msg)
    except smtplib.SMTPServerDisconnected as e:
        return False, "Server disconnected"
    except smtplib.SMTPRecipientsRefused as e:
        return False, str(e.recipients) if e.recipients else "Recipients refused"
    except (socket.timeout, socket.gaierror, OSError) as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)


def verify_one(email: str, delay_min: float = 1.0, delay_max: float = 2.0) -> Tuple[str, str]:
    """
    Verify a single email. Returns (status, reason).
    Status: Valid, Invalid, Risky, Unknown.
    """
    import time
    time.sleep(random.uniform(delay_min, delay_max))
    try:
        ok, reason = validate_syntax(email)
        if not ok:
            return "Invalid", reason

        domain = email.split("@")[1]
        mx_ok, mx_list, mx_reason = get_mx_hosts(domain)
        if not mx_ok:
            return "Invalid", f"MX: {mx_reason}"

        last_reason = ""
        for _pri, host in mx_list[:3]:
            accepts, smtp_reason = smtp_verify(email, host)
            last_reason = smtp_reason or last_reason
            if accepts:
                return "Valid", "OK"
            if "try again" in (smtp_reason or "").lower() or "temp" in (smtp_reason or "").lower():
                return "Risky", smtp_reason
        return "Invalid", f"SMTP: {last_reason}"
    except Exception as e:
        return "Unknown", str(e)


def verify_batch(
    emails: list[str],
    max_workers: int = 5,
    delay_min: float = 1.0,
    delay_max: float = 2.0,
    progress_callback=None,
) -> list[Tuple[str, str, str]]:
    """
    Verify emails with max_workers threads and 1–2s random delay per check.
    progress_callback(current_index, total) called when each item completes.
    Returns list of (email, status, reason).
    """
    results = [None] * len(emails)  # preserve order
    completed = 0

    def do_one(idx: int, addr: str) -> Tuple[int, str, str, str]:
        status, reason = verify_one(addr, delay_min, delay_max)
        return idx, addr, status, reason

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(do_one, i, e): i for i, e in enumerate(emails)}
        for fut in as_completed(futures):
            idx, addr, status, reason = fut.result()
            results[idx] = (addr, status, reason)
            completed += 1
            if progress_callback:
                progress_callback(completed, len(emails))
    return results


# Sentinel for queue-based batch
_DONE = object()


def verify_batch_to_queue(
    emails: list[str],
    result_queue: queue.Queue,
    max_workers: int = 5,
    delay_min: float = 1.0,
    delay_max: float = 2.0,
) -> None:
    """Same as verify_batch but puts each (email, status, reason) on result_queue, then puts _DONE."""
    def callback(completed: int, total: int) -> None:
        pass  # queue items are pushed per result in do_one

    def do_one_and_put(idx: int, addr: str) -> None:
        status, reason = verify_one(addr, delay_min, delay_max)
        result_queue.put((addr, status, reason))

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(do_one_and_put, i, e) for i, e in enumerate(emails)]
        for f in futures:
            f.result()
    result_queue.put(_DONE)
