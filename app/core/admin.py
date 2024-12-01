from django.contrib import admin
from django import forms

from .models import (
    Provider,
    Host,
    Checker,
    Instance,
    Processor,
    HostChecker,
    CheckerProcessor,
    InstanceProcessor,
)


class ProviderAdmin(admin.ModelAdmin):
    list_display = ["provider_type", "provider_name"]
    ordering = ["provider_type"]
    actions = ["update_hosts"]

    @admin.action(description="Update lists of hosts")
    def update_hosts(self, request, queryset):
        for provider in queryset.all():
            provider.update_hosts()


class HostAdmin(admin.ModelAdmin):
    list_display = ["provider", "host_name", "host_type", "status"]
    ordering = ["provider", "host_type", "host_name"]

@admin.action(description="Run processing")
def processor_run_process(modeladmin, request, queryset):
    for processor in queryset.all():
        processor.process()

class ProcessorAdmin(admin.ModelAdmin):
    readonly_fields = ["processor_type"]
    list_display = ["processor_name", "processor_type", "schedule"]
    ordering = ["processor_name"]
    actions = [processor_run_process]
    
    def has_add_permission(self, request, obj=None):
        return False


admin.site.register(Provider, ProviderAdmin)
admin.site.register(Host, HostAdmin)
admin.site.register(Checker)
admin.site.register(Instance)
admin.site.register(HostChecker)
admin.site.register(CheckerProcessor)
admin.site.register(InstanceProcessor)
admin.site.register(Processor, ProcessorAdmin)
