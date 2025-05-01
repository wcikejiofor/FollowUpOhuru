from django.core.management.base import BaseCommand
from django.utils import timezone
from scheduler.models import ScheduledTask, UserProfile
from scheduler.sms_utils import SMSScheduler
import json
import logging
import time
from datetime import timedelta
import traceback

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Process scheduled reminders that are due'

    def add_arguments(self, parser):
        parser.add_argument(
            '--interval',
            type=int,
            default=60,
            help='Interval in seconds between checks (default: 60)'
        )

    def handle(self, *args, **options):
        interval = options['interval']
        logger.info(f"Starting reminder processor with {interval} second interval")
        
        while True:
            try:
                now = timezone.now()
                logger.info(f"Checking for due reminders at {now}")
                
                # Get all pending reminders that are due
                due_tasks = ScheduledTask.objects.filter(
                    task_type='reminder',
                    status='pending',
                    scheduled_time__lte=now
                ).select_related('event', 'event__user_profile')

                logger.info(f"Found {len(due_tasks)} due tasks")
                processed = 0
                for task in due_tasks:
                    try:
                        logger.info(f"Processing task {task.id} for event {task.event.id if task.event else 'None'}")
                        
                        # Skip if the event was deleted
                        if not task.event:
                            logger.warning(f"Task {task.id} has no associated event")
                            task.status = 'failed'
                            task.save()
                            continue

                        # Skip if reminders are disabled for this user
                        if not task.event.user_profile.enable_reminders:
                            logger.warning(f"Reminders disabled for user {task.event.user_profile.id}")
                            task.status = 'completed'
                            task.save()
                            continue

                        # Skip if reminder was already sent
                        if task.event.reminder_sent:
                            logger.warning(f"Reminder already sent for event {task.event.id}")
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

                        logger.info(f"Sending reminder for event {task.event.id} to {phone_number}")
                        
                        # Send the reminder
                        scheduler = SMSScheduler(task.event.user_profile)
                        if scheduler.send_reminder_now(phone_number, task.event):
                            logger.info(f"Successfully sent reminder for event {task.event.id}")
                            task.status = 'completed'
                            task.completed_at = timezone.now()
                            processed += 1
                        else:
                            logger.error(f"Failed to send reminder for event {task.event.id}")
                            task.status = 'failed'
                        
                        task.save()

                    except Exception as e:
                        logger.error(f"Error processing reminder task {task.id}: {str(e)}")
                        logger.error(traceback.format_exc())
                        task.status = 'failed'
                        task.save()

                if processed > 0:
                    logger.info(f"Processed {processed} reminders")
                else:
                    logger.info("No reminders processed in this cycle")

                # Sleep for the specified interval
                time.sleep(interval)

            except Exception as e:
                logger.error(f"Error in reminder processor loop: {str(e)}")
                logger.error(traceback.format_exc())
                # Sleep for a shorter interval on error
                time.sleep(10) 