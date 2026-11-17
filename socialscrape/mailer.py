"""Dashboard email through Resend (https://resend.com): invitations, password resets and security notices.

Nothing here is sent unless an owner set up email in the dashboard or SOCIALSCRAPE_RESEND_API_KEY is configured;
without it, owners copy invitation and reset links by hand. Errors never carry the API key, and logs never carry
the links: both are credentials.
"""

from __future__ import annotations

import html
import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger("socialscrape.mailer")

RESEND_URL = "https://api.resend.com/emails"
TIMEOUT = 15


class EmailError(Exception):
    """Resend didn't take the email. The message is meant for the workspace owner."""


@dataclass(frozen=True)
class EmailConfig:
    api_key: str = field(repr=False)
    # Resend `from`, e.g. "SocialScrape <noreply@example.com>"; its domain must be verified in Resend.
    sender: str
    # The dashboard's public origin, without a trailing slash.
    public_url: str
    # "dashboard" (saved encrypted by the owner) or "environment" (SOCIALSCRAPE_RESEND_API_KEY ...)
    source: str

    def link(self, path: str) -> str:
        return self.public_url + path


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    html: str
    text: str


def _context() -> ssl.SSLContext:
    try:
        import certifi  # installed with yt-dlp[default]; slim images can lack system CAs
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _post(request: urllib.request.Request) -> bytes:
    with urllib.request.urlopen(request, timeout=TIMEOUT, context=_context()) as response:
        return response.read()


def _user_agent() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return f"socialscrape/{version('socialscrape')}"
    except PackageNotFoundError:
        return "socialscrape"


def _rejection(exc: urllib.error.HTTPError) -> str:
    try:
        data = json.loads(exc.read() or b"{}")
    except (ValueError, OSError):
        data = None
    detail = data.get("message") if isinstance(data, dict) else None
    detail = " ".join(str(detail).split())[:300] if detail else ""
    log.warning("Resend answered %d%s", exc.code, f": {detail}" if detail else "")
    if exc.code == 429:
        return "Resend is rate limiting this workspace. Try again in a minute."
    if exc.code >= 500:
        return "Resend is unavailable right now. Try again in a minute."
    return f"Resend rejected the email: {detail}" if detail else f"Resend rejected the email (HTTP {exc.code})."


def send(config: EmailConfig, message: Message) -> str:
    """Send through Resend's API; the email's id. Raises EmailError."""
    request = urllib.request.Request(RESEND_URL, method="POST", data=json.dumps({
        "from": config.sender, "to": [message.to], "subject": message.subject, "html": message.html,
        "text": message.text,
    }).encode(), headers={
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": _user_agent(),  # Resend refuses requests without one (403, error 1010)
    })
    try:
        body = _post(request)
    except urllib.error.HTTPError as exc:
        raise EmailError(_rejection(exc)) from None
    except (OSError, ValueError) as exc:  # URLError, a timeout, a TLS failure
        log.warning("could not reach Resend: %s", exc)
        raise EmailError("Could not reach Resend. Check the server's connection and try again.") from None
    try:
        return str(json.loads(body or b"{}").get("id", ""))
    except (ValueError, AttributeError):
        return ""


def send_quietly(config: EmailConfig, message: Message) -> None:
    """For mail sent after the response (password resets, notices): failures are logged, not raised."""
    try:
        send(config, message)
    except EmailError as exc:
        log.warning("could not email %r: %s", message.subject, exc)


def _render(to: str, subject: str, heading: str, paragraphs: list[str], action: tuple[str, str] | None,
            note: str) -> Message:
    """A plain, light email in the dashboard's workspace palette, with a text twin. Everything is escaped."""
    e = html.escape
    body = "".join(f'<p style="margin:0 0 16px;font-size:15px;line-height:24px;color:#18181b">{e(p)}</p>'
                   for p in paragraphs)
    button = (f'<p style="margin:24px 0"><a href="{e(action[1])}" style="display:inline-block;padding:12px 20px;'
              f'border-radius:12px;background:#18181b;color:#ffffff;font-size:14px;font-weight:600;'
              f'text-decoration:none">{e(action[0])}</a></p><p style="margin:0 0 16px;font-size:12px;line-height:18px;'
              f'color:#52525b">Or paste this link into your browser:<br><span style="word-break:break-all">'
              f'{e(action[1])}</span></p>' if action else "")
    page = f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(subject)}</title></head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:Inter,-apple-system,BlinkMacSystemFont,'Segoe UI',
Helvetica,Arial,sans-serif;color:#18181b"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"
style="background:#f4f4f4"><tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;background:#ffffff;
border:1px solid rgba(0,0,0,.09);border-radius:16px"><tr><td style="padding:32px">
<p style="margin:0 0 24px;font-size:15px;font-weight:650;letter-spacing:-.02em">SocialScrape
<span style="font-weight:400;color:#52525b;font-size:12px">by Mimic</span></p>
<h1 style="margin:0 0 16px;font-size:22px;line-height:28px;font-weight:650;letter-spacing:-.02em">{e(heading)}</h1>
{body}{button}<p style="margin:24px 0 0;padding-top:16px;border-top:1px solid rgba(0,0,0,.09);font-size:12px;
line-height:18px;color:#52525b">{e(note)}</p></td></tr></table>
<p style="margin:16px 0 0;font-size:11px;color:#6b6b74">Private, invite-only workspace</p></td></tr></table>
</body></html>'''
    text = "\n\n".join([heading, *paragraphs, *([f"{action[0]}: {action[1]}"] if action else []), note,
                        "SocialScrape by Mimic"])
    return Message(to, subject, page, text + "\n")


def invitation(config: EmailConfig, to: str, token: str, inviter: str) -> Message:
    return _render(to, "You’re invited to SocialScrape", "Join your team on SocialScrape",
                   [(f"{inviter} invited you to their private SocialScrape workspace, where your team works with "
                     "YouTube, TikTok and Instagram data.")],
                   ("Accept invitation", config.link(f"/invite/{token}")),
                   f"This invitation is for {to} and expires in 7 days. If you weren’t expecting it, you can ignore "
                   "this email.")


def password_reset(config: EmailConfig, to: str, token: str, *, hours: float, requested_by: str | None = None) -> Message:
    lifetime = "1 hour" if hours == 1 else f"{hours:g} hours"
    if requested_by:
        intro = f"{requested_by}, your workspace owner, sent you a link to choose a new password for {to}."
        note = (f"The link works once and expires in {lifetime}. Your current password keeps working until you "
                "choose a new one.")
    else:
        intro = f"We received a request to reset the password for {to}."
        note = (f"The link works once and expires in {lifetime}. If you didn’t ask for this, you can ignore this "
                "email: your password won’t change.")
    return _render(to, "Reset your SocialScrape password", "Choose a new password", [intro],
                   ("Choose a new password", config.link(f"/reset/{token}")), note)


def password_changed(config: EmailConfig, to: str, *, when: float | None = None) -> Message:
    t = time.gmtime(when if when is not None else time.time())
    stamp = f"{time.strftime('%B', t)} {t.tm_mday}, {t.tm_year} at {time.strftime('%H:%M', t)} UTC"
    return _render(to, "Your SocialScrape password was changed", "Your password was changed",
                   [f"The password for {to} was changed on {stamp}. Every other signed-in session was signed out."],
                   ("Reset your password", config.link("/forgot")),
                   "If you didn’t make this change, reset your password now and tell your workspace owner.")


def test_message(config: EmailConfig, to: str) -> Message:
    return _render(to, "SocialScrape email is set up", "Email delivery works",
                   [f"SocialScrape can now email invitations and password reset links from {config.sender}."],
                   ("Open your workspace", config.link("/app")),
                   "You received this because an owner sent a test email from the Team page.")
