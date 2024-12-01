from django.db import models
from django.db.models import signals
from django.utils.translation import gettext_lazy as _

PROVIDERS_MAPPER = {}
PROCESSORS_MAPPER = {}


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

    processors = models.ManyToManyField(
        "Processor",
        through="CheckerProcessor",
        through_fields=("checker", "processor")
    )

    def __str__(self):
        return self.checker_name


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
    current_tag = models.CharField(max_length=100, null=True)
    current_version = models.CharField(max_length=100, null=True)
    new_version = models.CharField(max_length=100, null=True)
    latest_version = models.CharField(max_length=100, null=True)

    checker = models.ForeignKey(Checker, on_delete=models.CASCADE, null=True)
    host = models.ForeignKey(Host, on_delete=models.CASCADE, null=True)

    processors = models.ManyToManyField(
        "Processor",
        through="InstanceProcessor",
        through_fields=("instance", "processor")
    )

    def __str__(self):
        return f'{self.instance_name} ({self.instance_type})'


class Processor(models.Model):
    processor_type = models.CharField(max_length=20)
    processor_name = models.CharField(max_length=100)
    schedule = models.CharField(max_length=20, default="00 01 * * *")
    checkers = models.ManyToManyField(
        Checker,
        through="CheckerProcessor",
        through_fields=("processor", "checker")
    )
    instances = models.ManyToManyField(
        Instance,
        through="InstanceProcessor",
        through_fields=("processor", "instance"),
    )

    def _get_class_processor(self):
        processor_class = PROCESSORS_MAPPER.get(self.processor_type)
        if not processor_class:
            raise ProviderProcessorException(self.processor_type)
        return processor_class

    def _get_pending_instances(self):
        results =  InstanceProcessor.objects.filter(processor=self, processed=False).all()
        return list(map(lambda x: x.instance, results))

    def _mark_as_processed(self, instance):
        obj = InstanceProcessor.objects.filter(processor=self, instance=instance).first()
        obj.processed = True
        obj.save()

    def process(self):
        processor_class = self._get_class_processor()
        processor = processor_class.objects.get(id=self.id)
        processor.process()
    
    def __str__(self):
        return f'{self.processor_name} ({self.processor_type})'

class CheckerProcessor(models.Model):
    checker = models.ForeignKey(Checker, on_delete=models.CASCADE)
    processor = models.ForeignKey(Processor, on_delete=models.CASCADE)

class InstanceProcessor(models.Model):
    instance = models.ForeignKey(Instance, on_delete=models.CASCADE)
    processor = models.ForeignKey(Processor, on_delete=models.CASCADE)
    processed = models.BooleanField(default=False)

def create_instance_processor_link(sender, instance, created, **kwargs):
    for link in CheckerProcessor.objects.filter(checker=instance.checker).all():
        InstanceProcessor.objects.update_or_create(
            processor=link.processor,
            instance=instance,
            processed=False,
        )

signals.post_save.connect(
    create_instance_processor_link, 
    sender=Instance, 
    weak=False, 
    dispatch_uid='models.create_instance_processor_link'
)
