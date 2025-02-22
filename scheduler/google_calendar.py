import os
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.discovery_cache.base import Cache
import pickle
import phonenumbers
import logging
import pytz
from datetime import datetime, timedelta
import traceback

import os
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.discovery_cache.base import Cache
import pickle
import phonenumbers
import logging
import pytz
from datetime import datetime, timedelta
import traceback

logger = logging.getLogger(__name__)

SCOPES = ['https://www.googleapis.com/auth/calendar']

# Environment settings
os.environ['GOOGLE_API_PYTHON_CLIENT_ENABLE_FILE_CACHE'] = '0'
os.environ['GOOGLE_API_USE_MTLS_ENDPOINT'] = 'never'
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'  # Only for development


class MemoryCache(Cache):
    """Memory cache implementation for Google API client"""
    _CACHE = {}

    def get(self, url):
        return MemoryCache._CACHE.get(url)

    def set(self, url, content):
        MemoryCache._CACHE[url] = content


def refresh_credentials(credentials):
    """Refresh Google OAuth credentials if expired"""
    try:
        if not credentials.valid:
            if credentials.expired and credentials.refresh_token:
                logger.debug("Refreshing expired credentials")
                credentials.refresh(Request())
                return credentials
            else:
                logger.error("Credentials expired and can't be refreshed")
                return None
        return credentials
    except Exception as e:
        logger.error(f"Error refreshing credentials: {e}")
        return None


def get_calendar_service(credentials):
    """Get Google Calendar service with proper caching"""
    try:
        # Refresh credentials if needed
        credentials = refresh_credentials(credentials)
        if not credentials:
            logger.error("Failed to refresh credentials")
            return None

        # Build service with memory cache
        service = build('calendar', 'v3',
                       credentials=credentials,
                       cache=MemoryCache())
        logger.debug("Successfully created calendar service")
        return service
    except Exception as e:
        logger.error(f"Error building calendar service: {e}")
        logger.error(traceback.format_exc())
        return None


def get_timezone_from_phone(phone_number):
    """[Your existing get_timezone_from_phone function]"""
    # Keep your existing implementation
    pass


def find_event_for_cancellation(service, event_time, user_timezone):
    """Find an event around a specific time"""
    try:
        logger.debug(f"Original event_time: {event_time}, timezone: {user_timezone}")

        # Ensure timezone object
        if isinstance(user_timezone, str):
            user_timezone = pytz.timezone(user_timezone)

        # Convert to timezone-aware datetime
        if not event_time.tzinfo:
            event_time = user_timezone.localize(event_time)
            logger.debug(f"Localized time: {event_time}")
        else:
            event_time = event_time.astimezone(user_timezone)
            logger.debug(f"Converted time: {event_time}")

        # Set to the correct time in UTC
        event_time_utc = event_time.astimezone(pytz.UTC)
        logger.debug(f"UTC time: {event_time_utc}")

        # Create a 10-minute window around the specified time
        time_min = (event_time_utc - timedelta(minutes=5)).isoformat()
        time_max = (event_time_utc + timedelta(minutes=5)).isoformat()

        logger.debug(f"Search window: {time_min} to {time_max}")

        events_result = service.events().list(
            calendarId='primary',
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])
        logger.debug(f"Found {len(events)} events")

        for evt in events:
            logger.debug(f"Found event: {evt.get('summary')} at {evt['start'].get('dateTime')}")

        if events:
            closest_event = min(
                events,
                key=lambda x: abs(
                    datetime.fromisoformat(x['start'].get('dateTime')).replace(
                        tzinfo=pytz.UTC) - event_time_utc
                )
            )
            logger.debug(f"Selected event: {closest_event.get('summary')} at {closest_event['start'].get('dateTime')}")
            return closest_event

        logger.debug("No events found in time window")
        return None

    except Exception as e:
        logger.error(f"Error finding event: {e}")
        logger.error(traceback.format_exc())
        return None


def cancel_event(credentials, event_time, user_timezone):
    """Cancel a calendar event"""
    try:
        # Get calendar service with proper cache handling
        service = get_calendar_service(credentials)
        if not service:
            logger.error("Failed to get calendar service")
            return False

        # Convert timezone to pytz timezone if string
        if isinstance(user_timezone, str):
            user_timezone = pytz.timezone(user_timezone)

        # Find the event
        event = find_event_for_cancellation(service, event_time, user_timezone)
        if not event:
            logger.error(f"No event found to cancel at time: {event_time}")
            return False

        try:
            # Try to delete the event
            service.events().delete(
                calendarId='primary',
                eventId=event['id']
            ).execute()
            logger.info(f"Successfully cancelled event at {event_time}")
            return True
        except Exception as delete_error:
            logger.error(f"Error deleting event: {delete_error}")
            # If credentials expired, try refreshing and deleting again
            if '401' in str(delete_error):
                logger.debug("Attempting to refresh credentials and retry")
                refreshed_credentials = refresh_credentials(credentials)
                if refreshed_credentials:
                    service = get_calendar_service(refreshed_credentials)
                    service.events().delete(
                        calendarId='primary',
                        eventId=event['id']
                    ).execute()
                    return True
            return False

    except Exception as e:
        logger.error(f"Error in cancel_event: {e}")
        logger.error(traceback.format_exc())
        return False

def authenticate_google_calendar():
    """
    Authenticate with Google Calendar API

    Returns:
        credentials: Google OAuth credentials
    """
    try:
        creds = None
        if os.path.exists('token.pickle'):
            with open('token.pickle', 'rb') as token:
                creds = pickle.load(token)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    'credentials.json', SCOPES)
                creds = flow.run_local_server(port=0)

            with open('token.pickle', 'wb') as token:
                pickle.dump(creds, token)

        # Test the credentials by creating a service
        service = get_calendar_service(creds)
        if not service:
            logger.error("Failed to create calendar service")
            return None

        return creds

    except Exception as e:
        logger.error(f"Error in authenticate_google_calendar: {e}")
        return None