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

            lead_service.record_customer_message(conn, conv_id, text)
            result = conversation_service.process_turn(conn, conv_id, settings, generate=True)

            messages = repo.get_messages(conn, conv_id)
            assistant_msgs = [m for m in messages if m.get("role") == "assistant"]
            if result.ok and assistant_msgs:
                reply_text = assistant_msgs[-1]["content"]
            else:
                reply_text = "Thank you for reaching out! Our team has noted your inquiry and will get back to you shortly."

        send_whatsapp_reply(sender_id, reply_text)
    except Exception as exc:
        log.exception("Failed processing WhatsApp turn for %s: %s", sender_id, exc)


@app.get("/")
async def health():
    return {"status": "ok", "service": "WhatsApp Lead Assistant"}


@app.get("/webhook")
async def verify_webhook(request: Request):
    """Meta webhook verification handshake."""
    load_dotenv(override=True)
    verify_token = os.getenv("WHATSAPP_VERIFY_TOKEN", "leadbot_secret_2026").strip()

    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == verify_token:
        return int(params.get("hub.challenge"))
    raise HTTPException(status_code=403, detail="Invalid verify token")


@app.post("/webhook")
async def receive_whatsapp(request: Request, background_tasks: BackgroundTasks):
    """Receives incoming WhatsApp messages and status updates from Meta."""
    raw_body = await request.body()
    if not raw_body:
        return {"status": "ok"}

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        log.warning("Non-JSON payload received: %s", raw_body)
        return {"status": "ok"}

    log.info("INCOMING WHATSAPP PAYLOAD: %s", json.dumps(body))

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            contacts = value.get("contacts", [])
            wa_id = contacts[0].get("wa_id", "") if contacts else ""

            for msg in value.get("messages", []):
                if msg.get("type") == "text":
                    sender_id = wa_id or msg.get("from", "")
                    text = msg.get("text", {}).get("body", "").strip()
                    if sender_id and text:
                        background_tasks.add_task(handle_whatsapp_turn, sender_id, text)

    return {"status": "ok"}