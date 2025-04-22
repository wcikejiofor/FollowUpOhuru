# scheduler/sms_sender.py
from twilio.rest import Client
from django.conf import settings
import logging
import traceback

logger = logging.getLogger(__name__)


def send_sms(to_number, message):
    """
    Send an SMS message using Twilio

    Args:
        to_number (str): Phone number to send SMS to
        message (str): Message content
    """
    try:
        # Log all details before sending
        logger.info(f"Attempting to send SMS")
        logger.info(f"To Number: {to_number}")
        logger.info(f"Message: {message}")
        logger.info(f"Twilio Account SID: {settings.TWILIO_ACCOUNT_SID}")
        logger.info(f"Twilio Phone Number: {settings.TWILIO_PHONE_NUMBER}")

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
        logger.info(f"SMS sent successfully to {to_number}")
        logger.info(f"Twilio Message SID: {message.sid}")

        return True
    except Exception as e:
        # Log comprehensive error details
        logger.error(f"Error sending SMS to {to_number}")
        logger.error(f"Error details: {str(e)}")
        logger.error(f"Full traceback: {traceback.format_exc()}")
        return False