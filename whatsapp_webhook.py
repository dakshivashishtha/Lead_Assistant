
from flask import Flask, request, Response
from twilio.twiml.messaging_response import MessagingResponse

app = Flask(__name__)

@app.route("/reply_whatsapp", methods=["POST"])
def reply_whatsapp():
    incoming_message = request.form.get("Body", "")

    print("Received WhatsApp message:", incoming_message)

    response = MessagingResponse()
    response.message(
        "Hello! Your Python webhook is connected successfully."
    )

    return Response(str(response), mimetype="text/xml")


if __name__ == "__main__":
    app.run(port=3000, debug=True)
