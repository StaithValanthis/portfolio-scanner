from __future__ import annotations
import os, smtplib, httpx
from email.mime.text import MIMEText
WEBHOOK_URL = os.getenv("WEBHOOK_URL","").strip()
ALERT_MIN_SCORE = float(os.getenv("ALERT_MIN_SCORE","1.5"))
ALERT_SEND_RISK_FLAGS = os.getenv("ALERT_SEND_RISK_FLAGS","true").lower() == "true"
SMTP_HOST = os.getenv("SMTP_HOST","").strip(); SMTP_PORT = int(os.getenv("SMTP_PORT","587")); SMTP_USER = os.getenv("SMTP_USER","").strip(); SMTP_PASS = os.getenv("SMTP_PASS","").strip()
ALERT_EMAIL_TO = os.getenv("ALERT_EMAIL_TO","").strip()
def _send_webhook(payload: dict):
    if not WEBHOOK_URL: return
    try: httpx.post(WEBHOOK_URL, json=payload, timeout=10)
    except Exception: pass
def _send_email(subject: str, body: str):
    if not (SMTP_HOST and ALERT_EMAIL_TO): return
    try:
        msg = MIMEText(body, "plain", "utf-8"); msg["Subject"]=subject; msg["From"]=SMTP_USER or "scanner@localhost"; msg["To"]=ALERT_EMAIL_TO
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as s:
            s.starttls(); 
            if SMTP_USER and SMTP_PASS: s.login(SMTP_USER, SMTP_PASS)
            s.sendmail(msg["From"], [ALERT_EMAIL_TO], msg.as_string())
    except Exception: pass
def notify_signals(signals: list[dict]):
    highs = [s for s in signals if s.get("side")=="BUY" and s.get("score",0)>=ALERT_MIN_SCORE]
    if not highs: return
    payload = {"type":"signals","high_conviction":highs[:10]}; _send_webhook(payload)
    lines = ["High-conviction BUY signals:"] + [f"- {s['ticker']} score={s['score']} reasons={'; '.join(s['reasons'][:3])}" for s in highs[:10]]
    _send_email("Scanner: High-Conviction BUYs", "\n".join(lines))
def notify_riskflags(flags: list[str]):
    if not ALERT_SEND_RISK_FLAGS or not flags: return
    _send_webhook({"type":"risk_flags","flags":flags})
    _send_email("Scanner: Portfolio Risk Flags", "\n".join(flags))
