import logging
import json
from datetime import datetime, timedelta
import pytz
import dateparser
import traceback
import re
from django.conf import settings
from openai import OpenAI
import phonenumbers
from googleapiclient.discovery import build
from googleapiclient.discovery_cache.base import Cache

logger = logging.getLogger(__name__)

# Initialize OpenAI client
client = OpenAI(api_key=settings.OPENAI_API_KEY)


class MemoryCache(Cache):
    _CACHE = {}

    def get(self, url):
        return MemoryCache._CACHE.get(url)

    def set(self, url, content):
        MemoryCache._CACHE[url] = content


def get_calendar_service(credentials):
    return build('calendar', 'v3', credentials=credentials, cache=MemoryCache())


def parse_event_details(incoming_msg, phone_number):
    user_timezone = pytz.timezone(get_timezone_from_phone(phone_number))
    current_date = datetime.now(user_timezone)

    # Extract reminder minutes using regex
    reminder_match = re.search(r'remind me (\d+)\s*(minute|minutes|min)', incoming_msg)
    reminder_minutes = None
    if reminder_match:
        reminder_minutes = int(reminder_match.group(1))
        # Remove reminder text from the message
        incoming_msg = incoming_msg.replace(reminder_match.group(0), '').strip()

    # Use OpenAI to parse the message
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": f"""
            Parse the event details and return a JSON response.
            Current time: {current_date.strftime('%Y-%m-%d %H:%M')}
            User timezone: {user_timezone}

            Return JSON format:
            {{
                "action": "schedule" or "cancel",
                "event": {{
                    "summary": "EXACT NAME OF THE APPOINTMENT/MEETING",
                    "location": "FULL ADDRESS OR LOCATION IF PROVIDED",
                    "start_time": "YYYY-MM-DD HH:MM:SS",
                    "duration_minutes": 60,
                    "reminder_minutes": null or number of minutes before event
                }}
            }}

            RULES:
            - Use 24-hour time format (00-23)
            - IMPORTANT: Capture the EXACT appointment name and location from the message
            - If an address or location is provided, include it in the location field
            - If "tomorrow" is mentioned, use tomorrow's date
            - If a specific time is mentioned, use it
            - If no specific time is mentioned, don't include a start_time
            - Set "action" to "cancel" if the message is about canceling an event
            """},
            {"role": "user", "content": incoming_msg}
        ],
        response_format={"type": "json_object"}
    )

    parsed_data = json.loads(response.choices[0].message.content)

    event = parsed_data['event']

    # Override or add reminder minutes
    if reminder_minutes is not None:
        event['reminder_minutes'] = reminder_minutes

    if 'start_time' in event:
        # Parse the time with timezone awareness
        start_time = dateparser.parse(
            event['start_time'],
            settings={
                'TIMEZONE': str(user_timezone),
                'RETURN_AS_TIMEZONE_AWARE': True
            }
        )

        # Ensure timezone awareness
        if start_time and not start_time.tzinfo:
            start_time = user_timezone.localize(start_time)

        # Convert to ISO format
        event['start_time'] = start_time.isoformat()
        event['end_time'] = (
                    start_time + timedelta(minutes=event.get('duration_minutes', 60))).isoformat()

    return parsed_data['action'], event

def parse_modification_details(message, phone_number):
    """Parse modification details from incoming SMS"""
    try:
        # Get user's timezone
        user_timezone = pytz.timezone(get_timezone_from_phone(phone_number))
        current_date = datetime.now(user_timezone)

        logger.debug(f"Parsing modification. Message: {message}")
        logger.debug(f"Current date: {current_date}, Timezone: {user_timezone}")

        # Use OpenAI to parse the modification details
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": f"""
                Parse the meeting modification details and return a JSON response.
                Current time: {current_date.strftime('%Y-%m-%d %H:%M')}
                User timezone: {user_timezone}

                The input will be like:
                - "Move meeting from 3pm to 4pm"
                - "Change 11am meeting to 2pm"
                - "Move the meeting from 11 today to 1107 today"

                Return JSON format:
                {{
                    "original_time": "YYYY-MM-DD HH:MM",
                    "new_time": "YYYY-MM-DD HH:MM"
                }}

                Rules:
                - Use current date if no date specified
                - Use 24-hour format for times
                - If time is in format like "1107", interpret as "11:07"
                - Maintain same date unless specifically changed
                - Both times must be on same date unless otherwise specified
                """},
                {"role": "user", "content": message}
            ],
            response_format={"type": "json_object"}
        )

        parsed_data = json.loads(response.choices[0].message.content)
        logger.debug(f"OpenAI response: {parsed_data}")

        # Parse times with robust timezone handling
        original_time = dateparser.parse(
            parsed_data['original_time'],
            settings={
                'TIMEZONE': str(user_timezone),
                'RETURN_AS_TIMEZONE_AWARE': True,
                'PREFER_DATES_FROM': 'current_period',
                'RELATIVE_BASE': current_date
            }
        )

        new_time = dateparser.parse(
            parsed_data['new_time'],
            settings={
                'TIMEZONE': str(user_timezone),
                'RETURN_AS_TIMEZONE_AWARE': True,
                'PREFER_DATES_FROM': 'current_period',
                'RELATIVE_BASE': current_date
            }
        )

        logger.debug(f"Parsed original time: {original_time}")
        logger.debug(f"Parsed new time: {new_time}")

        # Validate parsing
        if not original_time or not new_time:
            logger.error("Failed to parse times")
            return None

        # Ensure times are timezone-aware and on the same date
        if not original_time.tzinfo:
            original_time = user_timezone.localize(original_time)
        if not new_time.tzinfo:
            new_time = user_timezone.localize(new_time)

        # Ensure new time is on the same date as original time if not explicitly changed
        if original_time.date() != new_time.date():
            # Keep the original date, update only the time
            new_time = new_time.replace(
                year=original_time.year,
                month=original_time.month,
                day=original_time.day
            )

        # Calculate end times (1 hour after start times)
        new_end_time = new_time + timedelta(hours=1)

        # Validate times
        if new_time < current_date:
            logger.warning(f"New time {new_time} is in the past")
            return None

        return {
            'original_time': original_time,
            'changes': {
                'new_start_time': new_time.isoformat(),
                'new_end_time': new_end_time.isoformat()
            }
        }

    except Exception as e:
        logger.error(f"Error parsing modification details: {e}")
        logger.error(traceback.format_exc())
        return None


def create_event_in_calendar(event_details, credentials, user_timezone):
    """
    Create events in Google Calendar
    """
    try:
        service = get_calendar_service(credentials)

        if isinstance(event_details, list):
            created_events = []
            for event in event_details:
                event_body = {
                    'summary': event.get('summary', 'Appointment'),
                    'start': {
                        'dateTime': event['start_time'],
                        'timeZone': user_timezone,
                    },
                    'end': {
                        'dateTime': event['end_time'],
                        'timeZone': user_timezone,
                    },
                    'location': event.get('location', 'Virtual Meeting')
                }
                try:
                    created_event = service.events().insert(
                        calendarId='primary',
                        body=event_body
                    ).execute()
                    created_events.append(created_event)
                except Exception as e:
                    logger.error(f"Error creating individual event: {e}")
                    continue

        return True

    except Exception as e:
        logger.error(f"Error in create_event_in_calendar: {e}")
        return False


def cancel_event(credentials, event_details, user_timezone):
    try:
        service = get_calendar_service(credentials)
        user_tz = pytz.timezone(user_timezone)

        search_time = datetime.fromisoformat(event_details['start_time'])
        logger.debug(f"Attempting to cancel event at {search_time} in timezone {user_timezone}")

        # Ensure timezone awareness
        if not search_time.tzinfo:
            search_time = user_tz.localize(search_time)

        # Convert to UTC for API
        search_time_utc = search_time.astimezone(pytz.UTC)

        logger.debug(f"Searching for events between {search_time_utc - timedelta(minutes=30)} and {search_time_utc + timedelta(minutes=30)}")

        # Create a wider search window
        events_result = service.events().list(
            calendarId='primary',
            timeMin=(search_time_utc - timedelta(minutes=30)).isoformat(),
            timeMax=(search_time_utc + timedelta(minutes=30)).isoformat(),
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])
        logger.debug(f"Found {len(events)} events in the search window")

        # Find matching event
        for event in events:
            event_start = datetime.fromisoformat(event['start']['dateTime'].replace('Z', '+00:00'))
            logger.debug(f"Comparing event at {event_start} with search time {search_time_utc}")
            # Allow for a small time difference (5 minutes)
            time_diff = abs((event_start - search_time_utc).total_seconds())
            if time_diff <= 300:  # 5 minutes in seconds
                logger.debug(f"Found matching event with ID {event['id']}")
                service.events().delete(
                    calendarId='primary',
                    eventId=event['id']
                ).execute()
                return True, f"Event at {event_start.astimezone(user_tz).strftime('%I:%M %p')} has been canceled."

        logger.debug("No matching event found")
        return False, "No matching event found to cancel."

    except Exception as e:
        logger.error(f"Error in cancel_event: {e}")
        logger.error(traceback.format_exc())
        return False, f"An error occurred while canceling the event: {str(e)}"


def get_timezone_from_phone(phone_number):
    """Get timezone from phone number or return default"""
    try:
        logger.info(f"Attempting to get timezone for phone number: {phone_number}")
        # Parse the phone number
        parsed_number = phonenumbers.parse(phone_number)
        logger.info(f"Parsed phone number: country_code={parsed_number.country_code}, national_number={parsed_number.national_number}")
        
        # For US numbers, we'll default to Eastern Time
        if parsed_number.country_code == 1:  # US/Canada number
            logger.info("US/Canada number detected, using America/New_York timezone")
            return 'America/New_York'
            
        # For other countries, you can add more specific timezone mappings
        # For now, default to UTC
        logger.info(f"Non-US number detected (country_code={parsed_number.country_code}), using UTC timezone")
        return 'UTC'
        
    except Exception as e:
        logger.error(f"Error getting timezone for {phone_number}: {str(e)}")
        logger.error(traceback.format_exc())
        return 'UTC'  # Default fallback


def get_available_slots(credentials, start_time, end_time, duration_minutes=60):
    service = get_calendar_service(credentials)

    # Ensure start_time and end_time are timezone-aware
    if not start_time.tzinfo:
        raise ValueError("start_time must be timezone-aware")
    if not end_time.tzinfo:
        raise ValueError("end_time must be timezone-aware")

    events_result = service.events().list(
        calendarId='primary',
        timeMin=start_time.isoformat(),
        timeMax=end_time.isoformat(),
        singleEvents=True,
        orderBy='startTime'
    ).execute()
    events = events_result.get('items', [])

    logger.debug(f"Checking availability between {start_time} and {end_time}")
    logger.debug(f"Number of events found: {len(events)}")

    busy_times = []
    for event in events:
        # Parse event times, ensuring they are timezone-aware
        event_start_str = event['start'].get('dateTime', event['start'].get('date'))
        event_end_str = event['end'].get('dateTime', event['end'].get('date'))

        try:
            event_start = dateparser.parse(event_start_str,
                                           settings={'RETURN_AS_TIMEZONE_AWARE': True})
            event_end = dateparser.parse(event_end_str, settings={'RETURN_AS_TIMEZONE_AWARE': True})

            if event_start and event_end:
                busy_times.append((event_start, event_end))
                logger.debug(f"Busy time: {event_start} to {event_end}")
        except Exception as e:
            logger.error(f"Error parsing event time: {e}")

    # If there are no events, the entire time range is available
    if not busy_times:
        logger.debug("No busy times found, slot is available")
        return [start_time]

    # Check if the requested time slot is free
    available_slots = []
    current_slot = start_time
    while current_slot < end_time:
        requested_end_time = current_slot + timedelta(minutes=duration_minutes)

        # Check if this slot conflicts with any busy times
        if all(requested_end_time <= busy_start or current_slot >= busy_end
               for busy_start, busy_end in busy_times):
            available_slots.append(current_slot)

        # Move to next potential slot
        current_slot += timedelta(hours=1)

    logger.debug(f"Available slots: {available_slots}")
    return available_slots


def format_slots_message(slots):
    """
    Format available slots into a readable message
    """
    if not slots:
        return "No available slots found."

    formatted_slots = [slot.strftime("%A, %B %d at %I:%M %p") for slot in slots[:5]]
    return "Available slots:\n" + "\n".join(formatted_slots)


def parse_preferred_time(message, reference_date):
    """
    Parse a preferred time from a message
    """
    try:
        parsed_time = dateparser.parse(message, settings={'RELATIVE_BASE': reference_date})
        if parsed_time:
            return parsed_time.replace(tzinfo=reference_date.tzinfo)
        return None
    except Exception as e:
        logger.error(f"Error parsing preferred time: {e}")
        return None


def schedule_event_reminder(event):
    """Schedule a reminder task for an event"""
    from scheduler.models import ScheduledTask
    import json
    import logging
    from django.utils import timezone
    from datetime import timedelta

    logger = logging.getLogger(__name__)

    # Skip if reminders are disabled
    if not event.user_profile.enable_reminders:
        return None

    # Get reminder minutes (use default if not specified)
    reminder_minutes = event.reminder_minutes
    if reminder_minutes is None:
        reminder_minutes = event.user_profile.default_reminder_minutes

    # Calculate when to send reminder
    reminder_time = event.start_time - timedelta(minutes=reminder_minutes)

    # Don't schedule if reminder time is in the past
    if reminder_time <= timezone.now():
        return None

    # Prepare task data
    task_data = {
        'phone_number': event.user_profile.phone_number,
        'event_summary': event.summary,
        'event_time': event.start_time.isoformat(),
        'location': event.location or '',
        'reminder_minutes': reminder_minutes
    }

    # Create task
    task = ScheduledTask.objects.create(
        task_type='reminder',
        data=json.dumps(task_data),
        scheduled_time=reminder_time,
        status='pending',
        event=event
    )

    logger.info(f"Scheduled reminder for event {event.id} at {reminder_time}")
    return task