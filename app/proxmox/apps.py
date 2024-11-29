from django.apps import AppConfig
# from core.apps import providers_mapper


class ProxmoxConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'proxmox'


# providers_mapper['proxmox']: proxmox.processor.ProxmoxProcessor