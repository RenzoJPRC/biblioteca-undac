"""
===============================================================================
  SISTEMA BIBLIOTECARIO UNDAC — MÓDULO TELEGRAM BOT API (REFACTORIZADO)
===============================================================================
  Este helper envía notificaciones Push instantáneas al celular del administrador
  o supervisor de seguridad mediante la API REST pública de Telegram.

  URL de la API Telegram:
  https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/sendMessage

  Parámetros requeridos por Telegram:
  - chat_id: ID numérico del usuario o grupo destinatario (ej. 1173510315).
  - text: El mensaje formateado en sintaxis Markdown.
  - parse_mode: 'Markdown' para soportar negritas (*texto*) e iconos emoji.
===============================================================================
"""

import requests
import os
from datetime import datetime

# Token oficial del Bot (@undac_biblioteca_bot) y Chat ID del Administrador desde .env
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def enviar_notificacion_ingreso_telegram(nombre_usuario, piso, sala, hora_str=None):
    """
    📌 PROPÓSITO: Enviar un mensaje de notificación push en vivo cada vez que
    se concede acceso a un alumno/visitante en cualquier sala o molinete.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        # Si no están configuradas las credenciales, se omite de forma segura
        return False

    if not hora_str:
        hora_str = datetime.now().strftime("%H:%M:%S")

    # Formateo estricto del mensaje solicitado: Usuario, Piso, Sala y Hora
    mensaje = (
        f"📌 *NUEVO INGRESO REGISTRADO*\n\n"
        f"👤 *Usuario:* {nombre_usuario}\n"
        f"🏢 *Piso:* Piso {piso}\n"
        f"🚪 *Sala:* {sala}\n"
        f"⏰ *Hora:* {hora_str}\n"
    )

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            'chat_id': TELEGRAM_CHAT_ID,
            'text': mensaje,
            'parse_mode': 'Markdown'
        }
        # Timeout de 3 segundos para garantizar que no ralentice la red
        response = requests.post(url, json=payload, timeout=3)
        return response.status_code == 200

    except Exception as e:
        print(f"[ERROR TELEGRAM BOT API] {e}")
        return False
