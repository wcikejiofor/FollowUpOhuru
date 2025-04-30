import os
import json
import logging
import traceback
from datetime import datetime, timedelta, timezone
from functools import wraps
import re
import dateparser
import phonenumbers
import pytz
import stripe

from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
from twilio.twiml.messaging_response import MessagingResponse
from django.conf import settings
from openai import OpenAI

from .models import UserProfile, SubscriptionPlan, ShortLink
from .stripe_utils import StripeSubscriptionManager
from .subscription_utils import SubscriptionManager

from django.shortcuts import render, redirect
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from .sms_sender import send_sms

from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator

from openai import OpenAI

from .models import UserProfile, GoogleCredentials, Event, SubscriptionPlan
from .sms_utils import SMSScheduler, EventManager
from .subscription_utils import SubscriptionManager
from .url_shortener import URLShortener
from .utils import parse_event_details, parse_modification_details, get_timezone_from_phone, \
    parse_preferred_time

# Configure logging
logger = logging.getLogger(__name__)

# Initialize OpenAI client
client = OpenAI(api_key=settings.OPENAI_API_KEY)

# Environment configurations
os.environ['GOOGLE_API_PYTHON_CLIENT_ENABLE_FILE_CACHE'] = '0'
os.environ['HTTPS_PROXY'] = ''
os.environ['GOOGLE_API_USE_MTLS_ENDPOINT'] = 'never'
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'  # Only for development


@csrf_exempt
def validate_twilio_request(f):
    """Decorator to validate Twilio requests"""
    def decorated_function(request, *args, **kwargs):
        # More permissive validation for debug
        if settings.DEBUG:
            logger.warning("Debug mode: Bypassing Twilio validation")
            return f(request, *args, **kwargs)

        try:
            validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
            request_valid = validator.validate(
                request.build_absolute_uri(),
                request.POST,
                request.META.get('HTTP_X_TWILIO_SIGNATURE', '')
            )

            if request_valid:
                return f(request, *args, **kwargs)

            logger.error("Twilio validation failed")
            return HttpResponseForbidden("Invalid Twilio request")

        except Exception as e:
            logger.error(f"Validation Error: {str(e)}")
            return HttpResponseForbidden(f"Validation error: {str(e)}")

    return decorated_function

@csrf_exempt
@validate_twilio_request
def handle_call(request):
    """Handle incoming voice calls"""
    try:
        response = VoiceResponse()

        if 'RecordingUrl' not in request.POST:
            response.say(
                'Please leave a message with your calendar event or task details after the beep.',
                voice='alice'
            )
            response.record(
                action=f"{settings.BASE_URL}/handle_call/",
                maxLength=30,
                finishOnKey='#',
                timeout=5,
                transcribe=True,
                transcribeCallback=f"{settings.BASE_URL}/handle_transcription/"
            )
        else:
            caller_number = request.POST.get('From', '')
            if caller_number:
                send_sms(caller_number, "Recording received, processing your request...")
            response.say('Thank you, your message has been recorded. Goodbye.', voice='alice')
            response.hangup()

        return HttpResponse(str(response), content_type='text/xml')
    except Exception as e:
        logger.error(f"Call handling error: {str(e)}")
        return HttpResponse(str(VoiceResponse()), content_type='text/xml')


@csrf_exempt
@validate_twilio_request
def handle_transcription(request):
    """Handle voice message transcriptions"""
    phone_number = request.POST.get('From', '')

    try:
        transcription = request.POST.get('TranscriptionText', '')
        recording_url = request.POST.get('RecordingUrl', '')

        logger.debug(f"Transcription received for {phone_number}: {transcription}")

        # Find or create user profile
        user_profile, _ = UserProfile.objects.get_or_create(
            phone_number=phone_number,
            defaults={
                'subscription_plan': SubscriptionPlan.STARTER,
                'subscription_start_date': timezone.now(),
                'is_guest_mode': True
            }
        )

        # Check if user has Google Calendar credentials
        if not user_profile.google_credentials:
            send_sms(phone_number, "Please connect your Google Calendar first by texting 'connect'.")
            return HttpResponse()

        # Parse event details from transcription
        event_details = parse_event_details(transcription, phone_number)

        if event_details and len(event_details) > 0:
            event_manager = EventManager(user_profile)
            if event_manager.create_local_event(event_details, phone_number):
                send_sms(phone_number, f"Event created: {event_details.get('summary', 'Meeting')}")
            else:
                send_sms(phone_number, "Error creating event. Please try again.")
        else:
            send_sms(phone_number, "Could not understand event details. Please try again.")

    except Exception as e:
        logger.error(f"Transcription error: {str(e)}")
        logger.error(traceback.format_exc())
        if phone_number:
            send_sms(phone_number, "Error processing your request. Please try again.")

    return HttpResponse()

@csrf_exempt
@validate_twilio_request
def sms_handler(request):
    """
    Zero-Step Onboarding SMS Handler
    Handles all incoming SMS interactions with minimal friction
    """
    if request.method == 'POST':
        try:
            # Standardize phone number
            original_phone_number = request.POST.get('From', '')
            incoming_msg = request.POST.get('Body', '').lower().strip()
            response = MessagingResponse()

            # Parse and validate phone number
            try:
                import phonenumbers
                parsed_number = phonenumbers.parse(original_phone_number, None)
                formatted_phone_number = phonenumbers.format_number(
                    parsed_number,
                    phonenumbers.PhoneNumberFormat.E164
                )
            except Exception:
                formatted_phone_number = original_phone_number

            # Find or Create User Profile with Zero-Friction Approach
            try:
                # First, try to get the existing user profile
                user_profile = UserProfile.objects.filter(phone_number=formatted_phone_number).first()
            except UserProfile.DoesNotExist:
                # If the user doesn't exist, create a new profile
                user_profile = UserProfile.objects.create(
                    phone_number=formatted_phone_number,
                    subscription_plan=SubscriptionPlan.STARTER,
                    subscription_start_date=timezone.now(),
                    trial_start_date=timezone.now(),
                    # Set default reminder settings for new users
                    default_reminder_minutes=60,
                    enable_reminders=True
                )

                # Conditionally set guest mode if the field exists
                if hasattr(user_profile, 'is_guest_mode'):
                    user_profile.is_guest_mode = True
                if hasattr(user_profile, 'setup_stage'):
                    user_profile.setup_stage = 'new'
                user_profile.save()

                # First-time user welcome
                response.message(
                    "Welcome to FollowUp! 🚀\n\n"
                    "You can start scheduling immediately:\n"
                    "• 'Schedule meeting with John at 3pm'\n"
                    "• 'Book call with Sarah tomorrow'\n\n"
                    "Pro Tip: Text 'connect' to sync with Google Calendar!"
                )
                return HttpResponse(str(response), content_type='application/xml')

            # Check meeting eligibility
            meeting_eligibility = SubscriptionManager.check_meeting_eligibility(user_profile)
            if not meeting_eligibility['eligible']:
                response.message(meeting_eligibility['message'])
                return HttpResponse(str(response), content_type='application/xml')

            # Event Scheduling Keywords
            event_keywords = [
                'schedule', 'book', 'meeting', 'call', 'cancel', 'reschedule',
                'modify', 'move', 'create event', 'delete event', 'update event'
            ]
            is_event_related = any(keyword in incoming_msg for keyword in event_keywords)

            event_manager = EventManager(user_profile)

            # NEW: Reminder Settings Handling
            if 'reminder' in incoming_msg and not is_event_related:
                import re
                if 'set default' in incoming_msg or 'default reminder' in incoming_msg:
                    # Extract time from message using regex or parsing
                    # Example: "set default reminder to 30 minutes"
                    time_match = re.search(r'(\d+)\s*(minute|minutes|min|hour|hours|hr)',
                                           incoming_msg)
                    if time_match:
                        value = int(time_match.group(1))
                        unit = time_match.group(2)

                        # Convert hours to minutes
                        if 'hour' in unit:
                            value *= 60

                        user_profile.default_reminder_minutes = value
                        user_profile.save()

                        # Format for display
                        if value >= 60 and value % 60 == 0:
                            display = f"{value // 60} hour{'s' if value // 60 > 1 else ''}"
                        else:
                            display = f"{value} minutes"

                        response.message(f"Default reminder time set to {display} before events.")
                    else:
                        response.message("Please specify a time, like '30 minutes' or '1 hour'.")

                elif 'off' in incoming_msg or 'disable' in incoming_msg:
                    user_profile.enable_reminders = False
                    user_profile.save()
                    response.message("Event reminders have been turned off.")

                elif 'on' in incoming_msg or 'enable' in incoming_msg:
                    user_profile.enable_reminders = True
                    user_profile.save()
                    response.message("Event reminders have been turned on.")

                else:
                    response.message(
                        "Reminder commands:\n"
                        "• 'set default reminder to 30 minutes'\n"
                        "• 'reminders off'\n"
                        "• 'reminders on'"
                    )
                return HttpResponse(str(response), content_type='application/xml')

            # Event Handling
            elif is_event_related:
                # Parse for reminder settings in event creation
                reminder_match = None
                if 'remind' in incoming_msg:
                    import re
                    logger.debug(f"Processing message with reminder: {incoming_msg}")

                    reminder_match = re.search(
                        r'remind me (\d+)\s*(minute|minutes|min|hour|hours|hr)', incoming_msg)

                    if reminder_match:
                        logger.debug(f"Found reminder match: {reminder_match.group(0)}")
                        logger.debug(
                            f"Minutes: {reminder_match.group(1)}, Unit: {reminder_match.group(2)}")

                # Smart Event Handling (Scheduling, Modification, Cancellation)
                if 'cancel' in incoming_msg:
                    action, event_details = parse_event_details(incoming_msg,
                                                                formatted_phone_number)
                    success, message = event_manager.cancel_event(event_details,
                                                                  formatted_phone_number)
                    response.message(message)
                elif 'move' in incoming_msg or 'reschedule' in incoming_msg:
                    modification_details = parse_modification_details(incoming_msg,
                                                                      formatted_phone_number)
                    if modification_details:
                        success = event_manager.modify_event(modification_details,
                                                             formatted_phone_number)
                        if not success:
                            response.message(
                                "I couldn't find that meeting. Please check the time and try again. "
                                "For example: 'Move my 3 PM meeting to 4 PM'"
                            )
                    else:
                        response.message(
                            "I couldn't understand those details. "
                            "Please try something like: 'Move my 3 PM meeting to 4 PM'"
                        )
                else:
                    # Default scheduling
                    action, event_details = parse_event_details(incoming_msg,
                                                                formatted_phone_number)
                    if event_details:
                        # Add reminder details if found
                        if reminder_match:
                            value = int(reminder_match.group(1))
                            unit = reminder_match.group(2)

                            # Convert hours to minutes
                            if 'hour' in unit:
                                value *= 60

                            event_details['reminder_minutes'] = value
                            logger.debug(f"Added reminder_minutes to event_details: {value}")
                        elif user_profile.enable_reminders:
                            # Use default reminder minutes if reminders are enabled and no explicit reminder was set
                            event_details[
                                'reminder_minutes'] = user_profile.default_reminder_minutes
                            logger.debug(
                                f"Using default reminder_minutes: {user_profile.default_reminder_minutes}")

                        # Schedule the event
                        success, message = event_manager.schedule_smart_event(
                            event_details,
                            event_details.get('start_time'),
                            formatted_phone_number
                        )

                        # Add reminder info to the response message if it was scheduled with a reminder
                        if success and user_profile.enable_reminders and 'reminder_minutes' in event_details:
                            # Only add reminder info if not already in the message
                            if "reminder" not in message.lower():
                                minutes = event_details['reminder_minutes']
                                if minutes >= 60 and minutes % 60 == 0:
                                    time_display = f"{minutes // 60} hour"
                                    if minutes // 60 > 1:
                                        time_display += "s"
                                else:
                                    time_display = f"{minutes} minute"
                                    if minutes > 1:
                                        time_display += "s"
                                message += f" You'll receive a reminder {time_display} before."

                        response.message(message)
                    else:
                        response.message(
                            "I couldn't understand those event details. Please try again.")

            # View Events
            elif any(keyword in incoming_msg.lower() for keyword in ['meetings', 'events']):
                user_tz = pytz.timezone(get_timezone_from_phone(formatted_phone_number))

                try:
                    # Use OpenAI to extract date reference
                    response_openai = client.chat.completions.create(
                        model="gpt-3.5-turbo",
                        messages=[
                            {
                                "role": "system",
                                "content": "Extract the date reference from the user's message about events."
                            },
                            {"role": "user", "content": incoming_msg}
                        ],
                        response_format={"type": "json_object"}
                    )

                    parsed_data = json.loads(response_openai.choices[0].message.content)
                    date_reference = parsed_data.get('date', 'upcoming')

                    return view_events(user_profile, date_reference, user_tz, response)

                except Exception as e:
                    logger.error(f"Error parsing event date: {e}")
                    # Fallback to upcoming events
                    return view_events(user_profile, 'upcoming', user_tz, response)

            # Google Calendar Connection
            elif incoming_msg in ['connect', 'sync', 'calendar']:
                auth_link = f"{settings.BASE_URL}/authorize/{user_profile.id}/"
                short_link = URLShortener.generate_short_link(auth_link)

                response.message(
                    "Connect your Google Calendar:\n"
                    f"{short_link}\n\n"
                    "✅ Existing events will automatically sync\n"
                    "🔒 Secure & Optional"
                )

            # Help Command
            elif incoming_msg in ['help', '?', 'info']:
                response.message(
                    "FollowUp Quickstart Guide 📅\n\n"
                    "• Schedule Events: 'Meet John at 3pm'\n"
                    "• Modify Events: 'Move meeting to 4pm'\n"
                    "• Cancel Events: 'Cancel my meeting'\n"
                    "• View Events: 'my events'\n"
                    "• Reminders: 'set default reminder to 30 minutes'\n"
                    "• Connect Calendar: Text 'connect'\n"
                    "• Signup/Plans: 'signup', 'my plan'"
                )

            # Subscription-Related Commands
            elif incoming_msg.startswith('signup'):
                if 'starter' in incoming_msg:
                    return handle_starter_signup(user_profile, response)
                else:
                    response.message(
                        "Currently, only the Starter Plan is available.\n"
                        "Text 'signup starter' to get started."
                    )
            elif incoming_msg in ['plan', 'subscription', 'my plan']:
                return show_plan_details(user_profile, response)

            # Default Catch-All for Non-Specific Messages
            else:
                try:
                    # AI Response for Starter Plan
                    if user_profile.subscription_plan.lower() in ['starter']:
                        ai_response = generate_ai_response(incoming_msg)
                        response.message(ai_response)
                    else:
                        response.message(
                            "AI Chat is available on the Starter Plan. "
                            "Text 'signup starter' to unlock AI assistance!"
                        )
                except Exception as e:
                    logger.error(f"AI Response generation error: {e}")
                    response.message(
                        "I'm not sure how to help with that. "
                        "Text 'help' to see what I can do!"
                    )

            return HttpResponse(str(response), content_type='application/xml')

        except Exception as e:
            logger.error(f"SMS Handler Critical Error: {e}")
            logger.error(traceback.format_exc())

            response.message(
                "Oops! Something went wrong. "
                "Please try again or text 'help' for assistance."
            )
            return HttpResponse(str(response), content_type='application/xml')

    return HttpResponse("Method not allowed", status=405)

def generate_ai_response(message):
    """Generate AI response for non-event messages"""
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful SMS assistant. Keep responses concise and practical."
                },
                {"role": "user", "content": message}
            ],
            max_tokens=300
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"AI response generation error: {e}")
        return "Sorry, I couldn't generate a response right now."

@csrf_exempt
def no_plan_welcome(response):
    """Welcome message for users without an active subscription plan"""
    response.message(
        "Welcome to FollowUp! This app helps you manage your calendar using simple text messages."
    )
    response.message(
        "Starter Plan: $5/month\n"
        "• Unlimited meetings/month\n"
        "• SMS-based scheduling\n"
        "• 7-day free trial if you use the Coupon CODE\n\n"
        "Text 'signup starter' to get started."

    )
    return HttpResponse(str(response), content_type='application/xml')


@csrf_exempt
def handle_starter_signup(user_profile, response):
    """Handle Starter plan signup"""
    try:
        # Generate Stripe Checkout URL
        checkout_url = StripeSubscriptionManager.create_subscription_checkout(
            user_profile,
            'starter'
        )

        if checkout_url:
            # Shorten the checkout URL
            from .url_shortener import URLShortener
            short_checkout_url = URLShortener.generate_short_link(checkout_url)

            logger.debug(f"Shortened checkout URL: {short_checkout_url}")

            # Send shortened checkout URL via SMS
            StripeSubscriptionManager.send_checkout_link_via_sms(
                user_profile.phone_number,
                short_checkout_url
            )

            response.message(
                "Checkout link sent via SMS! "
                "Complete your Starter Plan subscription:\n"
                "✅ 30 meetings/month\n"
                "✅ Basic SMS scheduling\n"
                "Monthly cost: $5"
            )
        else:
            response.message("Error generating checkout link. Please try again.")

        return HttpResponse(str(response), content_type='application/xml')
    except Exception as e:
        logger.error(f"Starter plan signup error: {e}")
        response.message("Error signing up for Starter plan. Please try again.")
        return HttpResponse(str(response), content_type='application/xml')


@csrf_exempt
def handle_business_signup(user_profile, response):
    try:
        # Generate Stripe Checkout URL
        checkout_url = StripeSubscriptionManager.create_subscription_checkout(
            user_profile,
            'business'
        )

        if checkout_url:
            # Shorten the checkout URL
            from .url_shortener import URLShortener
            short_checkout_url = URLShortener.generate_short_link(checkout_url)

            logger.debug(f"Shortened checkout URL: {short_checkout_url}")

            # Send shortened checkout URL via SMS
            StripeSubscriptionManager.send_checkout_link_via_sms(
                user_profile.phone_number,
                short_checkout_url
            )

            response.message(
                "Checkout link sent via SMS! "
                "Complete your Business Plan subscription:\n"
                "✅ Unlimited meetings\n"
                "✅ Assistant Mode\n"
                "Monthly cost: $20"
            )
        else:
            response.message("Error generating checkout link. Please try again.")

        return HttpResponse(str(response), content_type='application/xml')
    except Exception as e:
        logger.error(f"Business plan signup error: {e}", exc_info=True)
        response.message("Error initiating signup. Please try again.")
        return HttpResponse(str(response), content_type='application/xml')


@csrf_exempt
def show_plan_details(user_profile, response):
    """Show details of the user's current plan"""
    features = user_profile.get_plan_features()

    # Construct plan details message
    if user_profile.is_trial_active():
        trial_remaining = 7 - (timezone.now() - user_profile.trial_start_date).days
        plan_message = f"🌟 Free Trial Active (Ends in {trial_remaining} days)\n\n"
    else:
        plan_message = ""

    plan_message += (
        f"Current Plan: {features['name']}\n"
        f"Price: ${features['price']}/month\n"
        f"Max Meetings: {'Unlimited' if features['max_meetings'] == float('inf') else features['max_meetings']}\n"
        f"SMS Scheduling: {'Yes' if features['sms_scheduling'] else 'No'}\n"
        f"Priority Support: {'Yes' if features.get('priority', False) else 'No'}\n"
    )

    response.message(plan_message)
    return HttpResponse(str(response), content_type='application/xml')


@csrf_exempt
def handle_free_signup(user_profile, response):
    """Process free plan signup"""
    user_profile.subscription_plan = 'free'
    user_profile.save()
    response.message(
        "Great, you're signed up for the free plan! "
        "To start using FollowUp, here's how it works:\n\n"
        "1. Create a new event by texting the app with details like 'Meet with John at 2pm on Tuesday'.\n"
        "2. You can also view your upcoming events by texting 'view events'.\n"
        "3. Want to change plans? Text 'change plan' anytime.\n\n"
        "Let me know if you have any other questions!"
    )
    return HttpResponse(str(response), content_type='application/xml')


@csrf_exempt
def handle_pro_signup(user_profile, response):
    try:
        logger.debug(f"Starting Pro signup for user profile: {user_profile.id}")

        # Log user profile details
        logger.debug(f"User Profile Details:")
        logger.debug(f"Phone Number: {user_profile.phone_number}")
        logger.debug(f"Current Subscription Plan: {user_profile.subscription_plan}")

        # Generate Stripe Checkout URL
        checkout_url = StripeSubscriptionManager.create_subscription_checkout(
            user_profile,
            'pro',
            phone_number=user_profile.phone_number  # Pass phone number
        )

        logger.debug(f"Checkout URL generated: {checkout_url}")

        if checkout_url:
            # Send checkout URL via SMS
            sms_result = StripeSubscriptionManager.send_checkout_link_via_sms(
                user_profile.phone_number,
                checkout_url
            )

            logger.debug(f"SMS sending result: {sms_result}")

            response.message(
                "Checkout link sent via SMS! "
                "Complete your Pro Plan subscription:\n"
                "✅ Unlimited meetings\n"
                "✅ Priority SMS\n"
                "Monthly cost: $10"
            )
        else:
            logger.error("Failed to generate checkout URL")
            response.message("Error generating checkout link. Please try again.")

        return HttpResponse(str(response), content_type='application/xml')
    except Exception as e:
        logger.error(f"Pro plan signup error: {e}", exc_info=True)
        response.message("Error initiating signup. Please try again.")
        return HttpResponse(str(response), content_type='application/xml')


@csrf_exempt
def view_events(user_profile, date_reference, user_tz, response):
    """View events for a user based on flexible date parsing"""
    try:
        # Parse the date flexibly
        if date_reference.lower() in ['today', 'upcoming']:
            # For 'today' or 'upcoming', use current time as start
            start_date = datetime.now(user_tz)
            events = Event.objects.filter(
                user_profile=user_profile,
                start_time__gte=start_date
            ).order_by('start_time')[:5]
            date_description = "Upcoming"
        else:
            # Use dateparser for flexible date parsing
            parsed_date = dateparser.parse(
                date_reference,
                settings={
                    'TIMEZONE': str(user_tz),
                    'RELATIVE_BASE': datetime.now(user_tz),
                    'PREFER_DATES_FROM': 'future'
                }
            )

            # If parsing fails, return error
            if not parsed_date:
                response.message(
                    "Sorry, I couldn't understand the date. "
                    "Try formats like 'tomorrow', 'next week', '12/25', etc."
                )
                return HttpResponse(str(response), content_type='application/xml')

            # Normalize to start and end of the day in user's timezone
            start_date = user_tz.localize(datetime.combine(parsed_date.date(), datetime.min.time()))
            end_date = user_tz.localize(datetime.combine(parsed_date.date(), datetime.max.time()))

            events = Event.objects.filter(
                user_profile=user_profile,
                start_time__gte=start_date,
                start_time__lte=end_date
            ).order_by('start_time')

            # Format date description
            if parsed_date.date() == datetime.now(user_tz).date():
                date_description = "Today"
            elif parsed_date.date() == (datetime.now(user_tz) + timedelta(days=1)).date():
                date_description = "Tomorrow"
            else:
                date_description = parsed_date.strftime("%B %d")

        if events:
            event_list = "\n".join([
                f"- {e.summary} at {e.start_time.astimezone(user_tz).strftime('%I:%M %p').lstrip('0')}"
                for e in events
            ])
            response.message(f"{date_description}'s Events:\n{event_list}")
        else:
            response.message(f"No events scheduled for {date_description}.")

        return HttpResponse(str(response), content_type='application/xml')

    except Exception as e:
        logger.error(f"Error viewing events: {e}")
        response.message("Sorry, there was an error retrieving your events.")
        return HttpResponse(str(response), content_type='application/xml')


@csrf_exempt
def authorize_google(request, user_id):
    """Handle Google OAuth authorization"""
    try:
        import os

        # Extensive logging for debugging
        logger.error(f"Starting Google OAuth authorization for user {user_id}")

        # Log environment variables and file paths
        logger.error(f"BASE_DIR: {settings.BASE_DIR}")
        logger.error(f"GOOGLE_CLIENT_SECRETS_FILE: {settings.GOOGLE_CLIENT_SECRETS_FILE}")

        # Check if the file exists and is readable
        if not settings.GOOGLE_CLIENT_SECRETS_FILE:
            logger.error("No Google Client Secrets file path defined!")
            return HttpResponse("No client secrets file configured", status=500)

        # Additional file existence and permission check
        if not os.path.exists(settings.GOOGLE_CLIENT_SECRETS_FILE):
            logger.error(
                f"Client secrets file does not exist at: {settings.GOOGLE_CLIENT_SECRETS_FILE}")

            # Optional: Check environment variable contents
            client_secrets_env = os.environ.get('GOOGLE_CLIENT_SECRETS')
            if client_secrets_env:
                logger.error("GOOGLE_CLIENT_SECRETS environment variable is set")
                logger.error(f"First 200 chars of env var: {client_secrets_env[:200]}")
            else:
                logger.error("GOOGLE_CLIENT_SECRETS environment variable is NOT set")

            return HttpResponse(
                f"Client secrets file not found at {settings.GOOGLE_CLIENT_SECRETS_FILE}",
                status=500)

        # Try to read the file to verify its contents
        try:
            with open(settings.GOOGLE_CLIENT_SECRETS_FILE, 'r') as f:
                file_contents = f.read()
                logger.error(
                    f"Client secrets file contents (first 200 chars): {file_contents[:200]}")
        except Exception as read_error:
            logger.error(f"Error reading client secrets file: {read_error}")
            return HttpResponse(f"Error reading client secrets: {read_error}", status=500)

        # Retrieve user profile
        try:
            user_profile = UserProfile.objects.get(id=user_id)
            logger.debug(f"User Profile found: {user_profile.phone_number}")
        except UserProfile.DoesNotExist:
            logger.error(f"No user profile found for ID: {user_id}")
            return HttpResponse(f"User profile not found for ID {user_id}", status=404)

        # Create OAuth flow
        flow = Flow.from_client_secrets_file(
            settings.GOOGLE_CLIENT_SECRETS_FILE,
            scopes=[
                'https://www.googleapis.com/auth/calendar.events',
                'https://www.googleapis.com/auth/calendar.readonly'
            ],
            redirect_uri=f"{settings.BASE_URL}/oauth2callback/",
            state=str(user_id)
        )
        # Generate authorization URL
        authorization_url, state = flow.authorization_url(
            access_type='offline',
            prompt='consent'
        )

        logger.debug(f"Authorization URL generated: {authorization_url}")

        return redirect(authorization_url)

    except Exception as e:
        # Comprehensive error logging
        logger.error(f"Full Authorization error: {e}", exc_info=True)

        # Attempt to send error via SMS if possible
        try:
            user_profile = UserProfile.objects.get(id=user_id)
            send_sms(user_profile.phone_number,
                     f"Authentication setup failed. Please contact support.")
        except:
            pass

        return HttpResponse(f"Authentication error: {e}", status=500)



def ivr_menu(request):
    """Handle IVR menu for voice calls"""
    response = VoiceResponse()
    with response.gather(num_digits=1, action='/handle-key', method='POST') as gather:
        gather.say('For sales, press 1. For support, press 2.')
    return HttpResponse(str(response), content_type='text/xml')


@csrf_exempt
def check_credentials(request):
    """Debug endpoint to check stored credentials"""
    creds = GoogleCredentials.objects.all()
    if creds.exists():
        cred = creds.first()
        return HttpResponse(
            f"Number of credential records: {creds.count()}\n"
            f"First credential ID: {cred.id}\n"
            f"Has token: {'Yes' if cred.token else 'No'}\n"
            f"Has refresh_token: {'Yes' if cred.refresh_token else 'No'}"
        )
    else:
        return HttpResponse("No credentials found in database")


@csrf_exempt
def voicemail(request):
    """Handle voicemail recording"""
    response = VoiceResponse()
    response.say('Please leave a message after the beep.')
    response.record(transcribe=True, transcribe_callback='/handle-transcription')
    return HttpResponse(str(response), content_type='text/xml')


@csrf_exempt
def check_session(request):
    """Debug endpoint to check session data"""
    credentials = request.session.get('credentials', 'No credentials found')
    return HttpResponse(f"Session contents: {credentials}")


@csrf_exempt
def get_credentials():
    """Utility function to get credentials for a user"""
    from .models import UserProfile
    user_id = '1'  # Replace with actual user ID
    user_profile = UserProfile.objects.get(id=user_id)
    return Credentials(
        token=user_profile.google_credentials.token,
        refresh_token=user_profile.google_credentials.refresh_token,
        token_uri=user_profile.google_credentials.token_uri,
        client_id=user_profile.google_credentials.client_id,
        client_secret=user_profile.google_credentials.client_secret,
        scopes=user_profile.google_credentials.scopes.split(',')
    )


@csrf_exempt
def answer_call(request):
    """Handle basic incoming calls"""
    response = VoiceResponse()
    response.say('Hello! You have reached the Django and Twilio voice application.', voice='alice')
    return HttpResponse(str(response), content_type='text/xml')


@csrf_exempt
def credentials_to_dict(credentials):
    """Convert Google credentials object to dictionary"""
    return {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes,
    }


@csrf_exempt
def oauth2callback(request):
    """Handle Google OAuth callback"""
    user_id = None
    try:
        import os
        import logging
        import traceback
        import json
        import tempfile

        logger = logging.getLogger(__name__)

        # Log detailed debugging information
        logger.error(f"Full request GET parameters: {dict(request.GET)}")

        # Get client secrets from environment variable
        google_secrets = os.environ.get('GOOGLE_SECRETS')

        if not google_secrets:
            logger.error("GOOGLE_SECRETS environment variable is NOT set")
            return HttpResponse("Google OAuth configuration error", status=500)

        # Create temporary file with client secrets
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as temp_file:
            temp_file.write(google_secrets)
            credentials_path = temp_file.name

        logger.error(f"Created temporary credentials file: {credentials_path}")

        # Extract user ID from state parameter
        user_id = request.GET.get('state')

        if not user_id:
            logger.error("No user ID found in OAuth callback")
            return HttpResponse("Invalid OAuth callback: Missing user ID", status=400)

        # Retrieve user profile
        try:
            user_profile = UserProfile.objects.get(id=user_id)
        except UserProfile.DoesNotExist:
            logger.error(f"No user profile found for ID: {user_id}")
            return HttpResponse(f"User profile not found for ID {user_id}", status=404)

        # Create OAuth flow
        flow = Flow.from_client_secrets_file(
            credentials_path,
            scopes=[
                'https://www.googleapis.com/auth/calendar.events',
                'https://www.googleapis.com/auth/calendar.readonly'
            ],
            redirect_uri='https://checkout.chiresearchai.com/oauth2callback/',  # Use settings variable
            state=user_id
        )

        # Fetch the token
        flow.fetch_token(authorization_response=request.build_absolute_uri())
        credentials = flow.credentials

        # Validate credentials
        if not credentials or not credentials.valid:
            logger.error("Invalid or expired credentials")
            return HttpResponse("Failed to obtain valid credentials", status=500)

        # Prepare credentials dictionary
        creds_dict = {
            'token': credentials.token,
            'refresh_token': credentials.refresh_token,
            'token_uri': credentials.token_uri,
            'client_id': credentials.client_id,
            'client_secret': credentials.client_secret,
            'scopes': list(credentials.scopes)
        }

        # Store credentials and upgrade from guest mode
        user_profile.google_credentials = json.dumps(creds_dict)
        user_profile.is_guest_mode = False  # Upgrade from guest mode
        user_profile.save()

        # Check and sync pending events
        pending_events = Event.objects.filter(
            user_profile=user_profile,
            needs_sync=True
        )

        if pending_events.exists():
            # Background task to sync events
            try:
                for event in pending_events:
                    # Implement Google Calendar sync logic here
                    # This would involve using the credentials to create events in Google Calendar
                    event.needs_sync = False
                    event.save()

                # Notify user about sync
                send_sms(
                    user_profile.phone_number,
                    f"🔄 {pending_events.count()} events synced to Google Calendar!"
                )
            except Exception as sync_error:
                logger.error(f"Event sync error: {sync_error}")

        # Clean up temporary file
        try:
            os.unlink(credentials_path)
        except Exception as cleanup_error:
            logger.error(f"Error cleaning up temporary credentials file: {cleanup_error}")

        # Send SMS with success message
        send_sms(
            user_profile.phone_number,
            "Google Calendar successfully connected! 🎉\n\n"
            "You can now:\n"
            "• Schedule events via SMS\n"
            "• Automatically sync to Google Calendar\n"
            "Enjoy FollowUp!"
        )

        # Redirect or return success response
        return HttpResponse(
            'Successfully connected! You can now use SMS to manage your calendar.'
        )

    except Exception as e:
        # Comprehensive error logging
        logger.error(f"Full OAuth callback error: {str(e)}")
        logger.error(traceback.format_exc())

        # Attempt to send error SMS
        try:
            if user_id:
                user_profile = UserProfile.objects.get(id=user_id)
                send_sms(
                    user_profile.phone_number,
                    "Google Calendar authentication failed. Please try again or contact support."
                )
        except:
            pass

        return HttpResponse(f'Error in oauth2callback: {str(e)}', status=500)

@csrf_exempt
@require_POST
def stripe_webhook(request):
    logger.info("Received a webhook request from Stripe")
    payload = request.body
    sig_header = request.META['HTTP_STRIPE_SIGNATURE']

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
        logger.info(f"Successfully constructed Stripe event: {event['type']}")
    except ValueError as e:
        logger.error(f"Invalid payload: {str(e)}")
        return HttpResponse(status=400)
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Invalid signature: {str(e)}")
        return HttpResponse(status=400)

    logger.info(f"Processing Stripe webhook event: {event['type']}")

    if event['type'] == 'checkout.session.completed':
        logger.info("Handling checkout.session.completed event")
        handle_checkout_session_completed(event)
    elif event['type'] == 'invoice.payment_succeeded':
        logger.info("Handling invoice.payment_succeeded event")
        handle_payment_succeeded(event)
    elif event['type'] == 'customer.subscription.created':
        logger.info("Handling customer.subscription.created event")
        handle_subscription_created(event)
    elif event['type'] == 'customer.subscription.updated':
        logger.info("Handling customer.subscription.updated event")
        handle_subscription_updated(event)
    else:
        logger.info(f"Unhandled event type: {event['type']}")

    return HttpResponse(status=200)


@csrf_exempt
def handle_checkout_session_completed(event):
    session = event['data']['object']
    customer_id = session.get('customer')
    user_id = session.get('client_reference_id')


    logger.info(f"Checkout completed for user {user_id}")

    try:
        user_profile = UserProfile.objects.get(id=user_id)

        # Update user's subscription details
        plan = session['metadata'].get('plan')
        if plan:
            user_profile.subscription_plan = plan
            user_profile.stripe_customer_id = customer_id
            user_profile.subscription_start_date = timezone.now()
            user_profile.save()

        # Generate authentication link
        auth_link = f"{settings.BASE_URL}/authorize/{user_profile.id}/"

        # Send onboarding messages
        messages = [
            f"Welcome to FollowUp! Your {plan.capitalize()} plan is now active.",
            f"To start using the service, please authenticate your Google Calendar: {auth_link}",
            "To schedule a meeting, simply text 'Schedule meeting with [name] at [time]'.",
            "Enjoy using FollowUp! We're here if you need anything."
        ]

        for message in messages:
            send_sms(user_profile.phone_number, message)

        logger.info(f"Onboarding messages sent to user {user_id}")

    except UserProfile.DoesNotExist:
        logger.error(f"User profile not found for user {user_id}")
    except Exception as e:
        logger.error(f"Error starting onboarding for user {user_id}: {str(e)}")


@csrf_exempt
def handle_payment_succeeded(event):
    invoice = event['data']['object']
    customer_id = invoice['customer']

    try:
        # Retrieve the customer from Stripe
        customer = stripe.Customer.retrieve(customer_id)

        # Log all customer details for debugging
        logger.debug(f"Stripe Customer Details: {customer}")
        logger.debug(f"Customer Phone: {customer.phone}")
        logger.debug(f"Customer Metadata: {customer.metadata}")

        # Get phone number from customer metadata or phone field
        user_phone_number = (
                customer.metadata.get('phone_number') or
                customer.phone
        )

        # Find user profile by phone number
        user_profile = UserProfile.objects.filter(phone_number=user_phone_number).first()

        if not user_profile:
            logger.error(f"No UserProfile found for phone number {user_phone_number}")
            return

        # Update Stripe customer ID
        user_profile.stripe_customer_id = customer_id
        user_profile.save()

        # Generate authentication link
        auth_link = f"{settings.BASE_URL}/authorize/{user_profile.id}/"

        # Send SMS
        message = (
            f"Thank you for your payment! To complete setup, please authenticate your Google "
            f"Calendar. "
            "To get started, connect your Google Calendar securely. "
            "You may see a Google warning since we're still in beta—totally normal and safe. "
            "We never store or share your calendar info. "
            "Just click 'Advanced' → 'Continue' if prompted. "
            f"Connect here: {auth_link}"
        )
        send_sms(user_phone_number, message)

    except Exception as e:
        logger.error(f"Error in handle_payment_succeeded: {e}")
        logger.error(traceback.format_exc())


@csrf_exempt
def handle_subscription_created(event):
    subscription = event['data']['object']
    customer_id = subscription['customer']

    try:
        # Retrieve the customer from Stripe
        customer = stripe.Customer.retrieve(customer_id)

        # Log all customer details for debugging
        logger.debug(f"Stripe Customer Details: {customer}")
        logger.debug(f"Customer Phone: {customer.phone}")
        logger.debug(f"Customer Metadata: {customer.metadata}")

        # Get phone number from customer metadata or phone field
        user_phone_number = (
                customer.metadata.get('phone_number') or
                customer.phone
        )

        # If no phone number found, check the user profile
        if not user_phone_number:
            user_profile = UserProfile.objects.filter(stripe_customer_id=customer_id).first()
            if user_profile:
                user_phone_number = user_profile.phone_number

        # If still no phone number found, log and return
        if not user_phone_number:
            logger.warning(f"No phone number found for customer {customer_id}")
            return

        # Get or create the UserProfile
        user_profile, created = UserProfile.objects.get_or_create(
            stripe_customer_id=customer_id,
            defaults={
                'phone_number': user_phone_number,
                'subscription_plan': get_plan_name(subscription['items']['data'][0]['price']['id']),
                'subscription_start_date': timezone.now()
            }
        )

        # Always update the profile
        user_profile.phone_number = user_phone_number
        user_profile.subscription_plan = get_plan_name(
            subscription['items']['data'][0]['price']['id'])
        user_profile.subscription_start_date = timezone.now()
        user_profile.save()

        # Send welcome message
        message = f"Welcome to FollowUp! You've successfully signed up for the {user_profile.subscription_plan} plan."
        send_sms(user_phone_number, message)

    except Exception as e:
        logger.error(f"Error in handle_subscription_created: {e}")
        logger.error(traceback.format_exc())


@csrf_exempt
def handle_subscription_updated(event):
    subscription = event['data']['object']
    customer_id = subscription['customer']

    user_profile = UserProfile.objects.filter(stripe_customer_id=customer_id).first()
    if not user_profile:
        logger.error(f"No UserProfile found for customer {customer_id}")
        return

    # Update subscription information
    plan_id = subscription['items']['data'][0]['price']['id']
    plan_name = get_plan_name(plan_id)
    user_profile.subscription_plan = plan_name
    user_profile.save()

    message = f"Your FollowUp subscription has been updated to the {plan_name} plan."
    send_sms(user_profile.phone_number, message)


@csrf_exempt
def get_plan_name(plan_id):
    # Implement this to return the human-readable plan name based on the Stripe price ID
    # You might want to store this mapping in your database or settings
    plan_mapping = {
        'price_1Qs3GsBMW3FJGGMOUtveta1W': 'Starter',
        'price_1Qs3JYBMW3FJGGMOOmKxi2K2': 'Pro',
        'price_1QwpCGBMW3FJGGMOk2wiHbDs': 'Business',
    }
    return plan_mapping.get(plan_id, 'Unknown Plan')


@csrf_exempt
def redirect_short_link(request, short_code):
    try:
        # Log the incoming short code
        logger.error(f"DEBUG: Request headers: {dict(request.headers)}")
        logger.error(f"DEBUG: Host in META: {request.META.get('HTTP_HOST')}")
        logger.error(f"DEBUG: X-Forwarded-Host: {request.META.get('HTTP_X_FORWARDED_HOST')}")
        logger.error(f"DEBUG: Current ALLOWED_HOSTS: {settings.ALLOWED_HOSTS}")

        # Find the ShortLink in the database
        try:
            short_link = ShortLink.objects.get(short_code=short_code)

            # Log the details of the found short link
            logger.debug(f"Short link found:")
            logger.debug(f"Original URL: {short_link.original_url}")
            logger.debug(f"Created at: {short_link.created_at}")

            # Optional: Add expiration logic (e.g., links expire after 24 hours)
            if short_link.created_at < timezone.now() - timedelta(hours=24):
                logger.warning(f"Short link {short_code} has expired")
                return HttpResponse("Link expired", status=410)

            # Explicitly log the redirect
            logger.info(f"Redirecting to: {short_link.original_url}")

            # Use HttpResponseRedirect for explicit redirection
            from django.http import HttpResponseRedirect
            return HttpResponseRedirect(short_link.original_url)

        except ShortLink.DoesNotExist:
            logger.error(f"No short link found for code: {short_code}")
            return HttpResponse("Link not found", status=404)

    except Exception as e:
        # Catch and log any unexpected errors
        logger.error(f"Unexpected error in redirect_short_link: {e}")
        logger.error(traceback.format_exc())
        return HttpResponse("An error occurred", status=500)
