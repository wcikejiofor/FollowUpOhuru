import logging
from django.middleware.common import CommonMiddleware
from django.http import HttpRequest

logger = logging.getLogger(__name__)

import logging
from django.http import HttpRequest

logger = logging.getLogger(__name__)


class CustomCommonMiddleware(CommonMiddleware):
    def process_request(self, request: HttpRequest):
        host = request.META.get('HTTP_HOST', '')
        logger.error(f"Processing request for host: {host}")

        allowed_hosts = ['followupohuru.onrender.com', 'checkout.chiresearchai.com']

        if host in allowed_hosts:
            logger.error(f"Host {host} is allowed")

            # Replace the request's get_host method to always return the current host
            def get_host_override():
                return host

            request.get_host = get_host_override
            return None
        return super().process_request(request)
