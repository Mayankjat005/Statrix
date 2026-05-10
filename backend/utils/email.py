# This file is a part of Statrix
# Coding : Priyanshu Dey [@irisXDR]

import smtplib
import asyncio
import logging
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from ..config import settings

logger = logging.getLogger(__name__)
_FAVICON_CID = "statrix-favicon"
_FAVICON_PATH = Path(__file__).resolve().parents[2] / "frontend" / "static" / "images" / "favicon.png"


_FONT = "font-family:'Source Serif 4',Georgia,'Times New Roman',serif;"

_DOWN_TEMPLATE = (
    '<!DOCTYPE html>'
    '<html><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1.0">'
    '</head>'
    '<body style="margin:0;padding:0;background:#1e2835;{font}">'
    '<div style="padding:40px 16px;">'

    '<table cellpadding="0" cellspacing="0" border="0" align="center"'
    ' style="max-width:560px;width:100%;margin:0 auto;background:#253545;'
    'border-radius:12px;border:1px solid rgba(255,255,255,0.1);border-collapse:collapse;">'

    # Header
    '<tr><td style="padding:24px 36px;border-bottom:1px solid rgba(255,255,255,0.08);text-align:center;">'
    '<img src="{logo_url}" alt="S" width="44" height="44"'
    ' style="display:inline-block;vertical-align:middle;border-radius:50%;'
    'border:2px solid rgba(255,255,255,0.15);">'
    '<a href="https://github.com/irisXDR/Statrix" target="_blank"'
    ' style="display:inline-block;vertical-align:middle;margin-left:14px;'
    'font-size:22px;font-weight:700;color:#00d4aa;letter-spacing:-0.3px;text-decoration:none;{font}">'
    'Statrix</a>'
    '</td></tr>'

    # Status bar
    '<tr><td style="padding:0;">'
    '<div style="background:#ef4444;padding:12px 36px;text-align:center;">'
    '<span style="font-size:13px;font-weight:700;color:#ffffff;text-transform:uppercase;'
    'letter-spacing:1px;{font}">&#9660; Monitor Down</span>'
    '</div>'
    '</td></tr>'

    # Body
    '<tr><td style="padding:28px 36px;">'
    '<p style="font-size:15px;color:#e2e8f0;margin:0 0 20px;line-height:1.5;{font}">'
    'Hello <strong>{owner_name}</strong>,</p>'
    '<p style="font-size:15px;color:#c5d0de;margin:0 0 24px;line-height:1.5;{font}">'
    'One of your monitors is <strong style="color:#ef4444;">not responding</strong>.</p>'

    '<table cellpadding="0" cellspacing="0" border="0" style="width:100%;'
    'background:rgba(15,23,42,0.4);border-radius:8px;border:1px solid rgba(255,255,255,0.06);">'
    '<tr><td style="padding:16px 20px;">'

    '<p style="font-size:14px;color:#c5d0de;margin:0 0 10px;line-height:1.6;{font}">'
    '<span style="color:#889097;">Monitor </span>'
    '<strong style="color:#00d4aa;">{monitor_name}</strong></p>'

    '<p style="font-size:14px;color:#c5d0de;margin:0 0 10px;line-height:1.6;{font}">'
    '<span style="color:#889097;">Type </span>'
    '<strong style="color:#e2e8f0;">{monitor_type}</strong></p>'

    '<p style="font-size:14px;color:#c5d0de;margin:0 0 10px;line-height:1.6;{font}">'
    '<span style="color:#889097;">Target </span>'
    '<strong style="color:#e2e8f0;">{target}</strong></p>'

    '<p style="font-size:14px;color:#c5d0de;margin:0;line-height:1.6;{font}">'
    '<span style="color:#889097;">Noticed at </span>'
    '<strong style="color:#e2e8f0;">{down_since}</strong></p>'

    '</td></tr></table>'

    '<div style="text-align:center;padding:24px 0 4px;">'
    '<a href="{status_url}" style="display:inline-block;padding:12px 32px;'
    'border-radius:8px;font-size:14px;font-weight:600;text-decoration:none;'
    'color:#ffffff;background:#ef4444;{font}">View Status Page</a>'
    '</div>'
    '</td></tr>'

    # Footer
    '<tr><td style="text-align:center;padding:20px 36px;border-top:1px solid rgba(255,255,255,0.06);">'
    '<p style="margin:0;font-size:12px;color:#889097;{font}">'
    'Powered by <a href="https://github.com/irisXDR/Statrix" target="_blank"'
    ' style="color:#00d4aa;text-decoration:none;font-weight:600;">Statrix</a></p>'
    '</td></tr>'

    '</table></div></body></html>'
).replace("{font}", _FONT)


_UP_TEMPLATE = (
    '<!DOCTYPE html>'
    '<html><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1.0">'
    '</head>'
    '<body style="margin:0;padding:0;background:#1e2835;{font}">'
    '<div style="padding:40px 16px;">'

    '<table cellpadding="0" cellspacing="0" border="0" align="center"'
    ' style="max-width:560px;width:100%;margin:0 auto;background:#253545;'
    'border-radius:12px;border:1px solid rgba(255,255,255,0.1);border-collapse:collapse;">'

    # Header
    '<tr><td style="padding:24px 36px;border-bottom:1px solid rgba(255,255,255,0.08);text-align:center;">'
    '<img src="{logo_url}" alt="S" width="44" height="44"'
    ' style="display:inline-block;vertical-align:middle;border-radius:50%;'
    'border:2px solid rgba(255,255,255,0.15);">'
    '<a href="https://github.com/irisXDR/Statrix" target="_blank"'
    ' style="display:inline-block;vertical-align:middle;margin-left:14px;'
    'font-size:22px;font-weight:700;color:#00d4aa;letter-spacing:-0.3px;text-decoration:none;{font}">'
    'Statrix</a>'
    '</td></tr>'

    # Status bar
    '<tr><td style="padding:0;">'
    '<div style="background:#10b981;padding:12px 36px;text-align:center;">'
    '<span style="font-size:13px;font-weight:700;color:#ffffff;text-transform:uppercase;'
    'letter-spacing:1px;{font}">&#9650; Monitor Recovered</span>'
    '</div>'
    '</td></tr>'

    # Body
    '<tr><td style="padding:28px 36px;">'
    '<p style="font-size:15px;color:#e2e8f0;margin:0 0 20px;line-height:1.5;{font}">'
    'Hello <strong>{owner_name}</strong>,</p>'
    '<p style="font-size:15px;color:#c5d0de;margin:0 0 24px;line-height:1.5;{font}">'
    'One of your monitors is <strong style="color:#10b981;">back online</strong>.</p>'

    '<table cellpadding="0" cellspacing="0" border="0" style="width:100%;'
    'background:rgba(15,23,42,0.4);border-radius:8px;border:1px solid rgba(255,255,255,0.06);">'
    '<tr><td style="padding:16px 20px;">'

    '<p style="font-size:14px;color:#c5d0de;margin:0 0 10px;line-height:1.6;{font}">'
    '<span style="color:#889097;">Monitor </span>'
    '<strong style="color:#00d4aa;">{monitor_name}</strong></p>'

    '<p style="font-size:14px;color:#c5d0de;margin:0 0 10px;line-height:1.6;{font}">'
    '<span style="color:#889097;">Type </span>'
    '<strong style="color:#e2e8f0;">{monitor_type}</strong></p>'

    '<p style="font-size:14px;color:#c5d0de;margin:0 0 10px;line-height:1.6;{font}">'
    '<span style="color:#889097;">Target </span>'
    '<strong style="color:#e2e8f0;">{target}</strong></p>'

    '<p style="font-size:14px;color:#c5d0de;margin:0 0 10px;line-height:1.6;{font}">'
    '<span style="color:#889097;">Downtime </span>'
    '<strong style="color:#e2e8f0;">{downtime}</strong></p>'

    '<p style="font-size:14px;color:#c5d0de;margin:0;line-height:1.6;{font}">'
    '<span style="color:#889097;">Recovered at </span>'
    '<strong style="color:#e2e8f0;">{recovered_at}</strong></p>'

    '</td></tr></table>'

    '<div style="text-align:center;padding:24px 0 4px;">'
    '<a href="{status_url}" style="display:inline-block;padding:12px 32px;'
    'border-radius:8px;font-size:14px;font-weight:600;text-decoration:none;'
    'color:#ffffff;background:#00d4aa;{font}">View Status Page</a>'
    '</div>'
    '</td></tr>'

    # Footer
    '<tr><td style="text-align:center;padding:20px 36px;border-top:1px solid rgba(255,255,255,0.06);">'
    '<p style="margin:0;font-size:12px;color:#889097;{font}">'
    'Powered by <a href="https://github.com/irisXDR/Statrix" target="_blank"'
    ' style="color:#00d4aa;text-decoration:none;font-weight:600;">Statrix</a></p>'
    '</td></tr>'

    '</table></div></body></html>'
).replace("{font}", _FONT)


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%b %d, %Y %H:%M (UTC)")


def _fmt_duration(start: datetime, end: datetime) -> str:
    delta = end - start
    total = int(delta.total_seconds())
    if total < 60:
        return f"{total}s"
    minutes = total // 60
    if minutes < 60:
        return f"{minutes} min"
    hours = minutes // 60
    remaining = minutes % 60
    if hours < 24:
        return f"{hours}h {remaining}m" if remaining else f"{hours}h"
    days = hours // 24
    remaining_h = hours % 24
    return f"{days}d {remaining_h}h" if remaining_h else f"{days}d"


def _absolute_public_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith(("http://", "https://", "cid:", "data:")):
        return raw
    if raw.startswith("//"):
        return f"https:{raw}"
    base = settings.APP_URL.rstrip("/")
    if raw.startswith("/"):
        return f"{base}{raw}"
    return f"{base}/{raw}"


def _load_favicon_bytes() -> bytes | None:
    try:
        return _FAVICON_PATH.read_bytes()
    except Exception:
        return None


def _logo_source() -> tuple[str, bytes | None]:
    favicon_bytes = _load_favicon_bytes()
    if favicon_bytes:
        return (f"cid:{_FAVICON_CID}", favicon_bytes)
    return (_absolute_public_url("/static/images/favicon.png"), None)


_TYPE_LABELS = {
    "uptime": "Website",
    "website": "Website",
    "heartbeat": "Heartbeat",
    "heartbeat-cronjob": "Heartbeat (Cronjob)",
    "heartbeat-server-agent": "Server Agent",
    "server": "Server Agent",
}


def _type_label(monitor_type: str) -> str:
    return _TYPE_LABELS.get(monitor_type, monitor_type.replace("_", " ").title())


def _send_smtp(subject: str, html: str, inline_logo_bytes: bytes | None = None) -> None:
    msg = EmailMessage()
    msg["From"] = settings.SMTP_FROM or settings.SMTP_USER
    msg["To"] = settings.NOTIFICATION_EMAIL
    msg["Subject"] = subject
    msg.set_content(html, subtype="html")
    if inline_logo_bytes:
        msg.add_related(
            inline_logo_bytes,
            maintype="image",
            subtype="png",
            cid=f"<{_FAVICON_CID}>",
            filename="favicon.png",
            disposition="inline",
        )

    with smtplib.SMTP(settings.SMTP_SERVER, settings.SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(settings.SMTP_USER, settings.SMTP_PASS)
        server.send_message(msg)


async def _send(subject: str, html: str, inline_logo_bytes: bytes | None = None) -> bool:
    if not settings.SMTP_USER or not settings.NOTIFICATION_EMAIL:
        return False
    try:
        await asyncio.wait_for(
            asyncio.to_thread(_send_smtp, subject, html, inline_logo_bytes),
            timeout=30.0,
        )
        logger.info("Alert email sent: %s", subject)
        return True
    except asyncio.TimeoutError:
        logger.error("SMTP send timed out after 30s: %s", subject)
        return False
    except Exception:
        logger.exception("Failed to send alert email: %s", subject)
        return False


async def send_down_alert(
    monitor_name: str,
    monitor_type: str,
    target: str,
    down_since: datetime,
) -> bool:
    logo_url, inline_logo_bytes = _logo_source()
    html = _DOWN_TEMPLATE.format(
        logo_url=logo_url,
        owner_name=settings.OWNER_NAME,
        monitor_name=monitor_name,
        monitor_type=_type_label(monitor_type),
        target=target or "-",
        down_since=_fmt_time(down_since),
        status_url=settings.APP_URL,
    )
    return await _send(f"[DOWN] {monitor_name} is not responding", html, inline_logo_bytes)


async def send_up_alert(
    monitor_name: str,
    monitor_type: str,
    target: str,
    down_since: datetime,
    recovered_at: datetime,
) -> bool:
    logo_url, inline_logo_bytes = _logo_source()
    html = _UP_TEMPLATE.format(
        logo_url=logo_url,
        owner_name=settings.OWNER_NAME,
        monitor_name=monitor_name,
        monitor_type=_type_label(monitor_type),
        target=target or "-",
        recovered_at=_fmt_time(recovered_at),
        downtime=_fmt_duration(down_since, recovered_at),
        status_url=settings.APP_URL,
    )
    return await _send(f"[UP] {monitor_name} has recovered", html, inline_logo_bytes)
