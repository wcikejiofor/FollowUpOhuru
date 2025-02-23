import logging
from django.middleware.common import CommonMiddleware
from django.http import HttpRequest

logger = logging.getLogger(__name__)

import logging
from django.http import HttpRequest

logger = logging.getLogger(__name__)


class CustomHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        host = request.META.get('HTTP_HOST', '')
        logger.error(f"Processing request for host: {host}")

        allowed_hosts = ['followupohuru.onrender.com', 'checkout.chiresearchai.com']

        if host in allowed_hosts:
            logger.error(f"Host {host} is allowed")

            # Replace the request's get_host method
            def custom_get_host(*args, **kwargs):
                return host

            # Monkey patch both the instance and class method
            request.get_host = custom_get_host
            HttpRequest.get_host = custom_get_host

            # Also set allowed host directly in request.META
            request.META['ALLOWED_HOSTS'] = allowed_hosts

        return self.get_response(request)