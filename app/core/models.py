from django.db import models
from django.utils.translation import gettext_lazy as _

PROVIDERS_MAPPER = {}


class ProviderProcessorException(Exception):
    def __init__(self, provider_type: str):
        self.provider_type = provider_type
        super().__init__(f"Undefined processor for provider type: {self.provider_type}")


class Provider(models.Model):
    provider_type = models.CharField(max_length=20)
    provider_name = models.CharField(max_length=100)
    schedule = models.CharField(max_length=20)

    def __str__(self):
        return f'{self.provider_name} ({self.provider_type})'

    def _get_class_processor(self):
        class_processor = PROVIDERS_MAPPER.get(self.provider_type)
        if not class_processor:
            raise ProviderProcessorException(self.provider_type)
        return class_processor

    def update_hosts(self):
        class_processor = self._get_class_processor()
        processor = class_processor.objects.get(id=self.id)
        return processor.process_update_hosts()


class Checker(models.Model):
    checker_type = models.CharField(max_length=20)
    checker_name = models.CharField(max_length=100)
    schedule = models.CharField(max_length=20)


class Host(models.Model):
    class HostStatus(models.TextChoices):
        ACTIVE = "active", _("Active")
        STOPPED = "stopped", _("Stopped")
        FAILED = "failed", _("Failed")

    host_type = models.CharField(max_length=20)
    host_name = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=HostStatus, default=HostStatus.ACTIVE)
    is_archived = models.BooleanField(default=False, null=False)

    provider = models.ForeignKey(Provider, on_delete=models.CASCADE)
    checkers = models.ManyToManyField(
        Checker,
        through="HostChecker",
        through_fields=("host", "checker")
    )

    def __str__(self):
        return f'{self.host_name} ({self.host_type})'


class HostChecker(models.Model):
    checker = models.ForeignKey(Checker, on_delete=models.CASCADE)
    host = models.ForeignKey(Host, on_delete=models.CASCADE)


class Instance(models.Model):
    instance_type = models.CharField(max_length=20)
    instance_name = models.CharField(max_length=100)
    is_update_available = models.BooleanField(default=False, null=False)
    current_version = models.CharField(max_length=100)
    new_version = models.CharField(max_length=100)

    checker = models.ForeignKey(Checker, on_delete=models.CASCADE)


class Processor(models.Model):
    processor_type = models.CharField(max_length=20)
    processor_name = models.CharField(max_length=100)
    config = models.JSONField()
