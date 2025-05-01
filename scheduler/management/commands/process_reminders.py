from django.core.management.base import BaseCommand
from django.utils import timezone
from scheduler.models import ScheduledTask, UserProfile
from scheduler.sms_utils import SMSScheduler
import json
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Process scheduled reminders that are due'

    def handle(self, *args, **options):
        now = timezone.now()
        
        # Get all pending reminders that are due
        due_tasks = ScheduledTask.objects.filter(
            task_type='reminder',
            status='pending',
            scheduled_time__lte=now
        ).select_related('event', 'event__user_profile')

        processed = 0
        for task in due_tasks:
            try:
                # Skip if the event was deleted
                if not task.event:
                    task.status = 'failed'
                    task.save()
                    continue

                # Skip if reminders are disabled for this user
                if not task.event.user_profile.enable_reminders:
                    task.status = 'completed'
                    task.save()
                    continue

                # Skip if reminder was already sent
                if task.event.reminder_sent:
                    task.status = 'completed'
                    task.save()
                    continue

                # Get task data
                data = json.loads(task.data)
                phone_number = data.get('phone_number')

                if not phone_number:
                    logger.error(f"No phone number found for task {task.id}")
                    task.status = 'failed'
                    task.save()
                    continue

                # Send the reminder
                scheduler = SMSScheduler(task.event.user_profile)
                if scheduler.send_reminder_now(phone_number, task.event):
                    task.status = 'completed'
                    task.completed_at = timezone.now()
                    processed += 1
                else:
                    task.status = 'failed'
                
                task.save()

            except Exception as e:
                logger.error(f"Error processing reminder task {task.id}: {str(e)}")
                task.status = 'failed'
                task.save()

        self.stdout.write(
            self.style.SUCCESS(
                f'Successfully processed {processed} reminders out of {len(due_tasks)} due tasks'
            )
        ) 