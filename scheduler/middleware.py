import logging

logger = logging.getLogger(__name__)

import logging

logger = logging.getLogger(__name__)


class AllowedHostsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Get host directly from headers instead of using get_host()
        host = request.META.get('HTTP_HOST', '')
        logger.error(f"Custom Middleware - Raw host header: {host}")

        # Store the host in request META to bypass Django's check
        request.META['HTTP_HOST'] = host

        # Always allow these hosts
        if host in ['followupohuru.onrender.com', 'checkout.chiresearchai.com']:
            logger.error(f"Allowing host: {host}")
            # Set a flag to indicate this host is pre-approved
            request.META['HOST_VERIFIED'] = True

        return self.get_response(request)