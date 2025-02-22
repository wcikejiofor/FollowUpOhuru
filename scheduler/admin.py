from django.contrib import admin

# Register your models here.

# scheduler/admin.py
from django.contrib import admin
from .models import UserProfile, GoogleCredentials, Event

admin.site.register(UserProfile)
admin.site.register(GoogleCredentials)
admin.site.register(Event)


