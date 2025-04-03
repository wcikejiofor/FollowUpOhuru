import stripe
from django.conf import settings
from django.urls import reverse
from twilio.rest import Client
from django.utils import timezone
from datetime import timedelta
from scheduler.google_calendar import logger
from scheduler.models import SubscriptionPlan, UserProfile
from .sms_sender import send_sms
from .url_shortener import URLShortener

stripe.api_key = settings.STRIPE_SECRET_KEY


class StripeSubscriptionManager:
    @staticmethod
    def _send_sms(phone_number, message):
        """
        Internal method to send SMS, avoiding direct import
        """
        try:
            # Dynamically import send_sms to avoid circular import
            from scheduler.views import send_sms
            return send_sms(phone_number, message)
        except ImportError:
            logger.error("Could not import send_sms function")
            return False

    from .url_shortener import URLShortener

    @staticmethod
    def create_subscription_checkout(user_profile, plan):
        """
        Create a Stripe Checkout Session for subscriptions
        """
        stripe.api_key = settings.STRIPE_SECRET_KEY

        try:
            logger.debug(f"Creating checkout session for user {user_profile.id}, plan: {plan}")

            # Define Stripe price IDs for each plan
            plan_prices = {
                'starter': 'price_1Qs3GsBMW3FJGGMOUtveta1W',
                'pro': 'price_1Qs3JYBMW3FJGGMOOmKxi2K2',
                'business': 'price_1QwpCGBMW3FJGGMOk2wiHbDs'
            }

            # Validate the plan
            if plan not in plan_prices:
                logger.error(f"Invalid plan selected: {plan}")
                return None

            # Get the price ID
            price_id = plan_prices[plan]
            logger.debug(f"Using price ID: {price_id}")

            # Verify the price exists in Stripe before creating the checkout session
            try:
                stripe_price = stripe.Price.retrieve(price_id)
                logger.debug(f"Price details: {stripe_price}")
            except stripe.error.StripeError as stripe_error:
                logger.error(f"Stripe price verification error: {stripe_error}")
                logger.error(f"Full error details: {str(stripe_error)}")
                return None

            # Create a Stripe customer with the phone number
            stripe_customer = stripe.Customer.create(
                phone=user_profile.phone_number,
                metadata={
                    'user_id': str(user_profile.id),
                    'plan': plan,
                    'phone_number': user_profile.phone_number
                }
            )

            logger.debug(f"Created Stripe customer: {stripe_customer.id}")
            logger.debug(f"Customer phone number: {stripe_customer.phone}")
            logger.debug(f"Customer metadata: {stripe_customer.metadata}")

            # Set the expiration time to 30 minutes from now
            expire_at = int((timezone.now() + timedelta(minutes=30)).timestamp())

            # Create Checkout Session
            session = stripe.checkout.Session.create(
                customer=stripe_customer.id,
                payment_method_types=['card'],
                mode='subscription',
                line_items=[{
                    'price': price_id,
                    'quantity': 1,
                }],
                success_url=f'https://www.chiresearchai.com/followup',
                cancel_url=f'https://chiresearchai.com/subscription/cancel?user_id={user_profile.id}',
                client_reference_id=str(user_profile.id),
                metadata={
                    'user_id': str(user_profile.id),
                    'plan': plan,
                    'phone_number': user_profile.phone_number
                },
                expires_at=expire_at  # Set expiration time
            )

            logger.debug(f"Checkout session created successfully. URL: {session.url}")

            # Generate a short, branded link and EXPLICITLY return it
            short_link = URLShortener.generate_short_link(session.url)

            logger.debug(f"Final short link to be returned: {short_link}")

            return short_link

        except stripe.error.StripeError as session_error:
            logger.error(f"Stripe Checkout Session Error: {session_error}")
            logger.error(f"Full session creation error details: {str(session_error)}")
            return None
        except Exception as e:
            logger.error(f"Unexpected Stripe Checkout Error for {plan} plan: {e}", exc_info=True)
            return None

    @staticmethod
    def send_checkout_link_via_sms(phone_number, checkout_url):
        """
        Send Stripe checkout URL via SMS
        """
        try:
            logger.debug(f"Attempting to send SMS to {phone_number}")

            # Initialize Twilio client
            twilio_client = Client(
                settings.TWILIO_ACCOUNT_SID,
                settings.TWILIO_AUTH_TOKEN
            )

            # Send SMS with checkout link
            message = twilio_client.messages.create(
                body=f"Complete your FollowUp subscription:\n{checkout_url}",
                from_=settings.TWILIO_PHONE_NUMBER,
                to=phone_number
            )

            logger.info(f"Checkout link sent to {phone_number}")
            return True
        except Exception as e:
            logger.error(f"SMS sending error: {e}", exc_info=True)
            return False

    @staticmethod
    def process_successful_subscription(session):
        try:
            logger.info(f"Process successful subscription entered")
            user_id = session['client_reference_id']
            plan = session['metadata']['plan']

            # Find the user
            user_profile = UserProfile.objects.get(id=user_id)

            # Update user's subscription based on the plan
            if plan == 'starter':
                user_profile.subscription_plan = SubscriptionPlan.STARTER
            elif plan == 'pro':
                user_profile.subscription_plan = SubscriptionPlan.PRO
            elif plan == 'business':
                user_profile.subscription_plan = SubscriptionPlan.BUSINESS

            # Store Stripe-related information
            user_profile.stripe_customer_id = session['customer']
            user_profile.stripe_subscription_id = session['subscription']
            user_profile.subscription_start_date = timezone.now()

            # Save the updated profile
            user_profile.save()

            # Generate authentication link
            auth_link = f"{settings.BASE_URL}/authorize/{user_profile.id}/"

            StripeSubscriptionManager._send_sms(
                user_profile.phone_number,
                f"🎉 Welcome to FollowUp! Your {plan.capitalize()} plan is now active. "
                "To get started, connect your Google Calendar securely. "
                "You may see a Google warning since we're still in beta—totally normal and safe. "
                "We never store or share your calendar info. "
                "Just click 'Advanced' → 'Continue' if prompted. "
                f"Connect here: {auth_link}"
            )

            # Send additional onboarding messages
            onboarding_messages = [
                "To schedule a meeting, simply text 'Schedule meeting with [name] at [time]'.",
                "Need help? Just text 'help' for a list of commands.",
                "Enjoy using FollowUp! We're here if you need anything."
            ]
            for message in onboarding_messages:
                StripeSubscriptionManager._send_sms(user_profile.phone_number, message)

            return user_profile
        except Exception as e:
            logger.error(f"Subscription processing error: {e}")
            return None

    @staticmethod
    def handle_payment_failed(event):
        """
        Handle payment failure scenario
        """
        try:
            invoice = event['data']['object']
            customer_id = invoice['customer']

            # Find the user associated with this Stripe customer
            user_profile = UserProfile.objects.get(stripe_customer_id=customer_id)

            # Send SMS about payment failure
            StripeSubscriptionManager._send_sms(
                user_profile.phone_number,
                "Payment for your FollowUp subscription failed. "
                "Please update your payment method to continue using the service."
            )

            # Downgrade to free plan
            user_profile.subscription_plan = SubscriptionPlan.FREE
            user_profile.stripe_customer_id = None
            user_profile.stripe_subscription_id = None
            user_profile.save()

            return user_profile
        except Exception as e:
            logger.error(f"Payment failure handling error: {e}")
            return None

    @staticmethod
    def handle_subscription_cancellation(event):
        """
        Handle subscription cancellation
        """
        try:
            subscription = event['data']['object']
            customer_id = subscription['customer']

            # Find the user associated with this Stripe customer
            user_profile = UserProfile.objects.get(stripe_customer_id=customer_id)

            # Send SMS about subscription cancellation
            StripeSubscriptionManager._send_sms(
                user_profile.phone_number,
                "Your FollowUp subscription has been canceled. "
                "You have been downgraded to the free plan."
            )

            # Downgrade to free plan
            user_profile.subscription_plan = SubscriptionPlan.FREE
            user_profile.stripe_customer_id = None
            user_profile.stripe_subscription_id = None
            user_profile.save()

            return user_profile
        except Exception as e:
            logger.error(f"Subscription cancellation error: {e}")
            return None

    @staticmethod
    def handle_checkout_session_completed(event):
        session = event['data']['object']
        logger.info(f"Checkout session completed: {session['id']}")

        try:
            user_profile = StripeSubscriptionManager.process_successful_subscription(session)
            if user_profile:
                logger.info(f"Successfully processed subscription for user {user_profile.id}")
            else:
                logger.error("Failed to process subscription")
        except Exception as e:
            logger.error(f"Error handling checkout session completed: {str(e)}")
