# test_send.py
import os
from dotenv import load_dotenv
from twilio.rest import Client

load_dotenv()  # reads .env from the current folder

client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
try:
    m = client.messages.create(
        from_=os.environ["TWILIO_WHATSAPP_FROM"],
        to="whatsapp:+918595856142",
        body="custom test message",
    )
    print("Queued:", m.sid, m.status)
except Exception as e:
    print("ERROR:", e)