import asyncio
import logging
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional
import httpx
from app.config import settings
from app.database import record_alert

logger = logging.getLogger("sentrix.notification")

class NotificationService:
    def __init__(self):
        self._last_email_sent_ts: float = 0.0
        self._last_email_price_inr: float = 0.0
        self._last_email_data_hash: str = ""
        self._email_cooldown_seconds: float = 1800.0  # 30 minutes (1800 seconds)

    # -------------------------------------------------------------
    # 1. SMS NOTIFICATIONS (Twilio, Fast2SMS, Webhook)
    # -------------------------------------------------------------
    async def send_sms_twilio(self, to_number: str, text: str) -> bool:
        sid = settings.twilio_account_sid
        token = settings.twilio_auth_token
        from_num = settings.twilio_from_number
        if not (sid and token and from_num and to_number):
            logger.warning("Twilio credentials incomplete.")
            return False

        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        data = {
            "From": from_num,
            "To": to_number,
            "Body": text
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, data=data, auth=(sid, token))
                if resp.status_code in [200, 201]:
                    logger.info(f"Twilio SMS sent to {to_number}")
                    return True
                logger.error(f"Twilio error {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Failed to send Twilio SMS: {e}")
        return False

    async def send_sms_fast2sms(self, to_numbers: str, text: str) -> bool:
        api_key = settings.fast2sms_api_key
        if not (api_key and to_numbers):
            logger.warning("Fast2SMS API key or numbers missing.")
            return False

        url = "https://www.fast2sms.com/dev/bulkV2"
        headers = {
            "authorization": api_key,
            "Content-Type": "application/x-www-form-urlencoded"
        }
        data = {
            "route": "q",
            "message": text,
            "language": "english",
            "numbers": to_numbers
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, headers=headers, data=data)
                if resp.status_code == 200 and resp.json().get("return"):
                    logger.info(f"Fast2SMS sent to {to_numbers}")
                    return True
                logger.error(f"Fast2SMS error: {resp.text}")
        except Exception as e:
            logger.error(f"Failed to send Fast2SMS: {e}")
        return False

    async def send_sms_generic_webhook(self, text: str) -> bool:
        url = settings.sms_webhook_url
        if not url:
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json={"message": text, "type": "beldex_alert"})
                return resp.status_code in [200, 201, 202, 204]
        except Exception as e:
            logger.error(f"Generic SMS webhook failed: {e}")
            return False

    async def send_sms(self, text: str) -> bool:
        if not settings.sms_enabled:
            return False
        
        provider = settings.sms_provider.lower()
        if provider == "twilio":
            return await self.send_sms_twilio(settings.twilio_to_number, text)
        elif provider == "fast2sms":
            return await self.send_sms_fast2sms(settings.fast2sms_numbers, text)
        elif provider == "webhook":
            return await self.send_sms_generic_webhook(text)
        return False

    # -------------------------------------------------------------
    # 2. EMAIL (SMTP) NOTIFICATIONS
    # -------------------------------------------------------------
    def _send_smtp_sync(self, subject: str, text_body: str, html_body: str) -> bool:
        host = settings.smtp_host
        port = settings.smtp_port
        user = settings.smtp_user
        password = (settings.smtp_password or "").replace(" ", "").strip()
        sender = settings.email_from or user
        recipient = settings.email_to

        if not (host and user and password and recipient):
            logger.warning("SMTP configuration incomplete.")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Sentrix <{sender}>"
        msg["To"] = recipient

        msg.attach(MIMEText(text_body, "plain"))
        if html_body:
            msg.attach(MIMEText(html_body, "html"))

        try:
            server = smtplib.SMTP(host, port, timeout=12)
            if settings.email_use_tls:
                server.starttls()
            server.login(user, password)
            server.sendmail(sender, [recipient], msg.as_string())
            server.quit()
            logger.info(f"Email sent successfully to {recipient}")
            return True
        except Exception as e:
            logger.error(f"SMTP send failed: {e}")
            return False

    async def send_email(
        self,
        subject: str,
        text_body: str,
        html_body: str = "",
        force: bool = False
    ) -> bool:
        if not settings.email_enabled:
            return False

        now = time.time()
        elapsed = now - self._last_email_sent_ts

        # 20-second debounce: prevent duplicate emails from rapid double-clicks or dual triggers
        import hashlib
        data_hash = hashlib.md5(f"{subject}_{text_body[:60]}".encode("utf-8")).hexdigest()
        if elapsed < 20.0 and data_hash == self._last_email_data_hash:
            logger.info("Duplicate email suppressed by 20-second debounce filter.")
            return False

        # Run blocking SMTP in thread pool to preserve async loop
        ok = await asyncio.to_thread(self._send_smtp_sync, subject, text_body, html_body)
        if ok:
            self._last_email_sent_ts = now
            self._last_email_data_hash = data_hash
        return ok

    # -------------------------------------------------------------
    # 3. TELEGRAM BOT NOTIFICATIONS
    # -------------------------------------------------------------
    async def send_telegram(self, markdown_text: str) -> bool:
        if not settings.telegram_enabled:
            return False
        token = settings.telegram_bot_token
        chat_id = settings.telegram_chat_id
        if not (token and chat_id):
            logger.warning("Telegram token or chat ID missing.")
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": markdown_text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    logger.info("Telegram notification sent successfully.")
                    return True
                logger.error(f"Telegram API error {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
        return False

    # -------------------------------------------------------------
    # 4. DISCORD / GENERIC WEBHOOK NOTIFICATIONS
    # -------------------------------------------------------------
    async def send_discord(self, title: str, description: str, fields: List[Dict[str, Any]]) -> bool:
        if not settings.discord_enabled or not settings.discord_webhook_url:
            return False

        embed = {
            "title": title,
            "description": description,
            "color": 0x10B981,  # Emerald green
            "fields": fields,
            "footer": {"text": "Sentrix • Security • Precision • Intelligence • 10m/30m Rate Monitor"},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(settings.discord_webhook_url, json={"embeds": [embed]})
                return resp.status_code in [200, 204]
        except Exception as e:
            logger.error(f"Discord webhook failed: {e}")
            return False

    # -------------------------------------------------------------
    # MASTER ALERT DISPATCHER
    # -------------------------------------------------------------
    async def dispatch_rate_alert(self, analysis_data: Dict[str, Any]) -> Dict[str, Any]:
        cur_usd = analysis_data.get("current_price_usd") or 0.0
        cur_inr = analysis_data.get("current_price_inr") or 0.0
        p_30m_usd = analysis_data.get("price_30m_usd") or cur_usd
        p_30m_inr = analysis_data.get("price_30m_inr") or cur_inr
        diff_pct = analysis_data.get("diff_30m_pct") or 0.0
        reasons = analysis_data.get("alert_reasons") or []
        holdings = analysis_data.get("holdings_bdx") or 10000.0
        port_usd = analysis_data.get("portfolio_value_usd") or (cur_usd * holdings)
        port_inr = analysis_data.get("portfolio_value_inr") or (cur_inr * holdings)
        profit_usd = analysis_data.get("profit_gain_30m_usd") or 0.0
        diff_inr_abs = abs(cur_inr - p_30m_inr)
        sign = "+" if cur_inr >= p_30m_inr else "-"
        profit_inr = (cur_inr - p_30m_inr) * holdings

        # Nexus Selling Formula: Selling Price = Actual Beldex Price + 0.45 paise (+₹0.45 INR)
        nexus_prem = settings.nexus_selling_premium_inr
        nexus_sell_inr = round(cur_inr + nexus_prem, 4)
        nexus_port_inr = round(nexus_sell_inr * holdings, 2)
        nexus_bonus_inr = round(nexus_prem * holdings, 2)

        # 1. SMS Message (Short, actionable, INR-focused + Nexus formula)
        sms_text = (
            f"🚨 SENTRIX: BELDEX HIGH DEMAND ALERT!\n"
            f"30m Surge: +{diff_pct:.2f}% ({sign}Rs {diff_inr_abs:.2f})\n"
            f"Market Rate: Rs {cur_inr:.2f} (${cur_usd:.4f})\n"
            f"💎 Nexus Sell Price: Rs {nexus_sell_inr:.2f} (+45p)\n"
            f"Holdings ({holdings:,.0f} BDX): Rs {nexus_port_inr:,.0f} (+Rs {nexus_bonus_inr:,.0f} bonus)\n"
            f"Sell on Nexus.vin now!"
        )

        # 2. Telegram Message (Markdown formatted, INR first + Nexus breakdown)
        tg_text = (
            f"🚨 *SENTRIX: BELDEX HIGH DEMAND ALERT!*\n"
            f"_Security • Precision • Intelligence_\n\n"
            f"📈 *30-Min Surge*: `+{diff_pct:.2f}%` (`{sign}₹{diff_inr_abs:.2f}`)\n"
            f"💵 *Actual Market Rate*: `₹{cur_inr:.2f}` (`${cur_usd:.4f}` USD)\n"
            f"💎 *Nexus Selling Price*: `₹{nexus_sell_inr:.2f}` _(Actual + 45 paise)_\n"
            f"⏳ *Rate 30m Ago*: `₹{p_30m_inr:.2f}`\n"
            f"💼 *Holdings Value ({holdings:,.0f} BDX)*: `₹{port_inr:,.0f}`\n"
            f"💰 *Nexus Payout Total*: `₹{nexus_port_inr:,.0f}` (`+₹{nexus_bonus_inr:,.0f}` extra gain)\n"
            f"🔥 *30m Profit Gain*: `+₹{profit_inr:,.0f}`\n\n"
            f"💡 *Action*: HIGH PRICE DEMAND - Optimal opportunity to sell coins on Nexus.vin at ₹{nexus_sell_inr:.2f}!\n"
            f"⏰ *Time*: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"
        )

        # 3. Email Subject & HTML Body (INR-focused + Nexus Selling Price)
        # Dedicated Templates: Target Peak Alert (>= ₹7.50 INR) vs. Standard Surge Alert (< ₹7.50 INR)
        time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
        is_above_7_50 = cur_inr >= 7.50

        if is_above_7_50:
            # -------------------------------------------------------------
            # PEAK TARGET TEMPLATE (Price >= 7.50 INR)
            # -------------------------------------------------------------
            target_diff = cur_inr - 7.50
            email_subj = f"🎯 SENTRIX TARGET HIT: Beldex ₹{cur_inr:.2f} (Above ₹7.50) | Nexus: ₹{nexus_sell_inr:.2f} (+45p)"
            email_html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 14px; background-color: #050811; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #f1f5f9;">
  <table align="center" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 560px; background-color: #0b1329; border: 1px solid #d97706; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 30px rgba(217, 119, 6, 0.25);">
    <tr>
      <td style="background: linear-gradient(135deg, #b45309 0%, #d97706 50%, #059669 100%); padding: 18px 20px; text-align: center;">
        <span style="display: inline-block; background: rgba(0, 0, 0, 0.35); border: 1px solid rgba(255, 255, 255, 0.3); border-radius: 20px; padding: 3px 12px; font-size: 11px; font-weight: 800; color: #fef3c7; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 6px;">
          🎯 TARGET PROFIT CRITERIA SATISFIED
        </span>
        <h1 style="margin: 4px 0 2px 0; font-size: 20px; font-weight: 900; color: #ffffff; letter-spacing: 0.5px;">
          BELDEX EXCEEDS ₹7.50 INR TARGET
        </h1>
        <p style="margin: 0; font-size: 12px; font-weight: 600; color: #fde68a;">
          CRITICAL PEAK LIQUIDATION ZONE • LOCK IN MAXIMUM RETURNS
        </p>
      </td>
    </tr>
    <tr>
      <td style="padding: 18px 20px;">
        <table width="100%" border="0" cellpadding="0" cellspacing="6" style="margin-bottom: 14px;">
          <tr>
            <td width="50%" style="background-color: #111c38; border: 1px solid #f59e0b; border-radius: 8px; padding: 12px; vertical-align: top;">
              <div style="font-size: 10px; font-weight: 700; color: #fcd34d; text-transform: uppercase;">Peak Market Rate</div>
              <div style="font-size: 24px; font-weight: 900; color: #ffffff; margin-top: 2px;">₹{cur_inr:.2f}</div>
              <div style="font-size: 11px; color: #fbbf24; font-weight: 600;">+₹{target_diff:.2f} above target (${cur_usd:.4f})</div>
            </td>
            <td width="50%" style="background-color: #111c38; border: 1px solid #10b981; border-radius: 8px; padding: 12px; vertical-align: top;">
              <div style="font-size: 10px; font-weight: 700; color: #6ee7b7; text-transform: uppercase;">💎 Nexus Selling Price</div>
              <div style="font-size: 24px; font-weight: 900; color: #34d399; margin-top: 2px;">₹{nexus_sell_inr:.2f}</div>
              <div style="font-size: 11px; color: #a7f3d0; font-weight: 600;">Actual + 45 paise bonus</div>
            </td>
          </tr>
          <tr>
            <td width="50%" style="background-color: #111c38; border: 1px solid #1e293b; border-radius: 8px; padding: 12px; vertical-align: top;">
              <div style="font-size: 10px; font-weight: 700; color: #94a3b8; text-transform: uppercase;">Holdings Payout ({holdings:,.0f} BDX)</div>
              <div style="font-size: 18px; font-weight: 800; color: #38bdf8; margin-top: 2px;">₹{nexus_port_inr:,.0f}</div>
              <div style="font-size: 11px; color: #64748b;">Spot: ₹{port_inr:,.0f}</div>
            </td>
            <td width="50%" style="background-color: #111c38; border: 1px solid #1e293b; border-radius: 8px; padding: 12px; vertical-align: top;">
              <div style="font-size: 10px; font-weight: 700; color: #94a3b8; text-transform: uppercase;">Arbitrage Bonus</div>
              <div style="font-size: 18px; font-weight: 800; color: #a855f7; margin-top: 2px;">+₹{nexus_bonus_inr:,.0f}</div>
              <div style="font-size: 11px; color: #c084fc;">30m Surge: {diff_pct:+.2f}%</div>
            </td>
          </tr>
        </table>

        <table width="100%" border="0" cellpadding="0" cellspacing="0" style="background-color: rgba(217, 119, 6, 0.12); border-left: 3px solid #f59e0b; border-radius: 6px; margin-bottom: 16px;">
          <tr>
            <td style="padding: 12px 14px;">
              <div style="font-size: 11px; font-weight: 800; color: #fbbf24; text-transform: uppercase; margin-bottom: 4px;">
                💡 Peak Liquidation Strategy:
              </div>
              <div style="font-size: 12px; line-height: 1.5; color: #e2e8f0;">
                Beldex has penetrated your critical exit target of <strong>₹7.50 INR</strong>. Selling on <strong>Nexus.vin</strong> locks in <strong>₹{nexus_sell_inr:.2f} per coin</strong> (+45p premium over exchange spot), delivering <strong>₹{nexus_port_inr:,.0f} total payout</strong> (+₹{nexus_bonus_inr:,.0f} extra bonus) across your <strong>{holdings:,.0f} BDX</strong>.
              </div>
            </td>
          </tr>
        </table>

        <table width="100%" border="0" cellpadding="0" cellspacing="0">
          <tr>
            <td align="center">
              <a href="https://nexus.vin" target="_blank" style="display: inline-block; background: linear-gradient(135deg, #d97706 0%, #b45309 100%); color: #ffffff; text-decoration: none; font-size: 13px; font-weight: 800; padding: 11px 26px; border-radius: 7px; box-shadow: 0 4px 14px rgba(217, 119, 6, 0.35);">
                🔥 Execute Sale on Nexus.vin (₹{nexus_sell_inr:.2f}) →
              </a>
            </td>
          </tr>
        </table>
      </td>
    </tr>
    <tr>
      <td style="background-color: #080d1a; border-top: 1px solid #1e293b; padding: 10px 14px; text-align: center; font-size: 11px; color: #64748b;">
        Sentrix • Security, Precision and Intelligence • Next automatic update in 30 mins
      </td>
    </tr>
  </table>
</body>
</html>"""
        else:
            # -------------------------------------------------------------
            # STANDARD SURGE TEMPLATE (Price < 7.50 INR)
            # -------------------------------------------------------------
            email_subj = f"📈 SENTRIX: +{diff_pct:.2f}% Surge | Beldex ₹{cur_inr:.2f} | Nexus: ₹{nexus_sell_inr:.2f}"
            email_html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin: 0; padding: 14px; background-color: #050811; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #f1f5f9;">
  <table align="center" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 560px; background-color: #0b1329; border: 1px solid #1e293b; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 25px rgba(0, 0, 0, 0.5);">
    <tr>
      <td style="background: linear-gradient(135deg, #3b82f6 0%, #1d4ed8 50%, #4f46e5 100%); padding: 18px 20px; text-align: center;">
        <span style="display: inline-block; background: rgba(0, 0, 0, 0.3); border: 1px solid rgba(255, 255, 255, 0.2); border-radius: 20px; padding: 3px 12px; font-size: 11px; font-weight: 800; color: #bfdbfe; letter-spacing: 1px; text-transform: uppercase; margin-bottom: 6px;">
          🛡️ SENTRIX RATE ENGINE
        </span>
        <h1 style="margin: 4px 0 2px 0; font-size: 20px; font-weight: 900; color: #ffffff; letter-spacing: 0.5px;">
          30M PRICE SURGE: +{diff_pct:.2f}%
        </h1>
        <p style="margin: 0; font-size: 12px; font-weight: 500; color: #93c5fd;">
          SECURITY • PRECISION • INTELLIGENCE
        </p>
      </td>
    </tr>
    <tr>
      <td style="padding: 18px 20px;">
        <table width="100%" border="0" cellpadding="0" cellspacing="6" style="margin-bottom: 14px;">
          <tr>
            <td width="50%" style="background-color: #111c38; border: 1px solid #1e293b; border-radius: 8px; padding: 12px; vertical-align: top;">
              <div style="font-size: 10px; font-weight: 700; color: #94a3b8; text-transform: uppercase;">Current BDX Spot</div>
              <div style="font-size: 24px; font-weight: 900; color: #38bdf8; margin-top: 2px;">₹{cur_inr:.2f}</div>
              <div style="font-size: 11px; color: #64748b;">${cur_usd:.4f} USD</div>
            </td>
            <td width="50%" style="background-color: #111c38; border: 1px solid #10b981; border-radius: 8px; padding: 12px; vertical-align: top;">
              <div style="font-size: 10px; font-weight: 700; color: #6ee7b7; text-transform: uppercase;">💎 Nexus Sell Price</div>
              <div style="font-size: 24px; font-weight: 900; color: #10b981; margin-top: 2px;">₹{nexus_sell_inr:.2f}</div>
              <div style="font-size: 11px; color: #a7f3d0; font-weight: 600;">+₹0.45 (+45 paise)</div>
            </td>
          </tr>
          <tr>
            <td width="50%" style="background-color: #111c38; border: 1px solid #1e293b; border-radius: 8px; padding: 12px; vertical-align: top;">
              <div style="font-size: 10px; font-weight: 700; color: #94a3b8; text-transform: uppercase;">30-Min Difference</div>
              <div style="font-size: 18px; font-weight: 800; color: #10b981; margin-top: 2px;">+{diff_pct:.2f}%</div>
              <div style="font-size: 11px; color: #64748b;">{sign}₹{diff_inr_abs:.2f} (from ₹{p_30m_inr:.2f})</div>
            </td>
            <td width="50%" style="background-color: #111c38; border: 1px solid #1e293b; border-radius: 8px; padding: 12px; vertical-align: top;">
              <div style="font-size: 10px; font-weight: 700; color: #94a3b8; text-transform: uppercase;">Nexus Payout ({holdings:,.0f} BDX)</div>
              <div style="font-size: 18px; font-weight: 800; color: #f59e0b; margin-top: 2px;">₹{nexus_port_inr:,.0f}</div>
              <div style="font-size: 11px; color: #fbbf24;">+₹{nexus_bonus_inr:,.0f} extra profit</div>
            </td>
          </tr>
        </table>

        <table width="100%" border="0" cellpadding="0" cellspacing="0" style="background-color: rgba(59, 130, 246, 0.08); border-left: 3px solid #3b82f6; border-radius: 6px; margin-bottom: 16px;">
          <tr>
            <td style="padding: 12px 14px;">
              <div style="font-size: 11px; font-weight: 800; color: #60a5fa; text-transform: uppercase; margin-bottom: 4px;">
                💡 Market Intelligence & Action:
              </div>
              <div style="font-size: 12px; line-height: 1.5; color: #cbd5e1;">
                Buying momentum detected over the rolling 30-minute window (+{diff_pct:.2f}%). Selling on <strong>Nexus.vin</strong> delivers +45 paise above spot rates (+₹{nexus_bonus_inr:,.0f} extra across your portfolio).
              </div>
            </td>
          </tr>
        </table>

        <table width="100%" border="0" cellpadding="0" cellspacing="0">
          <tr>
            <td align="center">
              <a href="https://nexus.vin" target="_blank" style="display: inline-block; background: #2563eb; color: #ffffff; text-decoration: none; font-size: 13px; font-weight: 700; padding: 10px 22px; border-radius: 7px; margin-right: 8px;">
                Sell on Nexus.vin (₹{nexus_sell_inr:.2f}) →
              </a>
              <a href="https://www.gate.io/trade/BDX_USDT" target="_blank" style="display: inline-block; background: #1e293b; color: #94a3b8; text-decoration: none; font-size: 13px; font-weight: 600; padding: 10px 18px; border-radius: 7px;">
                Gate.io Market
              </a>
            </td>
          </tr>
        </table>
      </td>
    </tr>
    <tr>
      <td style="background-color: #080d1a; border-top: 1px solid #1e293b; padding: 10px 14px; text-align: center; font-size: 11px; color: #64748b;">
        Sentrix • Security, Precision and Intelligence • Next update in 30 mins
      </td>
    </tr>
  </table>
</body>
</html>"""

        # 4. Discord Embed
        discord_fields = [
            {"name": "Actual Rate (INR)", "value": f"₹{cur_inr:.2f} (${cur_usd:.4f})", "inline": True},
            {"name": "💎 Nexus Selling Rate", "value": f"₹{nexus_sell_inr:.2f} (+45p)", "inline": True},
            {"name": "30m Change", "value": f"+{diff_pct:.2f}% ({sign}₹{diff_inr_abs:.2f})", "inline": True},
            {"name": "Nexus Payout", "value": f"₹{nexus_port_inr:,.0f} (+₹{nexus_bonus_inr:,.0f} gain)", "inline": True},
            {"name": "Regular Market Value", "value": f"₹{port_inr:,.0f}", "inline": True},
            {"name": "Trigger Reason", "value": " • ".join(reasons) if reasons else "30m Surge", "inline": False}
        ]

        # Dispatch concurrently to all enabled channels
        channels_attempted = []
        channels_succeeded = []

        now = time.time()
        if self._last_email_sent_ts == 0.0:
            from app.database import get_last_alert_time
            self._last_email_sent_ts = await get_last_alert_time()

        time_since_email = now - self._last_email_sent_ts
        data_hash = f"{round(cur_inr, 2)}:{round(diff_pct, 1)}:{is_above_7_50}"

        can_send_email = False
        if settings.email_enabled:
            if self._last_email_sent_ts == 0.0:
                can_send_email = True
            elif is_above_7_50 and self._last_email_price_inr < 7.50:
                can_send_email = True
                logger.info(f"Target ₹7.50 crossed (Current: ₹{cur_inr:.2f}) - immediate peak target email triggered!")
            elif time_since_email >= self._email_cooldown_seconds:
                if data_hash != self._last_email_data_hash:
                    can_send_email = True
                else:
                    logger.info("Email alert suppressed: identical price/surge data within cooldown window.")
            else:
                logger.info(f"Email alert suppressed by 30-minute cooldown ({time_since_email/60:.1f}/30.0 mins elapsed).")

        tasks = []
        if settings.sms_enabled:
            channels_attempted.append("sms")
            tasks.append(("sms", self.send_sms(sms_text)))
        if settings.telegram_enabled:
            channels_attempted.append("telegram")
            tasks.append(("telegram", self.send_telegram(tg_text)))
        if can_send_email:
            channels_attempted.append("email")
            tasks.append(("email", self.send_email(email_subj, sms_text, email_html)))
            self._last_email_sent_ts = now
            self._last_email_price_inr = cur_inr
            self._last_email_data_hash = data_hash
        if settings.discord_enabled:
            channels_attempted.append("discord")
            tasks.append(("discord", self.send_discord("🚨 SENTRIX HIGH DEMAND SURGE DETECTED", sms_text, discord_fields)))

        for ch_name, coro in tasks:
            try:
                ok = await coro
                if ok:
                    channels_succeeded.append(ch_name)
            except Exception as e:
                logger.error(f"Error dispatching to {ch_name}: {e}")

        status = "success" if channels_succeeded else ("partial" if channels_attempted else "none_configured")
        
        # Record in database log
        await record_alert(
            alert_type="30m_surge",
            title=f"30m Surge +{diff_pct:.2f}%",
            message=sms_text,
            current_price_usd=cur_usd,
            current_price_inr=cur_inr,
            diff_30m_pct=diff_pct,
            channels_sent=channels_succeeded,
            status=status
        )

        return {
            "channels_attempted": channels_attempted,
            "channels_succeeded": channels_succeeded,
            "status": status,
            "timestamp": time.time()
        }

    async def send_test_alert(self, channel: str) -> Dict[str, Any]:
        test_data = {
            "current_price_usd": 0.0750,
            "current_price_inr": 7.04,
            "price_30m_usd": 0.0709,
            "price_30m_inr": 6.65,
            "diff_30m_pct": 5.78,
            "alert_reasons": ["Manual Test: High Demand Simulation +5.78%"],
            "holdings_bdx": settings.user_holdings_bdx,
            "portfolio_value_usd": round(0.0750 * settings.user_holdings_bdx, 2),
            "portfolio_value_inr": round(7.04 * settings.user_holdings_bdx, 2),
            "profit_gain_30m_usd": round((0.0750 - 0.0709) * settings.user_holdings_bdx, 2)
        }

        channel = channel.lower()
        success = False
        detail = ""

        if channel == "telegram":
            text = (
                "🧪 *SENTRIX TEST ALERT (Telegram)*\n\n"
                "🛡️ *Security • Precision • Intelligence*\n\n"
                "✅ Telegram notifications are working correctly!\n"
                "You will receive instant alerts here whenever a 10m/30m Beldex price surge is detected on nexus.vin / markets."
            )
            success = await self.send_telegram(text)
            detail = "Telegram test message dispatched"
        elif channel == "email":
            subj = "🧪 SENTRIX: Google SMTP Verification Alert"
            text = "This is a single verification alert from Sentrix (Security • Precision • Intelligence). Your Google SMTP notifications are configured and functioning properly."
            test_html = f"""<!DOCTYPE html>
<html>
<body style="margin: 0; padding: 16px; background-color: #050811; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #f1f5f9;">
  <table align="center" border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 560px; background-color: #0b1329; border: 1px solid #10b981; border-radius: 12px; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.5);">
    <tr>
      <td style="background: linear-gradient(135deg, #059669 0%, #10b981 100%); padding: 18px 20px; text-align: center;">
        <h1 style="margin: 0; font-size: 18px; font-weight: 800; color: #ffffff;">✅ SENTRIX GOOGLE SMTP VERIFIED</h1>
        <p style="margin: 4px 0 0 0; font-size: 12px; color: #d1fae5;">Security • Precision • Intelligence</p>
      </td>
    </tr>
    <tr>
      <td style="padding: 20px;">
        <div style="font-size: 13px; color: #cbd5e1; line-height: 1.5; margin-bottom: 14px;">
          Your Google SMTP channel is actively linked to <strong>{settings.email_to}</strong>. You will receive <strong>at most one email every 30 minutes</strong> whenever high-demand surges occur or when Beldex exceeds <strong>₹7.50 INR</strong>.
        </div>
        <div style="background-color: #111c38; border: 1px solid #1e293b; border-radius: 8px; padding: 12px; font-size: 12px; color: #94a3b8; line-height: 1.6;">
          <strong style="color: #38bdf8;">Active Sentrix Parameters:</strong><br>
          • <strong>Interval:</strong> At most 1 email every 30 minutes (cooldown guard active)<br>
          • <strong>Target Peak Template:</strong> Triggers automatically if price reaches ≥ ₹7.50 INR<br>
          • <strong>Nexus Formula:</strong> Selling Price = Actual Market Rate + 0.45 paise (+₹0.45)
        </div>
      </td>
    </tr>
    <tr>
      <td style="background-color: #080d1a; border-top: 1px solid #1e293b; padding: 10px; text-align: center; font-size: 11px; color: #64748b;">
        Sentrix Autonomous Rate Engine • Single Email Verification
      </td>
    </tr>
  </table>
</body>
</html>"""
            success = await self.send_email(subj, text, test_html)
            detail = "Single test email dispatched"
        elif channel == "sms":
            text = "Sentrix: Test SMS alert received. High demand notifications will arrive on this number. Security • Precision • Intelligence."
            success = await self.send_sms(text)
            detail = "SMS test dispatched"
        elif channel == "discord":
            fields = [{"name": "Test Status", "value": "Passed", "inline": True}]
            success = await self.send_discord("🧪 SENTRIX Test Alert", "Discord webhook configured and verified! Security • Precision • Intelligence", fields)
            detail = "Discord test dispatched"
        else:
            return {"status": "error", "message": f"Unknown channel '{channel}'"}

        return {"status": "ok" if success else "failed", "channel": channel, "detail": detail}

notification_service = NotificationService()
