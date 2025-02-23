import uuid
from django.utils import timezone
from .models import ShortLink

import uuid
from django.utils import timezone
from django.db import IntegrityError
import logging
import urllib.parse

import uuid
from django.utils import timezone
import logging
import traceback
from django.conf import settings

logger = logging.getLogger(__name__)


class URLShortener:
    @classmethod
    def generate_short_link(cls, original_url):
        from .models import ShortLink  # Import here to avoid circular imports

        try:
            # Generate a unique short code
            short_code = uuid.uuid4().hex[:8]

            # Create short link with the full original URL
            short_link = ShortLink.objects.create(
                short_code=short_code,
                original_url=original_url,
                created_at=timezone.now()
            )

            # Log the created short link details
            logger.debug(f"Generated short link:")
            logger.debug(f"Short Code: {short_code}")
            logger.debug(f"Original URL: {original_url}")

            shortened_url = f"{settings.BASE_URL}/checkout/{short_code}"
            logger.debug(f"Full shortened URL: {shortened_url}")

            return shortened_url

        except Exception as e:
            logger.error(f"Error creating short link: {e}")
            logger.error(traceback.format_exc())
            return original_url