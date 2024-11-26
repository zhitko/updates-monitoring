from django.contrib import admin

from .models import (
    Provider,
    Host,
    Checker,
    Instance,
    Processor,
)

admin.site.register(Provider)
admin.site.register(Host)
admin.site.register(Checker)
admin.site.register(Instance)
admin.site.register(Processor)
