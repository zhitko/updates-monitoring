from django.contrib import admin
from django import forms

from .models import (
    Provider,
    Host,
    Checker,
    Instance,
    Processor,
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


admin.site.register(Provider, ProviderAdmin)
admin.site.register(Host, HostAdmin)
admin.site.register(Checker)
admin.site.register(Instance)
admin.site.register(Processor)
