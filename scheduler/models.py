from django.db import models
from django.utils import timezone
from datetime import timedelta
import pytz
import json
import timedelta

class SubscriptionPlan(models.TextChoices):
    FREE = 'FREE', 'Free Plan'
    STARTER = 'STARTER', 'Starter Plan'
    PRO = 'PRO', 'Pro Plan'
    BUSINESS = 'BUSINESS', 'Business Plan'

class UserProfile(models.Model):
    phone_number = models.CharField(max_length=50, null=True, blank=True)

    # Enhanced subscription fields
    subscription_plan = models.CharField(
        max_length=20,
        choices=SubscriptionPlan.choices,
        default=SubscriptionPlan.FREE,
    )
    subscription_start_date = models.DateTimeField(null=True, blank=True)
    trial_start_date = models.DateTimeField(null=True, blank=True)
    meetings_this_month = models.IntegerField(default=0)
    stripe_customer_id = models.CharField(max_length=100, null=True, blank=True)

    # Google credentials
    google_credentials = models.TextField(null=True, blank=True)

    # Guest mode field
    is_guest_mode = models.BooleanField(default=True)
    setup_stage = models.CharField(max_length=20, null=True, blank=True)

    def is_trial_active(self):
        """Check if the 7-day free trial is still active"""
        if not self.trial_start_date:
            return False
        return timezone.now() <= self.trial_start_date + timedelta(days=7)

    def parse_google_credentials(self):
        """Parse stored Google credentials"""
        if not self.google_credentials:
            return None
        try:
            return json.loads(self.google_credentials)
        except json.JSONDecodeError:
            return None

    def can_schedule_meeting(self):
        """Check if user can schedule a meeting based on their plan"""
        # Guest mode or Starter plan gets full access
        if self.is_guest_mode or self.subscription_plan == SubscriptionPlan.STARTER:
            return True

        if self.subscription_plan in [SubscriptionPlan.PRO, SubscriptionPlan.BUSINESS]:
            # Pro and Business plans: Unlimited meetings
            return True

        return False

    def increment_meetings(self):
        """Increment the number of meetings for the current month"""
        # Skip incrementing for guest mode or if it's a plan with unlimited meetings
        if (self.is_guest_mode or
            self.subscription_plan in [SubscriptionPlan.PRO, SubscriptionPlan.BUSINESS]):
            return

        # Reset meetings count at the start of each month
        if (not self.subscription_start_date or
                timezone.now().month != self.subscription_start_date.month):
            self.meetings_this_month = 1
            self.subscription_start_date = timezone.now()
        else:
            self.meetings_this_month += 1
        self.save()

    def upgrade_to_full_account(self):
        """Transition from guest mode to full account"""
        self.is_guest_mode = False
        self.save()

    def start_trial(self):
        """Start the 7-day free trial"""
        self.trial_start_date = timezone.now()
        self.subscription_plan = SubscriptionPlan.STARTER
        self.save()

    @classmethod
    def delete_by_phone(cls, phone_number):
        """Delete user profile by phone number"""
        try:
            user = cls.objects.get(phone_number=phone_number)
            user.delete()
            return True
        except cls.DoesNotExist:
            return False

    def get_plan_features(self):
        """Return features for the current subscription plan"""
        features = {
            SubscriptionPlan.FREE: {
                'name': 'Free Plan',
                'max_meetings': 5,
                'sms_scheduling': False,
                'priority': False,
                'price': 0
            },
            SubscriptionPlan.STARTER: {
                'name': 'Starter Plan',
                'max_meetings': 30,
                'sms_scheduling': True,
                'priority': False,
                'price': 5,
                'description': 'For freelancers and small business owners'
            },
            SubscriptionPlan.PRO: {
                'name': 'Pro Plan',
                'max_meetings': float('inf'),  # Unlimited
                'sms_scheduling': True,
                'priority': True,
                'smart_rescheduling': 'coming soon',
                'price': 10,
                'description': 'For busy professionals and entrepreneurs'
            },
            SubscriptionPlan.BUSINESS: {
                'name': 'Business Plan',
                'max_meetings': float('inf'),  # Unlimited
                'sms_scheduling': True,
                'priority': True,
                'assistant_mode': True,
                'custom_reminders': True,
                'price': 20,
                'description': 'For executives, assistants, and sales teams'
            }
        }
        return features.get(self.subscription_plan, features[SubscriptionPlan.FREE])

    def __str__(self):
        return f"{self.phone_number} - {self.get_subscription_plan_display()}"


class GoogleCredentials(models.Model):
    token = models.TextField()
    refresh_token = models.TextField(null=True, blank=True)
    token_uri = models.TextField(null=True, blank=True)
    client_id = models.TextField(null=True, blank=True)
    client_secret = models.TextField(null=True, blank=True)
    scopes = models.TextField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Credentials for {self.client_id}"


class Event(models.Model):
    user_profile = models.ForeignKey(UserProfile, on_delete=models.CASCADE)
    summary = models.CharField(max_length=255)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    location = models.CharField(max_length=255, blank=True, null=True)

    def __str__(self):
        return f"{self.summary} - {self.start_time}"


class ShortLink(models.Model):
    short_code = models.CharField(max_length=10, unique=True)
    original_url = models.TextField()  # Change this to TextField to store long URLs
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.short_code} - {self.original_url[:50]}..."
