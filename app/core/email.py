"""
Email dispatch engine.

Flow:  system event  →  email_event_bindings (DB)  →  operation_key
       operation_key →  email_operation_configs    →  smtp_config_id + is_enabled
       operation_key →  email_templates (DB)       →  subject / html_body / text_body
       subject/body  →  {{variable}} substitution  →  rendered email
       rendered      →  SMTP send

Nothing is hardcoded — the event→operation mapping and all templates live in the DB.
Fallback built-in templates are used when no custom template has been saved yet.
"""

import asyncio
import logging
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import partial

import asyncpg

from app.core.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Template rendering  — {{variable}} syntax
# ---------------------------------------------------------------------------

def _render(template: str, variables: dict) -> str:
    """Replace {{key}} placeholders with values; unknown placeholders left as-is."""
    def _replace(m: re.Match) -> str:
        key = m.group(1).strip()
        return str(variables.get(key, m.group(0)))
    return re.sub(r"\{\{(\w+)\}\}", _replace, template or "")


# ---------------------------------------------------------------------------
# Built-in fallback templates (used when no custom template is saved)
# ---------------------------------------------------------------------------

def _builtin_template(operation_key: str, variables: dict, cfg: dict) -> tuple[str, str, str]:
    """Return (subject, html_body, text_body) using a generic fallback template."""
    platform = cfg.get("from_name") or settings.SMTP_FROM_NAME or "Platform"
    first_name = variables.get("first_name") or variables.get("email", "User")

    if operation_key == "portal_user_invite":
        subject = f"You've been invited to {platform}"
        html = f"""
<html><body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:0 auto;padding:20px;">
<h2 style="color:#1a56db;">Welcome to {platform}</h2>
<p>Hello {first_name},</p>
<p>You have been invited to access the <strong>{variables.get('company_name','')}</strong> customer portal.</p>
<div style="background:#f3f4f6;border-radius:6px;padding:16px;margin:16px 0;">
  <p style="margin:0;"><strong>Email:</strong> {variables.get('email','')}</p>
  <p style="margin:8px 0 0;"><strong>Temporary Password:</strong>
    <code style="background:#e5e7eb;padding:2px 6px;border-radius:3px;">{variables.get('temp_password','')}</code></p>
</div>
<p>You will be required to set a new password on first login.</p>
<a href="{variables.get('login_url','#')}" style="display:inline-block;background:#1a56db;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:bold;">Log In to Portal</a>
</body></html>"""
        text = (f"Hello {first_name},\n\nYou have been invited to {variables.get('company_name','')}.\n"
                f"Email: {variables.get('email','')}\nTemporary Password: {variables.get('temp_password','')}\n"
                f"Login: {variables.get('login_url','')}\n")

    elif operation_key == "portal_password_reset":
        subject = f"Your {platform} password has been reset"
        html = f"""
<html><body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:0 auto;padding:20px;">
<h2 style="color:#1a56db;">Password Reset</h2>
<p>Hello {first_name},</p>
<p>Your portal password has been reset. Your new temporary password:</p>
<div style="background:#f3f4f6;border-radius:6px;padding:16px;margin:16px 0;">
  <code style="font-size:1.1em;">{variables.get('temp_password','')}</code>
</div>
<a href="{variables.get('login_url','#')}" style="display:inline-block;background:#1a56db;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:bold;">Log In</a>
</body></html>"""
        text = (f"Hello {first_name},\nTemporary Password: {variables.get('temp_password','')}\n"
                f"Login: {variables.get('login_url','')}\n")

    elif operation_key in ("admin_user_invite", "admin_user_invited"):
        role_label = variables.get("role", "")
        subject = f"You've been invited to {platform} Admin"
        html = f"""
<html><body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:0 auto;padding:20px;">
<h2 style="color:#1a56db;">Welcome to {platform} Admin Toolkit</h2>
<p>Hello {first_name},</p>
<p>You have been invited to access the <strong>{platform}</strong> admin toolkit with the role <strong>{role_label}</strong>.</p>
<div style="background:#f3f4f6;border-radius:6px;padding:16px;margin:16px 0;">
  <p style="margin:0;"><strong>Email:</strong> {variables.get('email','')}</p>
  <p style="margin:8px 0 0;"><strong>Temporary Password:</strong>
    <code style="background:#e5e7eb;padding:2px 6px;border-radius:3px;">{variables.get('temp_password','')}</code></p>
</div>
<p>You will be required to set a new password on your first login.</p>
<a href="{variables.get('login_url','#')}" style="display:inline-block;background:#1a56db;color:#fff;padding:12px 24px;border-radius:6px;text-decoration:none;font-weight:bold;">Log In to Admin</a>
<p style="color:#888;font-size:12px;margin-top:24px;">This invitation expires in 7 days.</p>
</body></html>"""
        text = (f"Hello {first_name},\n\nYou have been invited to {platform} Admin Toolkit as {role_label}.\n"
                f"Email: {variables.get('email','')}\nTemporary Password: {variables.get('temp_password','')}\n"
                f"Login: {variables.get('login_url','')}\n\nYou must change your password on first login.\n")

    else:
        subject = f"Notification from {platform}"
        html = f"<p>Hello {first_name},</p><p>You have a new notification from {platform}.</p>"
        text = f"Hello {first_name},\nYou have a new notification from {platform}.\n"

    return subject, html, text


# ---------------------------------------------------------------------------
# SMTP lookup
# ---------------------------------------------------------------------------

async def _get_smtp_cfg(db: asyncpg.Connection, operation_key: str) -> dict | None:
    r = await db.fetchrow(
        """
        SELECT sc.* FROM smtp_configurations sc
        JOIN email_operation_configs eoc ON eoc.smtp_config_id = sc.id
        WHERE eoc.operation_key = $1 AND eoc.is_enabled = TRUE AND sc.is_active = TRUE
        """,
        operation_key,
    )
    if r:
        return dict(r)
    r = await db.fetchrow(
        "SELECT * FROM smtp_configurations WHERE is_default = TRUE AND is_active = TRUE LIMIT 1"
    )
    return dict(r) if r else None


def _cfg_or_env(cfg: dict | None) -> dict | None:
    if cfg:
        return cfg
    if not settings.SMTP_HOST:
        return None
    return {
        "host": settings.SMTP_HOST, "port": settings.SMTP_PORT,
        "username": settings.SMTP_USER or None, "password": settings.SMTP_PASSWORD or None,
        "from_email": settings.SMTP_FROM, "from_name": settings.SMTP_FROM_NAME, "use_tls": True,
    }


# ---------------------------------------------------------------------------
# SMTP send (sync — runs in executor)
# ---------------------------------------------------------------------------

def _send_smtp_sync(cfg: dict, to_email: str, subject: str, html_body: str, text_body: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{cfg['from_name']} <{cfg['from_email']}>" if cfg.get("from_name") else cfg["from_email"]
    msg["To"] = to_email
    msg.attach(MIMEText(text_body or subject, "plain"))
    msg.attach(MIMEText(html_body, "html"))
    with smtplib.SMTP(cfg["host"], int(cfg["port"])) as server:
        server.ehlo()
        if cfg.get("use_tls", True) and int(cfg["port"]) != 465:
            server.starttls()
            server.ehlo()
        if cfg.get("username"):
            server.login(cfg["username"], cfg.get("password") or "")
        server.sendmail(cfg["from_email"], to_email, msg.as_string())


async def send_email_with_cfg(cfg: dict, to_email: str, subject: str, html_body: str, text_body: str = "") -> None:
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, partial(_send_smtp_sync, cfg, to_email, subject, html_body, text_body))
    except Exception as exc:
        logger.error("SMTP send failed → %s via %s:%s — %s", to_email, cfg["host"], cfg["port"], exc)


# ---------------------------------------------------------------------------
# Main dispatch — called from everywhere (no hardcoded operation keys)
# ---------------------------------------------------------------------------

async def send_event_email(
    db: asyncpg.Connection,
    event_key: str,
    to_email: str,
    variables: dict,
) -> None:
    """
    Dispatch an email for a system event.

    1. Look up which operation is bound to this event (DB).
    2. Check the operation is enabled and has an SMTP config.
    3. Load the user-saved template (DB) or fall back to built-in.
    4. Render {{variable}} placeholders and send.
    """
    # Step 1: event → operation binding
    binding = await db.fetchrow(
        "SELECT * FROM email_event_bindings WHERE event_key=$1 AND is_enabled=TRUE",
        event_key,
    )
    if not binding:
        logger.info("No active binding for event '%s' — email skipped", event_key)
        return
    operation_key = binding["operation_key"]
    if not operation_key:
        logger.info("Event '%s' has no operation assigned — email skipped", event_key)
        return

    # Step 2: SMTP config
    cfg = _cfg_or_env(await _get_smtp_cfg(db, operation_key))
    if not cfg:
        logger.warning("No SMTP config for operation '%s' — email skipped", operation_key)
        return

    # Inject platform name into variables
    variables.setdefault("platform_name", cfg.get("from_name") or settings.SMTP_FROM_NAME or "Platform")

    # Step 3: Load template
    tmpl = await db.fetchrow(
        "SELECT subject, html_body, text_body FROM email_templates WHERE operation_key=$1",
        operation_key,
    )

    if tmpl and tmpl["html_body"].strip():
        subject  = _render(tmpl["subject"] or "", variables)
        html_body = _render(tmpl["html_body"], variables)
        text_body = _render(tmpl["text_body"] or "", variables)
    else:
        subject, html_body, text_body = _builtin_template(operation_key, variables, cfg)

    # Step 4: Send
    await send_email_with_cfg(cfg, to_email, subject, html_body, text_body)
