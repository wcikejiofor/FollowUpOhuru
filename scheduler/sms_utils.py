import logging
import json
from datetime import datetime, timedelta
import pytz
import dateparser
import json
import logging
import pytz
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from .google_calendar import get_calendar_service, get_timezone_from_phone
from django.conf import settings
from dateutil import parser

from twilio.rest import Client
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from .models import UserProfile, Event
from .google_calendar import get_timezone_from_phone, get_calendar_service

import json
import logging
import traceback
from datetime import datetime, timedelta
import pytz
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from django.conf import settings
from .models import Event
from .utils import get_timezone_from_phone, create_event_in_calendar, cancel_event, \
    get_available_slots, format_slots_message

from .utils import (
    parse_event_details,
    create_event_in_calendar,
    cancel_event
)
from .sms_sender import send_sms

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

class SMSScheduler:
    def __init__(self, user_profile):
        self.user_profile = user_profile
        self.twilio_client = Client(
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_AUTH_TOKEN
        )

    def send_modification_confirmation_sms(self, to_number, old_time, new_time, summary):
        """Send confirmation for modified event"""
        try:
            # Format times
            old_time_str = old_time.strftime("%I:%M %p").lstrip('0')
            new_time_str = new_time.strftime("%I:%M %p").lstrip('0')
            date_str = new_time.strftime("%A")

            # Format event name
            event_name = summary.replace('Meeting', 'meeting')

            message = (
                f"✅ {event_name} moved from {old_time_str} to {new_time_str} on {date_str}. "

            )
            self._send_sms(to_number, message)

        except Exception as e:
            logger.error(f"Error sending modification SMS: {e}")
            logger.error(traceback.format_exc())

    def send_confirmation_sms(self, to_number, event_details):
        """Send a confirmation SMS for a scheduled event"""
        try:
            # Debug logging
            logger.debug(f"Raw event details: {event_details}")
            logger.debug(f"Summary before processing: {event_details.get('summary')}")

            # Parse the datetime
            event_start = datetime.fromisoformat(event_details['start_time'])
            event_end = datetime.fromisoformat(event_details['end_time'])

            # Format times without leading zero
            start_time_str = event_start.strftime("%I:%M %p").lstrip('0')
            end_time_str = event_end.strftime("%I:%M %p").lstrip('0')

            # Use the existing summary directly
            summary = event_details.get('summary', 'Meeting')

            # Debug logging
            logger.debug(f"Summary after processing: {summary}")

            # Customize message to show both start and end times
            message = (
                f"✅ **{summary} scheduled from {start_time_str} to {end_time_str}.**"
            )
            self._send_sms(to_number, message)

        except Exception as e:
            logger.error(f"Error sending confirmation SMS: {e}")
            logger.error(traceback.format_exc())

    def send_reminder_sms(self, to_number, event_details, minutes_before=30):
        """Schedule a reminder SMS for before an event"""
        try:
            event_time = datetime.fromisoformat(event_details['start_time'])
            reminder_time = event_time - timedelta(minutes=minutes_before)
            current_time = datetime.now(event_time.tzinfo)

            # Only log that we would schedule if reminder time is in future
            if reminder_time > current_time:
                logger.info(f"Would schedule reminder for {to_number} at {reminder_time}")
                # TODO: Implement actual scheduling using Celery or similar
                return

            # Don't send immediate reminder if it's not time yet
            if current_time < reminder_time:
                return

        except Exception as e:
            logger.error(f"Error scheduling reminder SMS: {e}")
            logger.error(traceback.format_exc())

    def send_cancellation_confirmation_sms(self, to_number, event_details):
        """Send a cancellation confirmation SMS with undo option"""
        try:
            event_time = datetime.fromisoformat(event_details['start_time'])
            time_str = event_time.strftime("%I:%M %p").lstrip('0')

            message = (
                f"❌ {event_details['summary']} at {time_str} has been canceled. "

            )
            self._send_sms(to_number, message)
        except Exception as e:
            logger.error(f"Error sending cancellation SMS: {e}")
            logger.error(traceback.format_exc())

    def _send_sms(self, to_number, message):
        """Internal method to send SMS"""
        try:
            self.twilio_client.messages.create(
                body=message,
                from_=settings.TWILIO_PHONE_NUMBER,
                to=to_number
            )
        except Exception as e:
            logger.error(f"SMS sending error: {e}")
            logger.error(traceback.format_exc())


class EventManager:
    def __init__(self, user_profile):
        logger.debug("Initializing EventManager")
        self.user_profile = user_profile
        self.sms_scheduler = SMSScheduler(user_profile)
        logger.debug(f"EventManager methods: {dir(self)}")

    def cancel_event(self, event_details, phone_number):
        try:
            credentials = self._get_pro_credentials()
            if not credentials:
                return False, "Please authenticate with Google Calendar first."

            service = build('calendar', 'v3', credentials=credentials)
            user_tz = pytz.timezone(get_timezone_from_phone(phone_number))

            # Parse the cancellation time
            cancel_time = datetime.fromisoformat(event_details['start_time'])
            cancel_time = cancel_time.astimezone(user_tz)

            # Search for events in a small time window around the specified time
            time_min = (cancel_time - timedelta(minutes=5)).isoformat()
            time_max = (cancel_time + timedelta(minutes=5)).isoformat()

            events_result = service.events().list(
                calendarId='primary',
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            events = events_result.get('items', [])

            # Find the most precise match
            matching_events = [
                event for event in events
                if
                abs(datetime.fromisoformat(event['start']['dateTime']) - cancel_time) < timedelta(
                    minutes=5)
            ]

            # If there's an event with a matching summary, prioritize that
            if 'summary' in event_details:
                matching_events = [
                    event for event in matching_events
                    if event.get('summary', '').lower() == event_details['summary'].lower()
                ]

            if not matching_events:
                return False, f"No event found at {cancel_time.strftime('%A, %B %d at %I:%M %p')}."

            # Cancel the most precise match
            event_to_cancel = matching_events[0]
            service.events().delete(calendarId='primary', eventId=event_to_cancel['id']).execute()

            return True, f"Event canceled: {cancel_time.strftime('%A, %B %d at %I:%M %p')}"

        except Exception as e:
            logger.error(f"Error in canceling event: {str(e)}")
            logger.error(traceback.format_exc())
            return False, f"An error occurred while canceling the event: {str(e)}. Please try again."

    def suggest_slots(self, event_details, phone_number):
        """Suggest available time slots for next week"""
        try:
            credentials = self._get_pro_credentials()
            if not credentials:
                return "Please authenticate with Google Calendar first."

            user_tz = pytz.timezone(get_timezone_from_phone(phone_number))
            now = datetime.now(user_tz)
            next_week_start = (now + timedelta(days=(7 - now.weekday()))).replace(hour=9, minute=0,
                                                                                  second=0,
                                                                                  microsecond=0)
            next_week_end = next_week_start + timedelta(days=5)  # Monday to Friday

            available_slots = get_available_slots(credentials, next_week_start, next_week_end)

            if not available_slots:
                return "No available slots found for next week."

            formatted_slots = []
            for i, slot in enumerate(available_slots[:3], 1):
                formatted_slots.append(f"{i}. {slot.strftime('%A at %I:%M %p')}")

            return "Based on your availability, here are the best open slots next week:\n" + "\n".join(
                formatted_slots) + "\n\nReply with 1, 2, or 3 to confirm, or reply with a different preferred time."
        except Exception as e:
            logger.error(f"Error suggesting slots: {e}")
            return "Sorry, I couldn't get available slots at this time."

    def schedule_smart_event(self, event_details, preferred_time_str, phone_number):
        try:
            # Get the user profile
            user_profile = UserProfile.objects.get(phone_number=phone_number)

            # Log the current state of Google credentials
            logger.debug(f"Checking Google credentials for {phone_number}")
            logger.debug(f"Current credentials: {user_profile.google_credentials}")

            # Check credentials
            credentials = self._get_pro_credentials()

            if not credentials:
                # Generate Google OAuth authentication link
                auth_link = f"{settings.BASE_URL}/authorize/{user_profile.id}/"

                logger.warning(f"No Google credentials found for {phone_number}")
                logger.debug(f"Generated auth link: {auth_link}")

                # Send SMS with authentication link
                send_sms(phone_number,
                         "Please authenticate your Google Calendar to schedule events. "
                         f"Click this link to connect: {auth_link}")

                return False, (
                    "Please authenticate with Google Calendar first. "
                    "We've sent you a link to connect your account."
                )

            # Get user's timezone
            user_tz = pytz.timezone(get_timezone_from_phone(phone_number))

            # Parse preferred time
            preferred_time = dateparser.parse(
                preferred_time_str,
                settings={
                    'TIMEZONE': str(user_tz),
                    'RETURN_AS_TIMEZONE_AWARE': True
                }
            )

            # Ensure timezone awareness
            if preferred_time and not preferred_time.tzinfo:
                preferred_time = user_tz.localize(preferred_time)

            # Handle different time input scenarios
            if 'start_time' in event_details:
                preferred_time = datetime.fromisoformat(event_details['start_time'])
            elif not preferred_time:
                # Suggest available slots if no time provided
                start_date = datetime.now(user_tz) + timedelta(days=1)  # tomorrow
                end_date = start_date + timedelta(days=7)  # Look ahead for a week
                available_slots = self.get_available_slots_for_range(
                    credentials, start_date, end_date, user_tz
                )

                if available_slots:
                    slot_messages = [
                        f"{i + 1}. {slot.strftime('%A, %B %d at %I:%M %p')}"
                        for i, slot in enumerate(available_slots[:5])
                    ]
                    return False, (
                            "No time specified. Here are some available slots:\n" +
                            "\n".join(slot_messages) +
                            "\nReply with the number of your preferred slot or specify a different time."
                    )
                else:
                    return False, "No available slots found. Please try a different date range."

            # Validate the time
            current_time = datetime.now(user_tz)
            if preferred_time <= current_time:
                return False, "Please provide a future time."

            # Check slot availability and create event
            if self.is_slot_available(credentials, preferred_time, user_tz):
                # Prepare event details
                # Prepare event details
                event_details['start_time'] = preferred_time.isoformat()

                # Check if end time was specified, otherwise default to 1 hour
                if 'end_time' not in event_details:
                    event_details['end_time'] = (preferred_time + timedelta(hours=1)).isoformat()

                # Default summary if not provided
                if 'summary' not in event_details:
                    event_details['summary'] = event_details.get('summary',
                                                                 f"Meeting with {event_details.get('attendee', 'Someone')}")

                # Create the event
                success = self.create_event(event_details, phone_number)

                if success:
                    return True, f"Event scheduled for {preferred_time.strftime('%A, %B %d at %I:%M %p')}"
                else:
                    return False, "Failed to create the event. Please try again."
            else:
                return False, f"The requested time ({preferred_time.strftime('%A, %B %d at %I:%M %p')}) is not available. Please choose a different time."

        except UserProfile.DoesNotExist:
            logger.error(f"No user profile found for phone number {phone_number}")
            return False, "User profile not found. Please contact support."
        except Exception as e:
            logger.error(f"Error in smart scheduling: {str(e)}")
            logger.error(traceback.format_exc())
            return False, f"An error occurred while scheduling: {str(e)}. Please try again."

    def get_available_slots_for_range(self, credentials, start_date, end_date, user_tz):
        available_slots = []
        current_date = start_date
        while current_date < end_date:
            for hour in [9, 10, 11, 13, 14, 15, 16]:  # 9 AM to 5 PM, excluding 12 PM
                slot = current_date.replace(hour=hour)
                if self.is_slot_available(credentials, slot, user_tz):
                    available_slots.append(slot)
            current_date += timedelta(days=1)
        return available_slots

    def is_slot_available(self, credentials, slot, user_tz):
        # Ensure slot is timezone-aware
        if not slot.tzinfo:
            slot = user_tz.localize(slot)

        # Always return True to allow overlapping events
        return True

    def _get_pro_credentials(self):
        """Get Google credentials for Pro users"""
        try:
            if not self.user_profile.google_credentials:
                logger.error("No Google credentials found for user")
                return None

            creds_data = json.loads(self.user_profile.google_credentials)
            return Credentials(
                token=creds_data['token'],
                refresh_token=creds_data.get('refresh_token'),
                token_uri=creds_data.get('token_uri'),
                client_id=creds_data.get('client_id'),
                client_secret=creds_data.get('client_secret'),
                scopes=creds_data['scopes']
            )
        except Exception as e:
            logger.error(f"Error getting pro credentials: {e}")
            return None

    def create_event(self, event_details, phone_number):
        try:
            credentials = self._get_pro_credentials()
            user_timezone = get_timezone_from_phone(phone_number)

            event_time = datetime.fromisoformat(event_details['start_time'])
            end_time = datetime.fromisoformat(event_details['end_time'])

            event = {
                'summary': event_details.get('summary', 'Appointment'),
                'location': event_details.get('location', ''),
                'description': event_details.get('description', ''),
                'start': {
                    'dateTime': event_time.isoformat(),
                    'timeZone': user_timezone,
                },
                'end': {
                    'dateTime': end_time.isoformat(),
                    'timeZone': user_timezone,
                },
            }

            service = build('calendar', 'v3', credentials=credentials)
            event = service.events().insert(calendarId='primary', body=event).execute()

            logger.info(f"Event created: {event.get('htmlLink')}")
            return True
        except Exception as e:
            logger.error(f"Event creation error: {e}")
            logger.error(traceback.format_exc())
            return False

    def _create_free_event(self, event_details, phone_number):
        """Create event for free plan users"""
        try:
            user_tz = pytz.timezone(get_timezone_from_phone(phone_number))
            for event_info in event_details:
                start_time = datetime.fromisoformat(event_info['start_time'])
                end_time = datetime.fromisoformat(event_info['end_time'])

                if not start_time.tzinfo:
                    start_time = user_tz.localize(start_time)
                if not end_time.tzinfo:
                    end_time = user_tz.localize(end_time)

                Event.objects.create(
                    user_profile=self.user_profile,
                    summary=event_info['summary'],
                    start_time=start_time,
                    end_time=end_time,
                    location=event_info.get('location', 'Virtual Meeting')
                )
            return True
        except Exception as e:
            logger.error(f"Error creating free event: {e}")
            return False

    def modify_event(self, modification_details, phone_number):
        """Modify an existing event"""
        try:
            if self.user_profile.subscription_plan == 'pro':
                credentials = self._get_pro_credentials()
                return self._modify_pro_event(modification_details, credentials, phone_number)
            else:
                return self._modify_free_event(modification_details, phone_number)
        except Exception as e:
            logger.error(f"Event modification error: {e}")
            return False

    def _modify_pro_event(self, modification_details, credentials, phone_number):
        """Modify  event in Google Calendar"""
        try:
            service = get_calendar_service(credentials)  # Use new service getter
            if not service:
                logger.error("Failed to get calendar service")
                return False

            user_tz = pytz.timezone(get_timezone_from_phone(phone_number))
            search_time = modification_details['original_time']

            if not search_time.tzinfo:
                search_time = user_tz.localize(search_time)
            search_time_utc = search_time.astimezone(pytz.UTC)

            events_result = service.events().list(
                calendarId='primary',
                timeMin=(search_time_utc - timedelta(minutes=5)).isoformat(),
                timeMax=(search_time_utc + timedelta(minutes=65)).isoformat(),
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])
            if events:
                event = events[0]
                new_start_time = datetime.fromisoformat(
                    modification_details['changes']['new_start_time'])
                new_end_time = datetime.fromisoformat(
                    modification_details['changes']['new_end_time'])

                event['start']['dateTime'] = new_start_time.isoformat()
                event['end']['dateTime'] = new_end_time.isoformat()

                updated_event = service.events().update(
                    calendarId='primary',
                    eventId=event['id'],
                    body=event
                ).execute()

                # Send modified event confirmation
                self.sms_scheduler.send_modification_confirmation_sms(
                    phone_number,
                    search_time,
                    new_start_time,
                    event.get('summary', 'Meeting')
                )
                return True
            return False
        except Exception as e:
            logger.error(f"Error modifying pro event: {e}")
            return False

    def _modify_free_event(self, modification_details, phone_number):
        """Modify Free plan event"""
        try:
            user_tz = pytz.timezone(get_timezone_from_phone(phone_number))
            event = Event.objects.get(
                user_profile=self.user_profile,
                start_time=modification_details['original_time']
            )

            new_start = datetime.fromisoformat(modification_details['changes']['new_start_time'])
            new_end = datetime.fromisoformat(modification_details['changes']['new_end_time'])

            if not new_start.tzinfo:
                new_start = user_tz.localize(new_start)
            if not new_end.tzinfo:
                new_end = user_tz.localize(new_end)

            old_start = event.start_time
            event.start_time = new_start
            event.end_time = new_end
            event.save()

            # Send modified event confirmation
            self.sms_scheduler.send_modification_confirmation_sms(
                phone_number,
                old_start,
                new_start,
                event.summary
            )
            return True
        except Event.DoesNotExist:
            logger.error("Event not found")
            return False
        except Exception as e:
            logger.error(f"Error modifying free event: {e}")
            return False

    def cancel_event(self, event_details, phone_number):
        try:
            credentials = self._get_pro_credentials()
            if not credentials:
                return False, "Please authenticate with Google Calendar first."

            service = build('calendar', 'v3', credentials=credentials)
            user_tz = pytz.timezone(get_timezone_from_phone(phone_number))

            # Parse the cancellation time
            cancel_time = datetime.fromisoformat(event_details['start_time'])
            cancel_time = cancel_time.astimezone(user_tz)

            # Search for events in a small time window around the specified time
            time_min = (cancel_time - timedelta(minutes=5)).isoformat()
            time_max = (cancel_time + timedelta(minutes=5)).isoformat()

            events_result = service.events().list(calendarId='primary', timeMin=time_min,
                                                  timeMax=time_max, singleEvents=True,
                                                  orderBy='startTime').execute()
            events = events_result.get('items', [])

            if not events:
                return False, f"No event found at {cancel_time.strftime('%A, %B %d at %I:%M %p')}."

            # Cancel the first event found in the time window
            event = events[0]
            service.events().delete(calendarId='primary', eventId=event['id']).execute()

            return True, f"Event canceled: {cancel_time.strftime('%A, %B %d at %I:%M %p')}"

        except Exception as e:
            logger.error(f"Error in canceling event: {str(e)}")
            logger.error(traceback.format_exc())
            return False, f"An error occurred while canceling the event: {str(e)}. Please try again."

    def _cancel_free_event(self, event_details, phone_number):
        """Cancel Free plan event"""
        try:
            user_tz = pytz.timezone(get_timezone_from_phone(phone_number))
            event_time = datetime.fromisoformat(event_details[0]['start_time'])

            if not event_time.tzinfo:
                event_time = user_tz.localize(event_time)

            Event.objects.filter(
                user_profile=self.user_profile,
                start_time=event_time
            ).delete()
            return True
        except Exception as e:
            logger.error(f"Error cancelling free event: {e}")
            return False

    def get_suggested_slots(self, event_details, phone_number):
        """Get the suggested slots for the pending event"""
        # This method should return the same slots that were suggested in suggest_slots
        # You might want to store these slots in the session or recalculate them
        # For now, we'll recalculate them
        credentials = self._get_pro_credentials()
        user_tz = pytz.timezone(get_timezone_from_phone(phone_number))
        now = datetime.now(user_tz)
        next_week_start = (now + timedelta(days=(7 - now.weekday()))).replace(hour=9, minute=0,
                                                                              second=0,
                                                                              microsecond=0)
        next_week_end = next_week_start + timedelta(days=5)
        return get_available_slots(credentials, next_week_start, next_week_end)[:3]

