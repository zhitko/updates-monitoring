from proxmoxer import ProxmoxAPI
from django.db import models
from core.models import Provider as BaseProvider, PROVIDERS_MAPPER, Host

PROVIDER_TYPE = 'proxmox'


class Provider(BaseProvider):

    proxmox_host = models.CharField(max_length=20)
    proxmox_user = models.CharField(max_length=20)
    proxmox_password = models.CharField(max_length=20)

    def __init__(self, *args, **kwargs):
        self._meta.get_field('provider_type').default = PROVIDER_TYPE
        super(Provider, self).__init__(*args, **kwargs)

    def _connect(self):
        return ProxmoxAPI(
            host=self.proxmox_host,
            user=self.proxmox_user,
            password=self.proxmox_password,
            verify_ssl=False
        )

    # noinspection PyMethodMayBeStatic
    def _get_hosts_info(self, proxmox_connection):
        hosts = []
        for pve_node in proxmox_connection.nodes.get():
            for container in proxmox_connection.nodes(pve_node['node']).lxc.get():
                hosts.append({
                    'name': f'{container["vmid"]}. {container["name"]}',
                    'type': 'LXC',
                    'status': container['status']
                })
            for container in proxmox_connection.nodes(pve_node['node']).qemu.get():
                hosts.append({
                    'name': f'{container["vmid"]}. {container["name"]}',
                    'type': 'VM',
                    'status': container['status']
                })
        return hosts

    def _create_hosts(self, hosts):
        for host in hosts:
            new_values = {
                'host_type': host['type'],
                'host_name': host['name'],
                'provider': self,
            }
            Host.objects.update_or_create(
                host_name=host['name'], host_type=host['type'], provider=self,
                defaults=new_values,
            )

    def process_update_hosts(self):
        connection = self._connect()
        hosts = self._get_hosts_info(connection)
        self._create_hosts(hosts)


PROVIDERS_MAPPER[PROVIDER_TYPE] = Provider
