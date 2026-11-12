import os
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "sentinel.db"

class Settings(BaseSettings):
    # Server configuration
    host: str = "0.0.0.0"
    port: int = 8080
    debug: bool = False
    
    # Monitoring Engine
    poll_interval_seconds: int = 30
    diff_window_minutes: int = 30
    surge_threshold_percent_30m: float = 3.0  # Alert when 30m increase >= 3%
    target_sell_price_usd: float = 0.0        # 0 = disabled
    target_sell_price_inr: float = 0.0        # 0 = disabled
    cooldown_minutes: int = 15                # Minimum minutes between identical alerts
    user_holdings_bdx: float = 10000.0        # Used to calculate sell value
    
    # Primary Market Sources
    enable_gateio: bool = True
    enable_kucoin: bool = True
    enable_mexc: bool = True
    enable_coingecko: bool = True
    
    # Nexus.vin Configuration & Selling Formula
    nexus_enabled: bool = True
    nexus_url: str = "https://nexus.vin"
    nexus_selling_premium_inr: float = 0.45   # Selling price = actual beldex price + 0.45 paise (+₹0.45 INR)
    nexus_cookie: str = ""                    # User session cookies or cf_clearance
    nexus_user_agent: str = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    )
    nexus_auth_token: str = ""
    nexus_manual_rate_usd: Optional[float] = None
    nexus_manual_rate_inr: Optional[float] = None

    # Telegram Alerting
    telegram_enabled: bool = False
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Email (SMTP) Alerting
    email_enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = ""
    email_to: str = ""
    email_use_tls: bool = True

    # SMS Alerting
    sms_enabled: bool = False
    sms_provider: str = "twilio"              # twilio | fast2sms | webhook
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_from_number: str = ""
    twilio_to_number: str = ""
    fast2sms_api_key: str = ""
    fast2sms_numbers: str = ""
    sms_webhook_url: str = ""

    # Discord / Generic Webhook Alerting
    discord_enabled: bool = False
    discord_webhook_url: str = ""
    custom_webhook_url: str = ""

    # Admin Authentication Credentials
    admin_username: str = "admin"
    admin_password: str = "Admin@Beldex2026!"
    admin_secret_key: str = "beldex-sentinel-admin-auth-secret-key-2026"
    admin_session_expiry_hours: int = 24

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()
