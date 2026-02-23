#!/usr/bin/env python3
"""
send_report.py — Run the ERN momentum report and email it as HTML.

Credentials are read from environment variables (or a .env file):
    YAHOO_USER      your full Yahoo address  (e.g. rjgibson60607@yahoo.com)
    YAHOO_APP_PASS  Yahoo App Password (16-char, generated at
                    https://login.yahoo.com/account/security → App passwords)

Usage:
    python3 send_report.py             # uses default DIVIDEND_PCT below
    python3 send_report.py --dividend 2.0
"""

import argparse
import os
import smtplib
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from rich.console import Console

# ── Config ────────────────────────────────────────────────────────────────────

TO_ADDRESS   = "rjgibson60607@yahoo.com"
DIVIDEND_PCT = 1.5          # default dividend % passed to the report
SMTP_HOST    = "smtp.mail.yahoo.com"
SMTP_PORT    = 587


# ── Load .env if present ──────────────────────────────────────────────────────

def _load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader — sets os.environ for KEY=VALUE lines."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


# ── Report generation ─────────────────────────────────────────────────────────

def generate_html_report(dividend: float) -> str:
    """Run momentum.main() with a recording Console and return HTML."""
    import momentum

    recording = Console(record=True, width=120)
    original  = momentum.console
    momentum.console = recording
    try:
        momentum.main(dividend=dividend)
    finally:
        momentum.console = original

    return recording.export_html(inline_styles=True)


# ── Email ─────────────────────────────────────────────────────────────────────

def send_email(html_body: str, user: str, app_password: str, dividend: float) -> None:
    quarter   = f"Q{(datetime.today().month - 1) // 3 + 1} {datetime.today().year}"
    subject   = f"ERN Momentum Report — {quarter}  (div {dividend*100:.1f}%)"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = user
    msg["To"]      = TO_ADDRESS

    # Plain-text fallback
    plain = (
        f"ERN Momentum Report — {quarter}\n"
        "See the HTML version of this email for the full report.\n"
    )
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(user, app_password)
        smtp.sendmail(user, TO_ADDRESS, msg.as_string())

    print(f"Report emailed to {TO_ADDRESS} ({subject})")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Email the ERN momentum report")
    parser.add_argument(
        "--dividend", type=float, default=DIVIDEND_PCT, metavar="PCT",
        help=f"Dividend yield %% to subtract from CAPE SWR (default {DIVIDEND_PCT})",
    )
    args = parser.parse_args()
    dividend = args.dividend / 100.0

    _load_dotenv()
    user     = os.environ.get("YAHOO_USER", "").strip()
    password = os.environ.get("YAHOO_APP_PASS", "").strip()

    if not user or not password:
        print(
            "Error: set YAHOO_USER and YAHOO_APP_PASS in a .env file or environment.\n"
            "  echo 'YAHOO_USER=rjgibson60607@yahoo.com' >> .env\n"
            "  echo 'YAHOO_APP_PASS=your-16-char-app-password' >> .env",
            file=sys.stderr,
        )
        sys.exit(1)

    print("Generating report…")
    html = generate_html_report(dividend)
    print("Sending email…")
    send_email(html, user, password, dividend)


if __name__ == "__main__":
    main()
