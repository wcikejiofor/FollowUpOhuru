from django.core.management.base import BaseCommand
from django.utils import timezone
import logging
import json
import traceback
from scheduler.models import ScheduledTask, Event
from scheduler.sms_sender import send_sms

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Send event reminders based on scheduled tasks'

    def handle(self, *args, **options):
        now = timezone.now()

        # Find tasks that are due
        due_tasks = ScheduledTask.objects.filter(
            task_type='reminder',
            scheduled_time__lte=now,
            status='pending'
        )

        self.stdout.write(f"Processing {due_tasks.count()} due reminder tasks")
        sent_count = 0

        # Process each task
        for task in due_tasks:
            try:
                # Extract task data
                data = json.loads(task.data)
                phone_number = data.get('phone_number')
                event_summary = data.get('event_summary')
                event_time_str = data.get('event_time')

                if not phone_number or not event_summary or not event_time_str:
                    logger.error(f"Missing required data in task {task.id}")
                    task.status = 'failed'
                    task.save()
                    continue

                # Format reminder message
                reminder_message = f"Reminder: Your event '{event_summary}' is starting soon."

                # Send SMS reminder
                send_sms(phone_number, reminder_message)

                # Mark task as completed
                task.status = 'completed'
                task.completed_at = timezone.now()
                task.save()

                # Also update the event if it's linked
                if task.event:
                    task.event.reminder_sent = True
                    task.event.save()

                sent_count += 1
                self.stdout.write(self.style.SUCCESS(
                    f"Sent reminder for event: {event_summary}"
                ))

            except Exception as e:
                logger.error(f"Error processing reminder task {task.id}: {e}")
                logger.error(traceback.format_exc())
                self.stdout.write(self.style.ERROR(
                    f"Failed to process reminder task {task.id}"
                ))

                # Mark as failed
                task.status = 'failed'
                task.save()

        # Also check for the old style events that need reminders (for backward compatibility)
        self.check_events_directly(now)

        self.stdout.write(self.style.SUCCESS(f'Sent {sent_count} reminders via scheduled tasks'))

    def check_events_directly(self, now):
        """Legacy method to check events directly for reminders"""
        # Find events that need reminders
        events_needing_reminder = Event.objects.filter(
            reminder_sent=False,  # Not yet reminded
            user_profile__enable_reminders=True,  # User has reminders enabled
            start_time__gt=now,  # Event is in the future
            reminder_minutes__isnull=False  # Has a reminder set
        )

        self.stdout.write(f"Also checking {events_needing_reminder.count()} events directly")
        sent_count = 0

        for event in events_needing_reminder:
            try:
                # Calculate reminder time
                reminder_minutes = event.reminder_minutes or event.user_profile.default_reminder_minutes
                reminder_time = event.start_time - timedelta(minutes=reminder_minutes)

                # Add debug logging
                logger.debug(f"Event: {event.id}, Summary: {event.summary}")
                logger.debug(
                    f"Start time: {event.start_time}, Reminder minutes: {reminder_minutes}")
                logger.debug(f"Reminder time: {reminder_time}, Current time: {now}")
                logger.debug(f"Should send reminder: {reminder_time <= now}")

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

                    sent_count += 1
                    self.stdout.write(self.style.SUCCESS(
                        f"Sent reminder for event: {event.summary}"
                    ))

            except Exception as e:
                logger.error(f"Error sending reminder for event {event.id}: {e}")
                logger.error(traceback.format_exc())
                self.stdout.write(self.style.ERROR(
                    f"Failed to send reminder for event: {event.summary}"
                ))

        self.stdout.write(self.style.SUCCESS(f'Sent {sent_count} reminders directly from events'))