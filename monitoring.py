import subprocess
import json
import requests
import time
import os
from typing import Dict, List

# -------------------------------------------------------------------------------------
# General config
# -------------------------------------------------------------------------------------
DEBUG_MODE = True
MANIFESTS_FOLDER = 'manifests'
USE_CACHE = True
# -------------------------------------------------------------------------------------
# Docker manifest config
# -------------------------------------------------------------------------------------
DOCKER_ARCHITECTURE = 'amd64'
DOCKER_OS = 'linux'
# -------------------------------------------------------------------------------------
# Influx config
# -------------------------------------------------------------------------------------
INFLUX_HOST = ''
INFLUX_PORT = '8086'
INFLUX_ORG = 'home'
INFLUX_BUCKET = 'pve_updates'
INFLUX_TOKEN = ''
# -------------------------------------------------------------------------------------

container_processors_mapping = {
    '102': ['docker'],
    # '103': ['docker'],
    '104': ['docker'],
    '105': ['docker'],
    '106': ['docker'],
    # '108': ['docker'],
    '109': ['docker'],
    '112': ['docker'],
}

# -------------------------------------------------------------------------------------
# Utilities
# -------------------------------------------------------------------------------------


def dict_deep_get(obj: Dict, route: List[str]):
    """
    recursive function which allows to get value from dict with several levels by route
    """
    count_points = len(route)
    value = ''
    for count, point in enumerate(route):
        value = obj.get(point, {})
        if count + 1 != count_points:
            if not isinstance(value, dict):
                return ''
            return dict_deep_get(value, route[1::])
    return value or ''

# -------------------------------------------------------------------------------------
# Main processes
# -------------------------------------------------------------------------------------


class DockerProcessor:
    class Commands:
        base_command = "pct exec {container_id} -- bash -c '{command}'"
        get_images = 'docker ps --format {{.Image}}'
        docker_inspect = 'docker inspect {image_name}'
        docker_buildx_inspect = 'docker buildx imagetools inspect {image_name} --format "{{{{json .}}}}"'

    def __init__(self, container_id):
        self.container_id = container_id
        self.type = 'docker'
        if DEBUG_MODE:
            try:
                os.mkdir(MANIFESTS_FOLDER)
            except FileExistsError:
                pass
            except PermissionError:
                print(f"Permission denied: Unable to create '{MANIFESTS_FOLDER}'.")
            except Exception as e:
                print(f"An error occurred: {e}")

    def __exec_command(self, cmd):
        cmd = self.Commands.base_command.format(
            container_id=self.container_id,
            command=cmd
        )
        result = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
        return [line.decode('utf-8').strip() for line in result.stdout]

    def __debug_write_manifest_info(self, image_name, prefix, lines):
        with open(f'{MANIFESTS_FOLDER}/{image_name.replace("/", "_")}_{prefix}.txt', 'w') as f:
            f.writelines(f'Container id = {self.container_id}\n')
            f.writelines(f'Image = {image_name}\n')
            for line in lines:
                f.writelines(line + '\n')

    def _get_images(self):
        return self.__exec_command(self.Commands.get_images)

    def _get_local_docker_image_digest(self, image_name):
        version = ''
        digest = ''

        manifest_res = self.__exec_command(self.Commands.docker_inspect.format(image_name=image_name))
        if DEBUG_MODE:
            self.__debug_write_manifest_info(image_name, 'current_local', manifest_res)
        manifests_json = json.loads(''.join(manifest_res))

        for manifest_json in manifests_json:
            if manifest_json.get('Architecture') == DOCKER_ARCHITECTURE:
                repo_digest = manifest_json.get('RepoDigests')
                if len(repo_digest) > 0:
                    digest = repo_digest[0].split('@')[-1]
                version = dict_deep_get(manifest_json, ['Config', 'Labels', 'org.opencontainers.image.version']) or '-'
        return {
            'current_local': {
                'digest': digest,
                'version': version,
            }
        }

    def _get_remote_docker_image_digest(self, image_name):
        response = {}
        # parse image name
        tag = image_name.split(':')[-1]
        image_name_without_tag = image_name.split(':')[0]

        # get current remote info
        manifest_res = self.__exec_command(self.Commands.docker_buildx_inspect.format(image_name=image_name))
        if DEBUG_MODE:
            self.__debug_write_manifest_info(image_name, 'remote_current', manifest_res)
        manifest_json = json.loads(''.join(manifest_res))

        response['current_remote'] = {
            'digest': dict_deep_get(manifest_json, ['manifest', 'digest']) or '-',
            'version': dict_deep_get(manifest_json, [
                'image', f'{DOCKER_OS}/{DOCKER_ARCHITECTURE}', 'config', 'Labels', 'org.opencontainers.image.version'
            ]) or '-',
        }

        if tag == 'latest':
            response['latest_remote'] = response['current_remote']
        else:
            # get info about latest version of image
            latest_manifest_res = self.__exec_command(
                self.Commands.docker_buildx_inspect.format(image_name=f'{image_name_without_tag}:latest')
            )
            if DEBUG_MODE:
                self.__debug_write_manifest_info(image_name, 'remote_latest', latest_manifest_res)
            latest_manifest_json = json.loads(''.join(latest_manifest_res))

            response['latest_remote'] = {
                'digest': dict_deep_get(latest_manifest_json, ['manifest', 'digest']) or '-',
                'version': dict_deep_get(latest_manifest_json, [
                    'image', f'{DOCKER_OS}/{DOCKER_ARCHITECTURE}', 'config', 'Labels', 'org.opencontainers.image.version'
                ]) or '-',
            }
        return response

    def process(self):
        images = self._get_images()
        images_updates_info = {}
        for image_name in images:

            print(f'[{self.container_id}] {image_name}')
            local_repo_digest_info = self._get_local_docker_image_digest(image_name)
            print('local_repo_digest = %s' % local_repo_digest_info)
            remote_repo_digest_info = self._get_remote_docker_image_digest(image_name)
            print('remote_repo_digest = %s' % remote_repo_digest_info)

            images_updates_info[image_name] = {
                'type': self.type,
                'local_current_digest': local_repo_digest_info['current_local']['digest'],
                'local_current_version': local_repo_digest_info['current_local']['version'],
                'remote_current_digest': remote_repo_digest_info['current_remote']['digest'],
                'remote_current_version': remote_repo_digest_info['current_remote']['version'],
                'remote_latest_digest': remote_repo_digest_info['latest_remote']['digest'],
                'remote_latest_version': remote_repo_digest_info['latest_remote']['version'],
            }
        return images_updates_info


processors_mapping = {
    'docker': DockerProcessor
}


class PVEMonitoring:
    class Commands:
        get_containers_ids = "pct list | awk '{if(NR>1) print $1}'"
        check_container_is_template = 'pct config {container_id} | grep -q "template:" && echo "true" || echo "false"'

    def __init__(self):
        self.checkers = ['docker', 'apt']

    def __exec_command(self, cmd):
        result = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
        return [line.decode('utf-8').strip() for line in result.stdout]

    def _check_container_is_template(self, container_id):
        is_template = self.__exec_command(self.Commands.check_container_is_template.format(container_id=container_id))
        if len(is_template) > 0:
            is_template = is_template[0]
        else:
            raise
        return is_template

    def _get_containers_ids(self, exclude_templates=True):
        print('Get containers ids...')
        containers_ids = self.__exec_command(self.Commands.get_containers_ids)
        if exclude_templates:
            for container_id in containers_ids:
                is_template = self._check_container_is_template(container_id)
                if is_template == 'true':
                    containers_ids.remove(container_id)
        return containers_ids

    def process(self):
        """
        response example:
        {
           "105":{
              "lscr.io/linuxserver/transmission:latest":{
                 "type":"docker",
                 "local_current_digest":"sha256:25692848ea167ef57f3914a55393d48b7a96c201a0dcc2002e316bcd146ddd8c",
                 "local_current_version":"4.0.6-r0-ls272",
                 "remote_current_digest":"sha256:25692848ea167ef57f3914a55393d48b7a96c201a0dcc2002e316bcd146ddd8c",
                 "remote_current_version":"4.0.6-r0-ls272",
                 "remote_latest_digest":"sha256:25692848ea167ef57f3914a55393d48b7a96c201a0dcc2002e316bcd146ddd8c",
                 "remote_latest_version":"4.0.6-r0-ls272"
              },
              "portainer/agent:2.21.3":{
                 "type":"docker",
                 "local_current_digest":"sha256:0298f083ae43930ae3cbc9cacafa89be6d5a3e2ab0aff5312a84712916e8d234",
                 "local_current_version":"-",
                 "remote_current_digest":"sha256:0298f083ae43930ae3cbc9cacafa89be6d5a3e2ab0aff5312a84712916e8d234",
                 "remote_current_version":"-",
                 "remote_latest_digest":"sha256:b87309640050c93433244b41513de186f9456e1024bfc9541bc9a8341c1b0938",
                 "remote_latest_version":"-"
              }
           }
        }
        """
        print('Checking updates...')
        containers_updates_info = {}
        containers_ids = self._get_containers_ids()
        print(f'Got containers = {containers_ids}')
        for container_id in containers_ids:
            processors_labels = container_processors_mapping.get(container_id, [])
            for processor_label in processors_labels:
                processor = processors_mapping.get(processor_label)
                if not processor:
                    continue
                print(f'Trying to get updates using processor "{processor_label}" for container id = {container_id}')
                images_updates_info = processor(container_id).process()
                containers_updates_info[container_id] = images_updates_info
        return containers_updates_info


class InfluxDBSender:
    def __init__(self):
        self.host = INFLUX_HOST
        self.port = INFLUX_PORT
        self.org = INFLUX_ORG
        self.bucket = INFLUX_BUCKET
        self.token = INFLUX_TOKEN

    def _prepare_data(self, monitoring_info):
        data_raws = []
        data_raw_template = 'updates,container_id={container_id},instance_type={instance_type},instance_name={instance_name},local_current_digest={local_current_digest},local_current_version={local_current_version},remote_current_digest={remote_current_digest},remote_current_version={remote_current_version},remote_latest_digest={remote_latest_digest},remote_latest_version={remote_latest_version} value=1 {current_unix_time}'
        for container_id, container_data in monitoring_info.items():
            for instance_name, instance_data in container_data.items():
                data_raws.append(
                    data_raw_template.format(
                        container_id=container_id,
                        instance_type=instance_data.get('type'),
                        instance_name=instance_name,
                        local_current_digest=instance_data.get('local_current_digest'),
                        local_current_version=instance_data.get('local_current_version'),
                        remote_current_digest=instance_data.get('remote_current_digest'),
                        remote_current_version=instance_data.get('remote_current_version'),
                        remote_latest_digest=instance_data.get('remote_latest_digest'),
                        remote_latest_version=instance_data.get('remote_latest_version'),
                        current_unix_time=time.time_ns()
                    )
                )
        data_raw = '\n'.join(data_raws)
        return data_raw

    def send(self, monitoring_info):
        print('Starting sending updating info to InfluxDB')
        url = f'{self.host}:{self.port}/api/v2/write?org={self.org}&bucket={self.bucket}&precision=ns'
        data = self._prepare_data(monitoring_info)
        print(data)
        try:
            response = requests.post(
                url,
                headers={
                    'Authorization': f'Token {self.token}',
                    'Content-Type': 'text/plain; charset=utf-8',
                    'Accept': 'application/json',
                },
                data=data
            )
            response.raise_for_status()
            print('Successfully sent updating info to InfluxDB')
        except Exception as e:
            print('Something wrong during sending updating info to InfluxDB')
            print('error = %s' % e)


monitoring = PVEMonitoring()
res = monitoring.process()
# res = {'105': {'lscr.io/linuxserver/transmission:latest': {'type': 'docker', 'local_current_digest': 'sha256:25692848ea167ef57f3914a55393d48b7a96c201a0dcc2002e316bcd146ddd8c', 'local_current_version': '4.0.6-r0-ls272', 'remote_current_digest': 'sha256:25692848ea167ef57f3914a55393d48b7a96c201a0dcc2002e316bcd146ddd8c', 'remote_current_version': '4.0.6-r0-ls272', 'remote_latest_digest': 'sha256:25692848ea167ef57f3914a55393d48b7a96c201a0dcc2002e316bcd146ddd8c', 'remote_latest_version': '4.0.6-r0-ls272'}, 'portainer/agent:2.21.3': {'type': 'docker', 'local_current_digest': 'sha256:0298f083ae43930ae3cbc9cacafa89be6d5a3e2ab0aff5312a84712916e8d234', 'local_current_version': '-', 'remote_current_digest': 'sha256:0298f083ae43930ae3cbc9cacafa89be6d5a3e2ab0aff5312a84712916e8d234', 'remote_current_version': '-', 'remote_latest_digest': 'sha256:b87309640050c93433244b41513de186f9456e1024bfc9541bc9a8341c1b0938', 'remote_latest_version': '-'}}}
print('result = %s' % res)
influx_sender = InfluxDBSender()
influx_sender.send(res)

