"""
Email verification: syntax, MX records, SMTP handshake.
Stealth mode: realistic HELO/EHLO, same-domain MAIL FROM, longer delays, no scanner fingerprints.
Result: detailed (Valid/Invalid/Risky/Unknown) or simple (Deliverable / Not deliverable).
"""
import re
import random
import time
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
# Stealth: longer timeout so we don't look like a fast scanner
SMTP_STEALTH_TIMEOUT = 15

# Major providers that often block/defer SMTP verification; we cannot confirm mailbox, so do NOT mark deliverable
MAJOR_PROVIDER_DOMAINS = frozenset({
    "yahoo.com", "yahoo.co.uk", "yahoo.fr", "yahoo.de", "yahoo.es", "yahoo.it", "ymail.com", "rocketmail.com",
    "gmail.com", "googlemail.com", "google.com",
    "outlook.com", "hotmail.com", "hotmail.co.uk", "live.com", "live.co.uk", "msn.com", "outlook.fr", "outlook.de", "outlook.es", "outlook.it", "outlook.jp", "outlook.kr", "outlook.com.br", "hotmail.fr", "hotmail.de", "hotmail.es", "hotmail.it", "hotmail.nl", "hotmail.ca", "hotmail.be", "hotmail.jp", "hotmail.in", "hotmail.com.br", "hotmail.com.au", "hotmail.com.mx", "hotmail.com.ar", "hotmail.com.sg", "hotmail.gr", "hotmail.ie", "hotmail.co.nz", "hotmail.co.th", "hotmail.co.id", "hotmail.co.kr", "hotmail.co.in", "hotmail.my", "hotmail.ph", "hotmail.sg", "hotmail.tw", "hotmail.vn", "hotmail.tr", "hotmail.dk", "hotmail.se", "hotmail.no", "hotmail.at", "hotmail.cl", "hotmail.pt", "hotmail.sa", "hotmail.cz", "hotmail.ro", "hotmail.rs", "hotmail.sk", "hotmail.hr", "hotmail.bg", "hotmail.ae", "hotmail.fi", "hotmail.ru", "hotmail.ee", "hotmail.lv", "hotmail.lt", "hotmail.pl", "hotmail.hu", "hotmail.ua", "hotmail.by", "hotmail.kz", "hotmail.az", "hotmail.ge", "hotmail.am", "hotmail.kg", "hotmail.tj", "hotmail.uz", "hotmail.tm", "hotmail.mn", "hotmail.cat", "outlook.at", "outlook.be", "outlook.cl", "outlook.co.nz", "outlook.co.th", "outlook.co.id", "outlook.co.kr", "outlook.co.in", "outlook.my", "outlook.ph", "outlook.sg", "outlook.tw", "outlook.vn", "outlook.dk", "outlook.se", "outlook.no", "outlook.pt", "outlook.sa", "outlook.cz", "outlook.ro", "outlook.sk", "outlook.hr", "outlook.bg", "outlook.ae", "outlook.fi", "outlook.ru", "outlook.ee", "outlook.lv", "outlook.lt", "outlook.pl", "outlook.hu", "outlook.ua", "outlook.com.au", "outlook.com.mx", "outlook.com.br", "outlook.com.ar", "outlook.com.sg", "outlook.com.my", "outlook.com.ph", "outlook.com.tw", "outlook.com.vn", "outlook.com.tr", "outlook.ie", "outlook.gr", "outlook.co", "outlook.in", "outlook.nl", "outlook.ca", "outlook.be", "outlook.at", "outlook.cl", "outlook.pt", "outlook.sa", "outlook.sk", "outlook.hr", "outlook.bg", "outlook.ae", "outlook.fi", "outlook.ru", "outlook.ee", "outlook.lv", "outlook.lt", "outlook.pl", "outlook.hu", "outlook.ua",
    "aol.com", "aim.com",
    "icloud.com", "me.com", "mac.com",
    "protonmail.com", "proton.me", "pm.me",
    "zoho.com", "zohomail.com",
    "mail.com", "email.com", "usa.com", "europe.com", "asia.com", "consultant.com", "engineer.com", "post.com", "inbox.com", "writeme.com", "myself.com", "dr.com", "lawyer.com", "accountant.com", "cheerful.com", "contractor.net", "techie.com", "musician.org", "artlover.com", "linuxmail.org", "linuxmail.info", "linuxmail.biz", "linuxmail.org", "post.com", "writeme.com",
})


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


def _stealth_helo_host(domain: str, mx_host: str) -> str:
    """Return a plausible HELO/EHLO hostname (avoids 'localhost' / 'verify@...' fingerprints)."""
    # Use recipient domain's mail host; fallback to MX we're connecting to
    return f"mail.{domain}" if domain else mx_host


def smtp_verify(
    email: str,
    mx_host: str,
    stealth: bool = True,
    domain: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Perform SMTP handshake (RCPT TO). Returns (accepts, reason).
    Does not send actual mail.
    Stealth: realistic HELO/EHLO, MAIL FROM same domain, no obvious verify@localhost.
    """
    _domain = domain or (email.split("@")[1] if "@" in email else "")
    helo_host = _stealth_helo_host(_domain, mx_host) if stealth else "localhost"
    mail_from = f"noreply@{_domain}" if (stealth and _domain) else "verify@localhost"
    timeout = SMTP_STEALTH_TIMEOUT if stealth else SMTP_TIMEOUT
    try:
        with smtplib.SMTP(timeout=timeout) as smtp:
            smtp.set_debuglevel(0)
            smtp.connect(mx_host, 25)
            if stealth:
                smtp.ehlo(helo_host)
            else:
                smtp.helo(helo_host)
            smtp.mail(mail_from)
            code, msg = smtp.rcpt(email)
            if 200 <= code < 300:
                # Catch-all check: if a fake address is also accepted, domain accepts everything
                _domain = _domain or (email.split("@")[1] if "@" in email else "")
                if _domain:
                    fake_local = f"noexist-verify-{random.randint(10000, 99999)}"
                    fake_addr = f"{fake_local}@{_domain}"
                    try:
                        code2, msg2 = smtp.rcpt(fake_addr)
                        if 200 <= code2 < 300:
                            smtp.rset()
                            return False, "Accept-all domain (cannot confirm mailbox)"
                    except Exception:
                        pass
                smtp.rset()
                if stealth:
                    time.sleep(random.uniform(0.2, 0.6))
                return True, "Accepted"
            smtp.rset()
        return False, msg.decode("utf-8", errors="replace") if isinstance(msg, bytes) else str(msg)
    except smtplib.SMTPServerDisconnected as e:
        return False, "Server disconnected"
    except smtplib.SMTPRecipientsRefused as e:
        return False, str(e.recipients) if e.recipients else "Recipients refused"
    except (socket.timeout, socket.gaierror, OSError) as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)


def verify_one(
    email: str,
    delay_min: float = 1.0,
    delay_max: float = 2.0,
    stealth: bool = True,
    simple_result: bool = True,
) -> Tuple[str, str]:
    """
    Verify a single email. Returns (status, reason).
    simple_result=False: Status is Valid, Invalid, Risky, Unknown.
    simple_result=True: Status is Deliverable or Not deliverable.
    """
    time.sleep(random.uniform(delay_min, delay_max))
    mx_was_ok = False
    try:
        ok, reason = validate_syntax(email)
        if not ok:
            if simple_result:
                return "Not deliverable", reason
            return "Invalid", reason

        domain = email.split("@")[1].strip().lower()
        mx_ok, mx_list, mx_reason = get_mx_hosts(domain)
        if not mx_ok:
            if simple_result:
                return "Not deliverable", f"MX: {mx_reason}"
            return "Invalid", f"MX: {mx_reason}"

        mx_was_ok = True
        last_reason = ""
        had_clear_smtp_response = False  # True if we got a refusal from server (not just timeout/error)
        for _pri, host in mx_list[:3]:
            try:
                accepts, smtp_reason = smtp_verify(email, host, stealth=stealth, domain=domain)
                had_clear_smtp_response = True
            except Exception as e:
                last_reason = str(e)
                continue
            last_reason = smtp_reason or last_reason
            if accepts:
                return ("Deliverable", "OK") if simple_result else ("Valid", "OK")
            if not simple_result and (
                "try again" in (smtp_reason or "").lower() or "temp" in (smtp_reason or "").lower()
            ):
                return "Risky", smtp_reason
        # Major providers block SMTP verification → Unverifiable (don't mark Dead)
        if domain in MAJOR_PROVIDER_DOMAINS:
            if simple_result:
                return "Unverifiable", "Provider blocks verification (assume working if you know it)"
            return "Unknown", "Provider blocks verification"
        # Custom domain: only timeouts/errors, no clear refusal → Unverifiable (don't remove working addresses)
        if simple_result and not had_clear_smtp_response and last_reason:
            return "Unverifiable", "Connection/error (assume working if you know it)"
        if simple_result:
            return "Not deliverable", last_reason or "SMTP refused"
        return "Invalid", f"SMTP: {last_reason}"
    except Exception as e:
        domain = (email or "").strip().split("@")[-1].strip().lower() if "@" in (email or "") else ""
        # MX was OK but connection/error → Unverifiable so we don't remove working addresses
        if mx_was_ok:
            if simple_result:
                return "Unverifiable", "Connection/error (assume working if you know it)"
            return "Unknown", str(e)
        if simple_result:
            return "Not deliverable", str(e)
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
        status, reason = verify_one(addr, delay_min, delay_max, stealth=True, simple_result=False)
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
    stealth: bool = True,
    simple_result: bool = True,
) -> None:
    """Same as verify_batch but puts each (email, status, reason) on result_queue, then puts _DONE."""
    def do_one_and_put(idx: int, addr: str) -> None:
        status, reason = verify_one(
            addr, delay_min, delay_max, stealth=stealth, simple_result=simple_result
        )
        result_queue.put((addr, status, reason))

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(do_one_and_put, i, e) for i, e in enumerate(emails)]
        for f in futures:
            f.result()
    result_queue.put(_DONE)
