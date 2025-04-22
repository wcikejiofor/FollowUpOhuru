from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from scheduler.models import Event
from scheduler.sms_sender import send_sms
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Send event reminders for upcoming events'

    def handle(self, *args, **options):
        # Current time
        now = timezone.now()

        # Find events that need reminders
        events_needing_reminder = Event.objects.filter(
            reminder_sent=False,  # Not yet reminded
            user_profile__enable_reminders=True,  # User has reminders enabled
            start_time__gt=now,  # Event is in the future
            reminder_minutes__isnull=False  # Has a reminder set
        )

        self.stdout.write(f"Checking {events_needing_reminder.count()} events for reminders")

        for event in events_needing_reminder:
            try:
                # Calculate reminder time
                reminder_minutes = event.reminder_minutes or event.user_profile.default_reminder_minutes
                reminder_time = event.start_time - timedelta(minutes=reminder_minutes)

                # Check if it's time to send reminder
                if reminder_time <= now:
                    # Prepare reminder message
                    reminder_message = (
                        f"Reminder: Your event '{event.summary}' "
                        f"is starting in {reminder_minutes} minutes"
                    )

                    # Send SMS reminder
                    send_sms(
                        event.user_profile.phone_number,
                        reminder_message
                    )

                    # Mark reminder as sent
                    event.reminder_sent = True
                    event.save()

                    self.stdout.write(self.style.SUCCESS(
                        f"Sent reminder for event: {event.summary}"
                    ))

            except Exception as e:
                logger.error(f"Error sending reminder for event {event.id}: {e}")
                self.stdout.write(self.style.ERROR(
                    f"Failed to send reminder for event: {event.summary}"
                ))

        self.stdout.write(self.style.SUCCESS('Reminder check completed'))