import logging
from datetime import timedelta
from django.utils import timezone
from django.conf import settings
import traceback

logger = logging.getLogger(__name__)


class ReminderDebugger:
    @staticmethod
    def comprehensive_reminder_check():
        """
        Detailed diagnostic check for reminder system
        """
        from scheduler.models import UserProfile, Event

        # Logging setup
        logger.info("🔍 Starting Comprehensive Reminder System Diagnostic")

        # Check reminder-enabled user profiles
        reminder_enabled_profiles = UserProfile.objects.filter(enable_reminders=True)
        logger.info(f"Total Users with Reminders Enabled: {reminder_enabled_profiles.count()}")

        # Analyze upcoming events with potential reminders
        now = timezone.now()
        upcoming_events = Event.objects.filter(
            user_profile__in=reminder_enabled_profiles,
            start_time__gt=now,
            reminder_minutes__isnull=False,
            reminder_sent=False
        ).order_by('start_time')

        logger.info(f"Total Upcoming Events with Potential Reminders: {upcoming_events.count()}")

        # Detailed event analysis
        for event in upcoming_events:
            try:
                # Calculate reminder time
                reminder_time = event.start_time - timedelta(minutes=event.reminder_minutes)

                # Logging event details
                logger.info("Event Reminder Analysis:")
                logger.info(f"Event: {event.summary}")
                logger.info(f"User Phone: {event.user_profile.phone_number}")
                logger.info(f"Event Start Time: {event.start_time}")
                logger.info(f"Reminder Minutes: {event.reminder_minutes}")
                logger.info(f"Calculated Reminder Time: {reminder_time}")
                logger.info(f"Current Time: {now}")

                # Check reminder eligibility
                if reminder_time <= now:
                    logger.warning(f"REMINDER ELIGIBLE: {event.summary}")
                    ReminderDebugger.attempt_reminder_send(event)
                else:
                    logger.info("Not yet time for reminder")

                logger.info("-" * 50)

            except Exception as e:
                logger.error(f"Error processing event {event.id}: {str(e)}")
                logger.error(traceback.format_exc())

        logger.info("🏁 Reminder System Diagnostic Complete")

    @staticmethod
    def attempt_reminder_send(event):
        """
        Attempt to send a reminder for a specific event
        """
        from scheduler.sms_sender import send_sms  # Adjust import as needed

        try:
            # Construct reminder message
            reminder_message = (
                f"Reminder: {event.summary} is coming up in "
                f"{event.reminder_minutes} minutes"
            )

            # Send SMS
            send_sms(event.user_profile.phone_number, reminder_message)

            # Mark event as reminder sent
            event.reminder_sent = True
            event.save()

            logger.info(f"Reminder sent successfully for event: {event.summary}")

        except Exception as e:
            logger.error(f"Failed to send reminder for event {event.id}: {str(e)}")
            logger.error(traceback.format_exc())

    @staticmethod
    def debug_user_reminders(phone_number):
        """
        Detailed debugging for a specific user's reminders
        """
        from scheduler.models import UserProfile, Event

        try:
            # Find user profile
            user_profile = UserProfile.objects.get(phone_number=phone_number)

            logger.info(f"Debugging Reminders for User: {phone_number}")
            logger.info(f"Reminders Enabled: {user_profile.enable_reminders}")
            logger.info(f"Default Reminder Minutes: {user_profile.default_reminder_minutes}")

            # Get upcoming events
            now = timezone.now()
            upcoming_events = Event.objects.filter(
                user_profile=user_profile,
                start_time__gt=now
            ).order_by('start_time')

            logger.info(f"Total Upcoming Events: {upcoming_events.count()}")

            for event in upcoming_events:
                logger.info("Event Details:")
                logger.info(f"Summary: {event.summary}")
                logger.info(f"Start Time: {event.start_time}")
                logger.info(f"Reminder Minutes: {event.reminder_minutes}")
                logger.info(f"Reminder Sent: {event.reminder_sent}")
                logger.info("-" * 50)

        except UserProfile.DoesNotExist:
            logger.error(f"No user profile found for phone number: {phone_number}")
        except Exception as e:
            logger.error(f"Error debugging user reminders: {str(e)}")
            logger.error(traceback.format_exc())

# Usage examples in Django shell:
# from scheduler.reminder_debugger import ReminderDebugger
#
# # Run comprehensive reminder check
# ReminderDebugger.comprehensive_reminder_check()
#
# # Debug reminders for a specific phone number
# ReminderDebugger.debug_user_reminders('+1234567890')