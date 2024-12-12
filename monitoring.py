import sys
import subprocess
import os
import json
import requests
import time
from typing import Dict, List
import termios
import tty
from pathlib import Path


class Config:
    # -------------------------------------------------------------------------------------
    # General config
    # -------------------------------------------------------------------------------------
    CONFIG_FILE = './monitoring.json'
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

    def __init__(self, **entries):
        self.__dict__.update(entries)

config = Config()

def load_config():
    return read_json(config.CONFIG_FILE, vars(config))

def save_config():
    write_json(vars(config), config.CONFIG_FILE)

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


def is_file_exists(file_path):
    file = Path(file_path)
    return file.is_file()


def write_json(data, file_path):
    json_object = json.dumps(data, indent=4, ensure_ascii=False)
    with open(file_path, 'w') as outfile:
        outfile.write(json_object)


def read_json(file_path, default = None):
    if not default:
        default = {}
    if not is_file_exists(file_path):
        return default
    with open(file_path) as infile:
        try:
            return json.load(infile)
        except:
            return default

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
        if config.DEBUG_MODE:
            try:
                os.mkdir(config.MANIFESTS_FOLDER)
            except FileExistsError:
                pass
            except PermissionError:
                print(f"Permission denied: Unable to create '{config.MANIFESTS_FOLDER}'.")
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
        with open(f'{config.MANIFESTS_FOLDER}/{image_name.replace("/", "_")}_{prefix}.txt', 'w') as f:
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
        if config.DEBUG_MODE:
            self.__debug_write_manifest_info(image_name, 'current_local', manifest_res)
        manifests_json = json.loads(''.join(manifest_res))

        for manifest_json in manifests_json:
            if manifest_json.get('Architecture') == config.DOCKER_ARCHITECTURE:
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
        if config.DEBUG_MODE:
            self.__debug_write_manifest_info(image_name, 'remote_current', manifest_res)
        manifest_json = json.loads(''.join(manifest_res))

        response['current_remote'] = {
            'digest': dict_deep_get(manifest_json, ['manifest', 'digest']) or '-',
            'version': dict_deep_get(manifest_json, [
                'image', f'{config.DOCKER_OS}/{config.DOCKER_ARCHITECTURE}', 'config', 'Labels', 'org.opencontainers.image.version'
            ]) or '-',
        }

        if tag == 'latest':
            response['latest_remote'] = response['current_remote']
        else:
            # get info about latest version of image
            latest_manifest_res = self.__exec_command(
                self.Commands.docker_buildx_inspect.format(image_name=f'{image_name_without_tag}:latest')
            )
            if config.DEBUG_MODE:
                self.__debug_write_manifest_info(image_name, 'remote_latest', latest_manifest_res)
            latest_manifest_json = json.loads(''.join(latest_manifest_res))

            response['latest_remote'] = {
                'digest': dict_deep_get(latest_manifest_json, ['manifest', 'digest']) or '-',
                'version': dict_deep_get(latest_manifest_json, [
                    'image', f'{config.DOCKER_OS}/{config.DOCKER_ARCHITECTURE}', 'config', 'Labels', 'org.opencontainers.image.version'
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
            processors_labels = config.container_processors_mapping.get(container_id, [])
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
        self.host = config.INFLUX_HOST
        self.port = config.INFLUX_PORT
        self.org = config.INFLUX_ORG
        self.bucket = config.INFLUX_BUCKET
        self.token = config.INFLUX_TOKEN

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


class Terminal:
    KEY_EXEC = 'execute'
    KEY_DESC = 'description'
    KEY_SUBM = 'commands'

    COMMAND_HELP = 'help'
    COMMAND_PROC = 'process'
    COMMAND_SETTINGS = 'settings'
    COMMAND_CONFIG = 'update-config'
    COMMAND_MENU = 'menu'
    COMMAND_EXIT = 'exit'
    COMMAND_BACK = 'back'

    def __init__(self, args):
        self.action = (args[1:]+[None])[0]
        self.args = args[2:]
        self.commands = {
            self.COMMAND_HELP: {
                self.KEY_EXEC: self.command_help,
                self.KEY_DESC: 'Show help',
            },
            self.COMMAND_PROC: {
                self.KEY_EXEC: self.command_process,
                self.KEY_DESC: 'Run monitoring round',
            },
            self.COMMAND_SETTINGS: {
                self.KEY_EXEC: self.command_settings,
                self.KEY_DESC: 'Settings',
                self.KEY_SUBM: {
                    self.COMMAND_CONFIG: {
                        self.KEY_EXEC: self.command_update_config,
                        self.KEY_DESC: 'Update config',
                        self.KEY_SUBM: {
                            'INFLUX_HOST': {
                                self.KEY_EXEC: self.command_update_config_host,
                                self.KEY_DESC: '',
                            },
                            'INFLUX_PORT': {
                                self.KEY_EXEC: self.command_update_config_port,
                                self.KEY_DESC: '',
                            },
                            'INFLUX_ORG': {
                                self.KEY_EXEC: self.command_update_config_org,
                                self.KEY_DESC: '',
                            },
                            'INFLUX_BUCKET': {
                                self.KEY_EXEC: self.command_update_config_bucket,
                                self.KEY_DESC: '',
                            },
                            'INFLUX_TOKEN': {
                                self.KEY_EXEC: self.command_update_config_token,
                                self.KEY_DESC: '',
                            },
                            self.COMMAND_BACK: {
                                self.KEY_EXEC: self.command_menu,
                                self.KEY_DESC: 'Back to main menu',
                            },
                        },
                    },
                    'update-crone': {
                        self.KEY_EXEC: exit,
                        self.KEY_DESC: 'Update cron',
                    },
                    self.COMMAND_BACK: {
                        self.KEY_EXEC: self.command_menu,
                        self.KEY_DESC: 'Back to main menu',
                    },
                    self.COMMAND_EXIT: {
                        self.KEY_EXEC: exit,
                        self.KEY_DESC: 'Exit to terminal',
                    },
                },
            },
            self.COMMAND_EXIT: {
                self.KEY_EXEC: exit,
                self.KEY_DESC: 'Exit to terminal',
            },
        }
        if self.action:
            self.current_command = self.commands[self.action]
            self._run_command(self.current_command)
        else:
            self.command_menu()

    def _run_command(self, command):
        command[self.KEY_EXEC]()

    def _clear_console(self):
        os.system('cls' if os.name=='nt' else 'clear')
    
    def _getch(self):
        fd = sys.stdin.fileno()
        orig = termios.tcgetattr(fd)

        try:
            tty.setcbreak(fd)
            return sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSAFLUSH, orig)

    def _show_menu(self, commands, index):
        self._clear_console()
        print('Select action...')
        selected_command_key = None
        if index >= len(commands.keys()):
            index = len(commands.keys()) - 1
        if index < 0:
            index = 0
        for idx, command in enumerate(commands.keys()):
            if index == idx:
                selector = '[*]'
                selected_command_key = command
            else:
                selector = '[ ]'
            print(f"{selector} {command} {commands[command][self.KEY_DESC]:>{30 - len(command)}}")
        c = self._getch()
        # Up arrow key
        if c == 'A':
            index-=1
        # Down arrow key
        elif c == 'B':
            index+=1
        # Enter key
        elif c == "\n":
            return (index, commands[selected_command_key])
        return (index, None)

    def command_settings(self):
        sub_action = (self.args+[None])[0]
        if not sub_action:
            self.command_menu(self.commands[self.COMMAND_SETTINGS][self.KEY_SUBM])
        elif sub_action not in self.commands[self.COMMAND_SETTINGS][self.KEY_SUBM].keys():
            print(f'Error: wrong action {sub_action}')
        else:
            self._run_command(self.commands[self.COMMAND_SETTINGS][self.KEY_SUBM][sub_action])

    def command_update_config(self):
        items = self.commands[self.COMMAND_SETTINGS][self.KEY_SUBM][self.COMMAND_CONFIG][self.KEY_SUBM]
        for item in items:
            if item in config.__dict__.keys():
                items[item][self.KEY_DESC] = config.__dict__[item]
            elif item in Config.__dict__.keys():
                items[item][self.KEY_DESC] = Config.__dict__[item]
        self.command_menu(items)

    def command_update_config_host(self):
        self._clear_console()
        print(f'Current value: {config.INFLUX_HOST}')
        config.INFLUX_HOST = input("Enter Influx host: ")
        save_config()
        self.command_update_config()

    def command_update_config_port(self):
        self._clear_console()
        print(f'Current value: {config.INFLUX_PORT}')
        config.INFLUX_PORT = input("Enter Influx port: ")
        save_config()
        self.command_update_config()

    def command_update_config_org(self):
        self._clear_console()
        print(f'Current value: {config.INFLUX_ORG}')
        config.INFLUX_ORG = input("Enter Influx org: ")
        save_config()
        self.command_update_config()

    def command_update_config_bucket(self):
        self._clear_console()
        print(f'Current value: {config.INFLUX_BUCKET}')
        config.INFLUX_BUCKET = input("Enter Influx bucket: ")
        save_config()
        self.command_update_config()

    def command_update_config_token(self):
        self._clear_console()
        print(f'Current value: {config.INFLUX_TOKEN}')
        config.INFLUX_TOKEN = input("Enter Influx token: ")
        save_config()
        self.command_update_config()

    def command_menu(self, commands = None):
        if not commands:
            commands = self.commands
        index = 0
        selected_command = None
        while selected_command is None:
            (index, selected_command) = self._show_menu(commands, index)
        self._run_command(selected_command)

    def command_help(self):
        print('Commands:')
        for command in self.commands.keys():
            print(f"\t{command}\t{self.commands[command][self.KEY_DESC]}")

    def command_process(self):
        monitoring = PVEMonitoring()
        res = monitoring.process()
        # res = {'105': {'lscr.io/linuxserver/transmission:latest': {'type': 'docker', 'local_current_digest': 'sha256:25692848ea167ef57f3914a55393d48b7a96c201a0dcc2002e316bcd146ddd8c', 'local_current_version': '4.0.6-r0-ls272', 'remote_current_digest': 'sha256:25692848ea167ef57f3914a55393d48b7a96c201a0dcc2002e316bcd146ddd8c', 'remote_current_version': '4.0.6-r0-ls272', 'remote_latest_digest': 'sha256:25692848ea167ef57f3914a55393d48b7a96c201a0dcc2002e316bcd146ddd8c', 'remote_latest_version': '4.0.6-r0-ls272'}, 'portainer/agent:2.21.3': {'type': 'docker', 'local_current_digest': 'sha256:0298f083ae43930ae3cbc9cacafa89be6d5a3e2ab0aff5312a84712916e8d234', 'local_current_version': '-', 'remote_current_digest': 'sha256:0298f083ae43930ae3cbc9cacafa89be6d5a3e2ab0aff5312a84712916e8d234', 'remote_current_version': '-', 'remote_latest_digest': 'sha256:b87309640050c93433244b41513de186f9456e1024bfc9541bc9a8341c1b0938', 'remote_latest_version': '-'}}}
        print('result = %s' % res)
        influx_sender = InfluxDBSender()
        influx_sender.send(res)

if __name__ == '__main__':
    config = Config(**load_config())
    Terminal(sys.argv)
