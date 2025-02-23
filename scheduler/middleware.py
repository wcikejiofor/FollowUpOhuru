import logging

logger = logging.getLogger(__name__)


class AllowedHostsMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Log the request details
        logger.error(f"Custom Middleware - Incoming request host: {request.get_host()}")

        # Allow both domains
        allowed_hosts = ['followupohuru.onrender.com', 'checkout.chiresearchai.com']

        # Get the host from the request
        host = request.META.get('HTTP_HOST', '')

        if host in allowed_hosts:
            logger.error(f"Host {host} is allowed")
            return self.get_response(request)
        else:
            logger.error(f"Host {host} checking through default middleware")
            return self.get_response(request)