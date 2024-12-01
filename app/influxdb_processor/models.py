from django.db import models
from core.models import (
    Processor, 
    Instance,
    PROCESSORS_MAPPER, 
)
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

INFLUX_PROCESSOR_TYPE = 'influxdb'
INFLUX_EMPTY_VALUR = 'unknown'


class InfluxdbProcessor(Processor):
    url = models.CharField(max_length=256, null=False, help_text="InfluxDB URL. Example: http://x.x.x.x:8086/")
    token = models.CharField(max_length=256, null=False, help_text="InfluxDB Token.")
    org = models.CharField(max_length=256, null=False, help_text="InfluxDB organization. Example: homelab")
    bucket = models.CharField(max_length=256, null=False, help_text="InfluxDB Bucket. Example: updates")
    verify_ssl = models.BooleanField(default=False, null=False, help_text="InfluxDB Bucket. Default: False")
    timeout = models.IntegerField(default=10000, null=False, help_text="InfluxDB Bucket. Default: 10000")

    def __init__(self, *args, **kwargs):
        self._meta.get_field('processor_type').default = INFLUX_PROCESSOR_TYPE
        super(Processor, self).__init__(*args, **kwargs)

    def process(self):
        instances = self._get_pending_instances()

        client = InfluxDBClient(
            url=self.url, 
            token=self.token, 
            org=self.org,
            verify_ssl=self.verify_ssl,
            timeout=self.timeout,
        )

        write_api = client.write_api(write_options=SYNCHRONOUS)

        for instance in instances:
            host = instance.host
            provider = host.provider
            value = Point("updates").tag(
                    "provider", provider.provider_name if provider is not None else INFLUX_EMPTY_VALUR
                ).tag(
                    "providerType", provider.provider_type if provider is not None else INFLUX_EMPTY_VALUR
                ).tag(
                    "host", host.host_name if host is not None else INFLUX_EMPTY_VALUR
                ).tag(
                    "hostType", host.host_type if host is not None else INFLUX_EMPTY_VALUR
                ).tag(
                    "instance", instance.instance_name
                ).tag(
                    "instanceType", instance.instance_type
                ).tag(
                    "currentVersion", instance.current_version if instance.current_version is not None else INFLUX_EMPTY_VALUR
                ).tag(
                    "currentTag", instance.current_tag if instance.current_tag is not None else INFLUX_EMPTY_VALUR
                ).tag(
                    "newVersion", instance.new_version if instance.new_version is not None else INFLUX_EMPTY_VALUR
                ).tag(
                    "latestVersion", instance.latest_version if instance.latest_version is not None else INFLUX_EMPTY_VALUR
                ).field(
                    "isUpdateAvailable", instance.is_update_available
                )
            write_api.write(bucket=self.bucket, record=value)
            self._mark_as_processed(instance=instance)

PROCESSORS_MAPPER[INFLUX_PROCESSOR_TYPE] = InfluxdbProcessor
