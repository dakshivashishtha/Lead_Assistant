import json
import os
import requests
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks

from core.config import get_settings
from core.logging_setup import configure_logging
from database import repository as repo
from database.connection import connect
from services import conversation_service, lead_service

load_dotenv()
log = configure_logging()
app = FastAPI(title="WhatsApp Lead Assistant Webhook")


def send_whatsapp_reply(to_id: str, text: str) -> None:
    """Sends a text reply back via Meta's official WhatsApp Cloud API."""
    access_token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()

    if not access_token or not phone_number_id:
        log.error("Missing WHATSAPP_ACCESS_TOKEN or WHATSAPP_PHONE_NUMBER_ID in environment.")
        return

    url = f"https://graph.facebook.com/v26.0/{phone_number_id}/messages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to_id,
        "type": "text",
        "text": {"body": text},
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=15)
    if not resp.ok:
        log.error("WhatsApp API error (%s): %s", resp.status_code, resp.text)
    else:
        log.info("WhatsApp reply sent successfully to %s", to_id)


def handle_whatsapp_turn(sender_id: str, text: str) -> None:
    """Runs lead extraction, scoring, persistence, and reply pipeline with full error logging."""
    try:
        load_dotenv(override=True)
        settings = get_settings()

        with connect(settings.database_path) as conn:
            # 1. Reuse an existing open conversation for this sender, or start a new one
            existing_convs = repo.list_conversations(conn, None)
            conv_id = next(
                (c["id"] for c in existing_convs if c.get("sender_phone") == sender_id),
                None,
            )
            if conv_id is None:
                conv_id = lead_service.start_conversation(
                    conn,
                    sender_phone=sender_id,
                    default_country_code=settings.default_country_code,
                )

            # 2. Save the customer's incoming message
            lead_service.record_customer_message(conn, conv_id, text)

            # 3. Run Groq extraction + deterministic scoring + reply generation
            result = conversation_service.process_turn(conn, conv_id, settings, generate=True)

            # 4. Read the generated assistant reply from the database
            messages = repo.get_messages(conn, conv_id)
            assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
            if result.ok and assistant_msgs:
                reply_text = assistant_msgs[-1]["content"]