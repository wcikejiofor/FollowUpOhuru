import logging
from django.utils import timezone
from datetime import timedelta
from django.conf import settings
from .models import UserProfile, SubscriptionPlan

from django.utils import timezone
from datetime import timedelta
from .models import Event, UserProfile  # Ensure this import is correct

logger = logging.getLogger(__name__)


class SubscriptionManager:
    @staticmethod
    def upgrade_subscription(user_profile, new_plan):
        """
        Upgrade user's subscription plan

        Args:
            user_profile (UserProfile): User whose plan is being upgraded
            new_plan (str): New subscription plan (from SubscriptionPlan choices)

        Returns:
            dict: Result of the upgrade operation
        """
        try:
            # Validate the new plan
            if new_plan not in dict(SubscriptionPlan.choices):
                return {
                    'success': False,
                    'message': 'Invalid subscription plan'
                }

            # Check if user is already on this plan
            if user_profile.subscription_plan == new_plan:
                return {
                    'success': False,
                    'message': 'You are already on this plan'
                }

            # Determine pricing
            plan_prices = {
                SubscriptionPlan.STARTER: 5,
                SubscriptionPlan.PRO: 10,
                SubscriptionPlan.BUSINESS: 20
            }

            # Update subscription details
            user_profile.subscription_plan = new_plan
            user_profile.subscription_start_date = timezone.now()

            # Reset trial if applicable
            if user_profile.is_trial_active():
                user_profile.trial_start_date = None

            # Reset monthly meetings counter
            user_profile.meetings_this_month = 0

            user_profile.save()

            return {
                'success': True,
                'message': f'Successfully upgraded to {new_plan} plan',
                'price': plan_prices.get(new_plan, 0)
            }

        except Exception as e:
            logger.error(f"Subscription upgrade error: {e}")
            return {
                'success': False,
                'message': 'An error occurred while upgrading your subscription'
            }

    @staticmethod
    def start_free_trial(user_profile):
        """
        Start a 7-day free trial for a user

        Args:
            user_profile (UserProfile): User starting the trial

        Returns:
            dict: Result of starting the trial
        """
        try:
            # Check if trial is already active
            if user_profile.is_trial_active():
                return {
                    'success': False,
                    'message': 'Free trial is already active'
                }

            # Start the trial
            user_profile.trial_start_date = timezone.now()
            user_profile.subscription_plan = SubscriptionPlan.STARTER
            user_profile.save()

            return {
                'success': True,
                'message': 'Free trial started successfully',
                'trial_end_date': user_profile.trial_start_date + timedelta(days=7)
            }

        except Exception as e:
            logger.error(f"Free trial start error: {e}")
            return {
                'success': False,
                'message': 'An error occurred while starting free trial'
            }

    @classmethod
    def check_meeting_eligibility(cls, user_profile):
        logger.debug(f"Checking meeting eligibility for profile ID: {user_profile.id}")
        logger.debug(f"Subscription Plan: {user_profile.subscription_plan}")
        logger.debug(f"Guest Mode: {user_profile.is_guest_mode}")

        # Guest mode or Business/Pro plans get unlimited meetings
        if user_profile.is_guest_mode or user_profile.subscription_plan.lower() in ['business',
                                                                                    'pro']:
            return {
                'eligible': True,
                'message': 'Unlimited meetings allowed'
            }

        # Starter plan: 30 meetings per month
        if user_profile.subscription_plan.lower() == 'starter':
            one_month_ago = timezone.now() - timedelta(days=30)
            monthly_meetings = Event.objects.filter(
                user_profile=user_profile,
                start_time__gte=one_month_ago
            ).count()

            # Allow 30 meetings per month for Starter plan
            if monthly_meetings >= 30:
                return {
                    'eligible': False,
                    'message': "You've reached the limit of 30 meetings for your Starter plan. "
                               "Upgrade to Pro or Business for unlimited meetings."
                }
            return {
                'eligible': True,
                'message': f'Meetings this month: {monthly_meetings}/30'
            }

        # Free plan: 3 meetings per month
        if user_profile.subscription_plan.lower() == 'free':
            one_month_ago = timezone.now() - timedelta(days=30)
            monthly_meetings = Event.objects.filter(
                user_profile=user_profile,
                start_time__gte=one_month_ago
            ).count()

            if monthly_meetings >= 3:
                return {
                    'eligible': False,
                    'message': "Upgrade to a paid plan to schedule more meetings. "
                               "You've reached the limit of 3 free meetings this month."
                }
            return {
                'eligible': True,
                'message': f'Meetings this month: {monthly_meetings}/3'
            }

        # If plan is not recognized, default to limiting meetings
        return {
            'eligible': True,
            'message': 'Temporary access allowed'
        }