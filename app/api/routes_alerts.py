from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.config import settings
from app.database import get_recent_alerts, set_dynamic_setting
from app.services.auth import require_admin
from app.services.notification import notification_service

router = APIRouter(prefix="/api/alerts", tags=["Alerts"])

class AlertConfigPayload(BaseModel):
    # Monitoring parameters
    poll_interval_seconds: Optional[int] = None
    surge_threshold_percent_30m: Optional[float] = None
    target_sell_price_usd: Optional[float] = None
    target_sell_price_inr: Optional[float] = None
    cooldown_minutes: Optional[int] = None
    user_holdings_bdx: Optional[float] = None

    # Telegram
    telegram_enabled: Optional[bool] = None
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    # Email
    email_enabled: Optional[bool] = None
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = None
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    email_from: Optional[str] = None
    email_to: Optional[str] = None
    email_use_tls: Optional[bool] = None

    # SMS
    sms_enabled: Optional[bool] = None
    sms_provider: Optional[str] = None
    twilio_account_sid: Optional[str] = None
    twilio_auth_token: Optional[str] = None
    twilio_from_number: Optional[str] = None
    twilio_to_number: Optional[str] = None
    fast2sms_api_key: Optional[str] = None
    fast2sms_numbers: Optional[str] = None
    sms_webhook_url: Optional[str] = None

    # Discord
    discord_enabled: Optional[bool] = None
    discord_webhook_url: Optional[str] = None

class TestAlertPayload(BaseModel):
    channel: str  # 'telegram', 'email', 'sms', 'discord'

@router.get("/history")
async def get_alerts_history(limit: int = 50):
    """
    Returns history of all dispatched price alerts and test notifications.
    """
    alerts = await get_recent_alerts(limit=limit)
    return {"alerts": alerts, "count": len(alerts)}

@router.get("/config")
async def get_alert_config():
    """
    Returns the active alert thresholds and notification channel states (sanitizing sensitive tokens).
    """
    return {
        "monitoring": {
            "poll_interval_seconds": settings.poll_interval_seconds,
            "surge_threshold_percent_30m": settings.surge_threshold_percent_30m,
            "target_sell_price_usd": settings.target_sell_price_usd,
            "target_sell_price_inr": settings.target_sell_price_inr,
            "cooldown_minutes": settings.cooldown_minutes,
            "user_holdings_bdx": settings.user_holdings_bdx
        },
        "channels": {
            "telegram": {
                "enabled": settings.telegram_enabled,
                "has_token": bool(settings.telegram_bot_token),
                "chat_id": settings.telegram_chat_id
            },
            "email": {
                "enabled": settings.email_enabled,
                "smtp_host": settings.smtp_host,
                "smtp_port": settings.smtp_port,
                "smtp_user": settings.smtp_user,
                "email_to": settings.email_to,
                "has_password": bool(settings.smtp_password),
                "use_tls": settings.email_use_tls
            },
            "sms": {
                "enabled": settings.sms_enabled,
                "provider": settings.sms_provider,
                "has_twilio_sid": bool(settings.twilio_account_sid),
                "twilio_to_number": settings.twilio_to_number,
                "has_fast2sms_key": bool(settings.fast2sms_api_key),
                "fast2sms_numbers": settings.fast2sms_numbers
            },
            "discord": {
                "enabled": settings.discord_enabled,
                "has_webhook": bool(settings.discord_webhook_url)
            }
        }
    }

@router.post("/config")
async def update_alert_config(
    payload: AlertConfigPayload,
    admin: str = Depends(require_admin)
):
    """
    Dynamically update notification channels and monitoring thresholds. (ADMIN ONLY)
    """
    # Monitoring parameters
    if payload.poll_interval_seconds is not None:
        settings.poll_interval_seconds = payload.poll_interval_seconds
    if payload.surge_threshold_percent_30m is not None:
        settings.surge_threshold_percent_30m = payload.surge_threshold_percent_30m
    if payload.target_sell_price_usd is not None:
        settings.target_sell_price_usd = payload.target_sell_price_usd
    if payload.target_sell_price_inr is not None:
        settings.target_sell_price_inr = payload.target_sell_price_inr
    if payload.cooldown_minutes is not None:
        settings.cooldown_minutes = payload.cooldown_minutes
    if payload.user_holdings_bdx is not None:
        settings.user_holdings_bdx = payload.user_holdings_bdx

    # Telegram
    if payload.telegram_enabled is not None:
        settings.telegram_enabled = payload.telegram_enabled
    if payload.telegram_bot_token is not None and payload.telegram_bot_token.strip():
        settings.telegram_bot_token = payload.telegram_bot_token.strip()
    if payload.telegram_chat_id is not None:
        settings.telegram_chat_id = payload.telegram_chat_id.strip()

    # Email
    if payload.email_enabled is not None:
        settings.email_enabled = payload.email_enabled
    if payload.smtp_host is not None:
        settings.smtp_host = payload.smtp_host.strip()
    if payload.smtp_port is not None:
        settings.smtp_port = payload.smtp_port
    if payload.smtp_user is not None:
        settings.smtp_user = payload.smtp_user.strip()
    if payload.smtp_password is not None and payload.smtp_password.strip():
        settings.smtp_password = payload.smtp_password.strip()
    if payload.email_from is not None:
        settings.email_from = payload.email_from.strip()
    if payload.email_to is not None:
        settings.email_to = payload.email_to.strip()
    if payload.email_use_tls is not None:
        settings.email_use_tls = payload.email_use_tls

    # SMS
    if payload.sms_enabled is not None:
        settings.sms_enabled = payload.sms_enabled
    if payload.sms_provider is not None:
        settings.sms_provider = payload.sms_provider
    if payload.twilio_account_sid is not None:
        settings.twilio_account_sid = payload.twilio_account_sid.strip()
    if payload.twilio_auth_token is not None and payload.twilio_auth_token.strip():
        settings.twilio_auth_token = payload.twilio_auth_token.strip()
    if payload.twilio_from_number is not None:
        settings.twilio_from_number = payload.twilio_from_number.strip()
    if payload.twilio_to_number is not None:
        settings.twilio_to_number = payload.twilio_to_number.strip()
    if payload.fast2sms_api_key is not None:
        settings.fast2sms_api_key = payload.fast2sms_api_key.strip()
    if payload.fast2sms_numbers is not None:
        settings.fast2sms_numbers = payload.fast2sms_numbers.strip()
    if payload.sms_webhook_url is not None:
        settings.sms_webhook_url = payload.sms_webhook_url.strip()

    # Discord
    if payload.discord_enabled is not None:
        settings.discord_enabled = payload.discord_enabled
    if payload.discord_webhook_url is not None:
        settings.discord_webhook_url = payload.discord_webhook_url.strip()

    # Save to dynamic settings DB
    await set_dynamic_setting("alert_config", payload.model_dump(exclude_none=True))

    return {"status": "success", "message": "Alert configuration updated successfully"}

@router.post("/test")
async def trigger_channel_test(
    payload: TestAlertPayload,
    admin: str = Depends(require_admin)
):
    """
    Test a specific channel (telegram, email, sms, discord). (ADMIN ONLY)
    """
    res = await notification_service.send_test_alert(payload.channel)
    return res
