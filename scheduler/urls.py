from django.urls import path
from . import views
from .views import (
    authorize_google, oauth2callback,
    handle_call, ivr_menu, voicemail,
    sms_handler, answer_call, check_credentials,
    handle_transcription
)

urlpatterns = [
    # Removed send_sms path as it's no longer in views
    path('authorize/<int:user_id>/', authorize_google, name='authorize_google'),
    path('oauth2callback/', oauth2callback, name='oauth2callback'),
    path('ivr-menu/', ivr_menu, name='ivr_menu'),
    path('voicemail/', voicemail, name='voicemail'),
    path('sms/', sms_handler, name='sms_handler'),
    path('answer-call/', answer_call, name='answer_call'),
    path('check-credentials/', check_credentials, name='check-credentials'),
    path('handle_call/', handle_call, name='handle_call'),
    path('handle_transcription/', handle_transcription, name='handle_transcription'),
    path('stripe/webhook/', views.stripe_webhook, name='stripe_webhook'),
    path('stripe/webhook', views.stripe_webhook, name='stripe_webhook'),
    path('checkout/<str:short_code>', views.redirect_short_link, name='short_link_redirect'),
]
