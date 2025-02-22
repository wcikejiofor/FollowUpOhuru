# scheduler/sms_sender.py
from twilio.rest import Client
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


def send_sms(to_number, message):
    """
    Send an SMS message using Twilio

    Args:
        to_number (str): Phone number to send SMS to
        message (str): Message content
    """
    try:
        # Initialize Twilio client
        client = Client(
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_AUTH_TOKEN
        )

        # Send the SMS
        message = client.messages.create(
            body=message,
            from_=settings.TWILIO_PHONE_NUMBER,
            to=to_number
        )

        # Log successful SMS send
        logger.info(f"SMS sent to {to_number}")

        return True
    except Exception as e:
        # Log any errors in sending SMS
        logger.error(f"Error sending SMS to {to_number}: {str(e)}")
        return False