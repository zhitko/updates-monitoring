from django.contrib import admin

from core.admin import ProcessorAdmin
from .models import (
    InfluxdbProcessor,
)

class InfluxdbProcessorAdmin(ProcessorAdmin):
    list_display = ["processor_name", "url", "org", "bucket"]
    
    def has_add_permission(self, request, obj=None):
        return True

admin.site.register(InfluxdbProcessor, InfluxdbProcessorAdmin)