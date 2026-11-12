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

logger = logging.getLogger("beldex_sentinel.notification")

class NotificationService:
    def __init__(self):
        pass

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
        password = settings.smtp_password
        sender = settings.email_from or user
        recipient = settings.email_to

        if not (host and user and password and recipient):
            logger.warning("SMTP configuration incomplete.")
            return False

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"Beldex Sentinel <{sender}>"
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

    async def send_email(self, subject: str, text_body: str, html_body: str = "") -> bool:
        if not settings.email_enabled:
            return False
        # Run blocking SMTP in thread pool to preserve async loop
        return await asyncio.to_thread(self._send_smtp_sync, subject, text_body, html_body)

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
            "footer": {"text": "Beldex Nexus Rate Sentinel • 30m Rate Monitor"},
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

        # 1. SMS Message (Short, actionable, INR-focused)
        sms_text = (
            f"🚨 BELDEX HIGH DEMAND ALERT!\n"
            f"30m Surge: +{diff_pct:.2f}% ({sign}Rs {diff_inr_abs:.2f})\n"
            f"Current Rate: Rs {cur_inr:.2f} (${cur_usd:.4f})\n"
            f"Rate 30m Ago: Rs {p_30m_inr:.2f}\n"
            f"Your {holdings:,.0f} BDX = Rs {port_inr:,.0f}\n"
            f"High Demand Window! Check Nexus.vin."
        )

        # 2. Telegram Message (Markdown formatted, INR first)
        tg_text = (
            f"🚨 *BELDEX (BDX) HIGH DEMAND ALERT!*\n\n"
            f"📈 *30-Min Surge*: `+{diff_pct:.2f}%` (`{sign}₹{diff_inr_abs:.2f}`)\n"
            f"💵 *Current BDX Rate*: `₹{cur_inr:.2f}` (`${cur_usd:.4f}` USD)\n"
            f"⏳ *Rate 30m Ago*: `₹{p_30m_inr:.2f}`\n"
            f"💼 *Holdings Value ({holdings:,.0f} BDX)*: `₹{port_inr:,.0f}` (`${port_usd:,.2f}`)\n"
            f"🔥 *30m Profit Gain*: `+₹{profit_inr:,.0f}`\n\n"
            f"💡 *Action*: HIGH PRICE DEMAND - Optimal opportunity to sell coins on Nexus.vin!\n"
            f"⏰ *Time*: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}"
        )

        # 3. Email Subject & HTML Body (INR-focused)
        email_subj = f"🚨 BELDEX ALERT: +{diff_pct:.2f}% Surge in 30 Mins (₹{cur_inr:.2f} / ${cur_usd:.4f})"
        email_html = f"""
        <html>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 24px; margin: 0;">
          <div style="max-width: 600px; margin: 0 auto; background: #1e293b; border-radius: 12px; border: 1px solid #334155; overflow: hidden; box-shadow: 0 10px 25px rgba(0,0,0,0.5);">
            <div style="background: linear-gradient(135deg, #10b981 0%, #059669 100%); padding: 20px; text-align: center;">
              <h1 style="margin: 0; color: #ffffff; font-size: 24px; font-weight: 800;">🚀 BELDEX HIGH DEMAND DETECTED</h1>
              <p style="margin: 4px 0 0 0; color: #ecfdf5; font-size: 14px;">30-Minute Price Spike Opportunity Alert</p>
            </div>
            <div style="padding: 24px;">
              <div style="display: flex; justify-content: space-between; background: #0f172a; padding: 16px; border-radius: 8px; margin-bottom: 20px;">
                <div>
                  <div style="font-size: 12px; color: #94a3b8; text-transform: uppercase;">Current BDX Rate (INR)</div>
                  <div style="font-size: 32px; font-weight: 800; color: #10b981;">₹{cur_inr:.2f} <span style="font-size: 16px; color: #cbd5e1;">(${cur_usd:.4f} USD)</span></div>
                </div>
                <div style="text-align: right;">
                  <div style="font-size: 12px; color: #94a3b8; text-transform: uppercase;">30-Min Difference</div>
                  <div style="font-size: 32px; font-weight: 800; color: #10b981;">+{diff_pct:.2f}%</div>
                  <div style="font-size: 12px; color: #34d399; font-weight: 600;">{sign}₹{diff_inr_abs:.2f}</div>
                </div>
              </div>
              
              <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px; font-size: 14px;">
                <tr style="border-bottom: 1px solid #334155;">
                  <td style="padding: 10px 0; color: #94a3b8;">Rate 30 Mins Ago:</td>
                  <td style="padding: 10px 0; text-align: right; font-weight: 600;">₹{p_30m_inr:.2f} (${p_30m_usd:.4f})</td>
                </tr>
                <tr style="border-bottom: 1px solid #334155;">
                  <td style="padding: 10px 0; color: #94a3b8;">Your Beldex Holdings:</td>
                  <td style="padding: 10px 0; text-align: right; font-weight: 600;">{holdings:,.0f} BDX</td>
                </tr>
                <tr style="border-bottom: 1px solid #334155;">
                  <td style="padding: 10px 0; color: #94a3b8;">Total Portfolio Sell Value:</td>
                  <td style="padding: 10px 0; text-align: right; font-weight: 800; color: #38bdf8;">₹{port_inr:,.0f} (${port_usd:,.2f})</td>
                </tr>
                <tr>
                  <td style="padding: 10px 0; color: #94a3b8;">Estimated 30m Value Gain:</td>
                  <td style="padding: 10px 0; text-align: right; font-weight: 800; color: #10b981;">+₹{profit_inr:,.0f}</td>
                </tr>
              </table>

              <div style="background: rgba(16, 185, 129, 0.1); border-left: 4px solid #10b981; padding: 12px; border-radius: 4px; margin-bottom: 24px;">
                <strong style="color: #10b981;">Recommended Strategy:</strong>
                <p style="margin: 4px 0 0 0; color: #cbd5e1; font-size: 13px;">
                  High demand detected over the last 30 minutes in INR rate. If you are looking to take profit or liquidate your coins at peak rates, review your orders on nexus.vin now.
                </p>
              </div>

              <div style="text-align: center;">
                <a href="https://nexus.vin" style="display: inline-block; background: #3b82f6; color: #ffffff; text-decoration: none; padding: 12px 24px; border-radius: 6px; font-weight: 600; margin-right: 8px;">Open Nexus.vin</a>
                <a href="https://www.gate.io/trade/BDX_USDT" style="display: inline-block; background: #334155; color: #ffffff; text-decoration: none; padding: 12px 24px; border-radius: 6px; font-weight: 600;">Gate.io Market</a>
              </div>
            </div>
            <div style="background: #0f172a; padding: 12px; text-align: center; font-size: 12px; color: #64748b;">
              Beldex Nexus Rate Sentinel • Autonomous Rate Monitoring Engine
            </div>
          </div>
        </body>
        </html>
        """

        # 4. Discord Embed
        discord_fields = [
            {"name": "Current Rate (INR)", "value": f"₹{cur_inr:.2f} (${cur_usd:.4f})", "inline": True},
            {"name": "30m Change", "value": f"+{diff_pct:.2f}% ({sign}₹{diff_inr_abs:.2f})", "inline": True},
            {"name": "30m Ago Rate", "value": f"₹{p_30m_inr:.2f}", "inline": True},
            {"name": "Portfolio Value", "value": f"₹{port_inr:,.0f} (${port_usd:,.2f})", "inline": True},
            {"name": "Trigger Reason", "value": " • ".join(reasons) if reasons else "30m Surge", "inline": False}
        ]

        # Dispatch concurrently to all enabled channels
        channels_attempted = []
        channels_succeeded = []

        tasks = []
        if settings.sms_enabled:
            channels_attempted.append("sms")
            tasks.append(("sms", self.send_sms(sms_text)))
        if settings.telegram_enabled:
            channels_attempted.append("telegram")
            tasks.append(("telegram", self.send_telegram(tg_text)))
        if settings.email_enabled:
            channels_attempted.append("email")
            tasks.append(("email", self.send_email(email_subj, sms_text, email_html)))
        if settings.discord_enabled:
            channels_attempted.append("discord")
            tasks.append(("discord", self.send_discord("🚨 BELDEX HIGH DEMAND SURGE DETECTED", sms_text, discord_fields)))

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
                "🧪 *BELDEX SENTINEL TEST ALERT (Telegram)*\n\n"
                "✅ Telegram notifications are working correctly!\n"
                "You will receive instant alerts here whenever a 30-minute Beldex price surge is detected on nexus.vin / markets."
            )
            success = await self.send_telegram(text)
            detail = "Telegram test message dispatched"
        elif channel == "email":
            subj = "🧪 BELDEX SENTINEL: Test Email Alert"
            text = "This is a test alert from Beldex Nexus Rate Sentinel. Your email notifications are configured and functioning properly!"
            success = await self.send_email(subj, text, f"<div style='padding:20px;background:#1e293b;color:#f8fafc;border-radius:8px;'><h2>✅ Test Alert Successful</h2><p>{text}</p></div>")
            detail = "Email test dispatched"
        elif channel == "sms":
            text = "Beldex Sentinel: Test SMS alert received. High demand notifications will arrive on this number."
            success = await self.send_sms(text)
            detail = "SMS test dispatched"
        elif channel == "discord":
            fields = [{"name": "Test Status", "value": "Passed", "inline": True}]
            success = await self.send_discord("🧪 Test Alert", "Discord webhook configured and verified!", fields)
            detail = "Discord test dispatched"
        else:
            return {"status": "error", "message": f"Unknown channel '{channel}'"}

        return {"status": "ok" if success else "failed", "channel": channel, "detail": detail}

notification_service = NotificationService()
