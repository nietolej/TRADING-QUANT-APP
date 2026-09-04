import os
import logging
from typing import Optional, Dict, Any
import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("TelegramNotifier")

class TelegramNotifier:
    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        
        self.enabled = bool(self.bot_token and self.chat_id)
        if not self.enabled:
            logger.debug("TelegramNotifier: Credenciales incompletas en .env. Notificaciones deshabilitadas.")

    def send_message(self, text: str):
        if not self.enabled:
            return
            
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        try:
            response = requests.post(url, json=payload, timeout=5)
            response.raise_for_status()
        except Exception as e:
            logger.warning("Error enviando mensaje de Telegram: %s", e)

    def send_alert(self, title: str, details: Optional[Dict[str, Any]] = None, is_critical: bool = True):
        """Envía una alerta destacada a Telegram con formato visual para operadores."""
        if not self.enabled:
            return

        header_icon = "🚨 <b>[ALERTA CRÍTICA]</b>" if is_critical else "⚠️ <b>[ADVERTENCIA]</b>"
        lines = [f"{header_icon} {title}"]

        if details:
            for k, v in details.items():
                lines.append(f"• <b>{k}:</b> <code>{v}</code>")

        message = "\n".join(lines)
        self.send_message(message)
