from django.db import models
from django.utils.translation import gettext_lazy as _


class Provider(models.Model):
    provider_type = models.CharField(max_length=20)
    provider_name = models.CharField(max_length=100)
    config = models.JSONField()
    schedule = models.CharField(max_length=20)


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
    is_arhived = models.BooleanField(default=False, null=False)

    provider = models.ForeignKey(Provider, on_delete=models.CASCADE)
    checkers = models.ManyToManyField(
        Checker,
        through="HostChecker",
        through_fields=("host", "checker")
    )


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
