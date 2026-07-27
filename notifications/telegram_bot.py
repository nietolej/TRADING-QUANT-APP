import os
import requests
from dotenv import load_dotenv

load_dotenv()

class TelegramNotifier:
    def __init__(self):
        self.bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        
        self.enabled = bool(self.bot_token and self.chat_id)
        if not self.enabled:
            print("TelegramNotifier: Credenciales incompletas en .env. Notificaciones deshabilitadas.")

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
            print(f"Error enviando mensaje de Telegram: {e}")
