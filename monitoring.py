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
    INFLUX_HOST = 'http://127.0.0.1'
    INFLUX_PORT = '8086'
    INFLUX_ORG = 'home'
    INFLUX_BUCKET = 'pve_updates'
    INFLUX_TOKEN = ''
    # -------------------------------------------------------------------------------------

    container_processors_mapping = {
        '101': ['docker'],
        '122': ['docker'],
    }

    def __init__(self, **entries):
        self.__dict__.update(entries)

    def convert(self, value, type=str):
        if type is bool:
            return True if str(value).lower() in ['true', '1', 't', 'y', 'yes', 'yeah', 'yup', 'certainly', 'uh-huh'] else False
        return type(value)

    def get(self, item, type=str):
        value = None
        if item in self.__dict__.keys():
            value = self.__dict__[item]
        elif item in Config.__dict__.keys():
            value = Config.__dict__[item]
        return self.convert(value, type) if value is not None else None

    def set(self, item, value, type=str):
        self.__dict__.update({item: self.convert(value, type)})

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
    print(data)
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
        try:
            manifest_json = json.loads(''.join(manifest_res))
        except:
            manifest_json = json.loads('{}')

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
            try:
                latest_manifest_json = json.loads(''.join(latest_manifest_res))
            except:
                latest_manifest_json = json.loads('{}')

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
        get_container_name = "pct config {container_id} | awk '/hostname/ {{print $2}}'"
        get_container_status = "pct status {container_id} | awk '/status/ {{print $2}}'"
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

    def get_containers(self):
        containers_ids = self.__exec_command(self.Commands.get_containers_ids)
        containers = [{
            'id': cid,
            'name': self.__exec_command(self.Commands.get_container_name.format(container_id=cid))[0],
            'state': self.__exec_command(self.Commands.get_container_status.format(container_id=cid))[0],
        } for cid in containers_ids if self._check_container_is_template(cid) != 'true']
        return containers

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

    def _escape(self, value):
        if len(value) == 0:
            return '-'
        return value.replace(' ', '\ ').replace('=', '\=').replace(',', '\,')

    def _prepare_data(self, monitoring_info):
        data_raws = []
        data_raw_template = 'updates,container_id={container_id},instance_type={instance_type},instance_name={instance_name},local_current_digest={local_current_digest},local_current_version={local_current_version},remote_current_digest={remote_current_digest},remote_current_version={remote_current_version},remote_latest_digest={remote_latest_digest},remote_latest_version={remote_latest_version} value=1 {current_unix_time}'
        for container_id, container_data in monitoring_info.items():
            for instance_name, instance_data in container_data.items():
                data_raws.append(
                    data_raw_template.format(
                        container_id=self._escape(container_id),
                        instance_type=self._escape(instance_data.get('type')),
                        instance_name=self._escape(instance_name),
                        local_current_digest=self._escape(instance_data.get('local_current_digest')),
                        local_current_version=self._escape(instance_data.get('local_current_version')),
                        remote_current_digest=self._escape(instance_data.get('remote_current_digest')),
                        remote_current_version=self._escape(instance_data.get('remote_current_version')),
                        remote_latest_digest=self._escape(instance_data.get('remote_latest_digest')),
                        remote_latest_version=self._escape(instance_data.get('remote_latest_version')),
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
    KEY_TYPE = 'type'
    KEY_PARENT = 'parent'

    COMMAND_HELP = 'help'
    COMMAND_PROC = 'process'
    COMMAND_SETTINGS = 'settings'
    COMMAND_CONFIG = 'update-influx'
    COMMAND_CONFIG_LXC = 'update-lxc'
    COMMAND_CONFIG_LXC_ID = 'id'
    COMMAND_CONFIG_LXC_NAME = 'name'
    COMMAND_CONFIG_LXC_STATUS = 'status'
    COMMAND_CONFIG_LXC_STATUS_MISSING = 'Missing'
    COMMAND_CONFIG_LXC_PROCESSORS = 'processors'
    COMMAND_CONFIG_DELETE_COMMAND = 'delete'
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
                self.KEY_EXEC: self.command_show_submenu,
                self.KEY_DESC: 'Settings',
                self.KEY_SUBM: {
                    self.COMMAND_CONFIG: {
                        self.KEY_EXEC: self.command_update_config,
                        self.KEY_DESC: 'Update INFLUX config',
                        self.KEY_SUBM: {
                            'INFLUX_HOST': {
                                self.KEY_EXEC: lambda c: self.command_update_config_item(c, 'INFLUX_HOST'),
                                self.KEY_DESC: '',
                            },
                            'INFLUX_PORT': {
                                self.KEY_EXEC: lambda c: self.command_update_config_item(c, 'INFLUX_PORT'),
                                self.KEY_DESC: '',
                            },
                            'INFLUX_ORG': {
                                self.KEY_EXEC: lambda c: self.command_update_config_item(c, 'INFLUX_ORG'),
                                self.KEY_DESC: '',
                            },
                            'INFLUX_BUCKET': {
                                self.KEY_EXEC: lambda c: self.command_update_config_item(c, 'INFLUX_BUCKET'),
                                self.KEY_DESC: '',
                            },
                            'INFLUX_TOKEN': {
                                self.KEY_EXEC: lambda c: self.command_update_config_item(c, 'INFLUX_TOKEN'),
                                self.KEY_DESC: '',
                            },
                            self.COMMAND_BACK: {
                                self.KEY_EXEC: self.command_back,
                                self.KEY_DESC: 'Back',
                            },
                        },
                    },
                    self.COMMAND_CONFIG_LXC: {
                        self.KEY_EXEC: self.command_update_containers,
                        self.KEY_DESC: 'Update containers mapping',
                    },
                    'update-crone': {
                        self.KEY_EXEC: self.command_exit,
                        self.KEY_DESC: '(TODO) Update cron',
                    },
                    'update-general': {
                        self.KEY_EXEC: self.command_update_config,
                        self.KEY_DESC: 'Update General config',
                        self.KEY_SUBM: {
                            'CONFIG_FILE': {
                                self.KEY_EXEC: lambda c: self.command_update_config_item(c, 'CONFIG_FILE'),
                                self.KEY_DESC: '',
                            },
                            'DEBUG_MODE': {
                                self.KEY_EXEC: lambda c: self.command_update_config_item(c, 'DEBUG_MODE'),
                                self.KEY_DESC: '',
                                self.KEY_TYPE: bool,
                            },
                            'MANIFESTS_FOLDER': {
                                self.KEY_EXEC: lambda c: self.command_update_config_item(c, 'MANIFESTS_FOLDER'),
                                self.KEY_DESC: '',
                            },
                            'USE_CACHE': {
                                self.KEY_EXEC: lambda c: self.command_update_config_item(c, 'USE_CACHE'),
                                self.KEY_DESC: '',
                                self.KEY_TYPE: bool,
                            },
                            self.COMMAND_BACK: {
                                self.KEY_EXEC: self.command_back,
                                self.KEY_DESC: 'Back',
                            },
                        },
                    },
                    'update-docker': {
                        self.KEY_EXEC: self.command_update_config,
                        self.KEY_DESC: 'Update Docker Processor config',
                        self.KEY_SUBM: {
                            'DOCKER_ARCHITECTURE': {
                                self.KEY_EXEC: lambda c: self.command_update_config_item(c, 'DOCKER_ARCHITECTURE'),
                            },
                            'DOCKER_OS': {
                                self.KEY_EXEC: lambda c: self.command_update_config_item(c, 'DOCKER_OS'),
                            },
                            self.COMMAND_BACK: {
                                self.KEY_EXEC: self.command_back,
                                self.KEY_DESC: 'Back',
                            },
                        },
                    },
                    self.COMMAND_BACK: {
                        self.KEY_EXEC: self.command_back,
                        self.KEY_DESC: 'Back',
                    },
                },
            },
            self.COMMAND_EXIT: {
                self.KEY_EXEC: self.command_exit,
                self.KEY_DESC: 'Exit to terminal',
            },
        }
        def set_parent_command(parent_command, commands):
            for key in commands.keys():
                command = commands[key]
                command[self.KEY_PARENT] = parent_command
                if self.KEY_SUBM in command.keys():
                    set_parent_command(command, command[self.KEY_SUBM])
        set_parent_command(None, self.commands)
        if self.action:
            self.current_command = self.commands[self.action]
            self._run_command(self.current_command)
        else:
            self.command_menu(None)

    def _run_command(self, command):
        command[self.KEY_EXEC](command)

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

    def _show_menu(self, commands, index, message):
        self._clear_console()
        print(message)
        selected_command_key = None
        if index >= len(commands.keys()):
            index = 0
        if index < 0:
            index = len(commands.keys()) - 1
        for idx, command in enumerate(commands.keys()):
            if index == idx:
                selected_command_key = command
                print(f"\033[96m[*] {command} {commands[command][self.KEY_DESC]:>{50 - len(command)}}\033[00m")
            else:
                print(f"[ ] {command} {commands[command][self.KEY_DESC]:>{50 - len(command)}}")
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

    def command_update_containers(self, command):
        if self.KEY_SUBM not in command.keys():
            print('Updating PVE containers list. Please wait...')

            # Get containers from config
            containers_from_config = config.container_processors_mapping;

            # Get containers from PVE
            monitoring = PVEMonitoring()
            containers_from_pve = monitoring.get_containers()

            commands = {}
            # Add containers from config
            for key in containers_from_config.keys():
                commands[key] = {
                    self.COMMAND_CONFIG_LXC_PROCESSORS: containers_from_config[key],
                    self.COMMAND_CONFIG_LXC_NAME: '',
                    self.COMMAND_CONFIG_LXC_STATUS: self.COMMAND_CONFIG_LXC_STATUS_MISSING,
                    self.COMMAND_CONFIG_LXC_ID: key,
                }

            # Upsert containers from PVE
            for container in containers_from_pve:
                container_id = container['id']
                container_name = container['name']
                container_state = container['state']
                c = commands.get(container_id, {
                    self.COMMAND_CONFIG_LXC_PROCESSORS: [],
                    self.COMMAND_CONFIG_LXC_NAME: container_name,
                    self.COMMAND_CONFIG_LXC_STATUS: container_state,
                    self.COMMAND_CONFIG_LXC_ID: container_id,
                })
                c[self.COMMAND_CONFIG_LXC_NAME] = container_name
                c[self.COMMAND_CONFIG_LXC_STATUS] = container_state
                commands[container_id] = c

            commands[self.COMMAND_BACK] = {
                self.KEY_EXEC: self.command_menu,
                self.KEY_DESC: 'Back',
            }

            command[self.KEY_SUBM] = commands

        for key in command[self.KEY_SUBM].keys():
            c = command[self.KEY_SUBM][key]
            if self.COMMAND_CONFIG_LXC_PROCESSORS in c.keys():
                procs = str(c[self.COMMAND_CONFIG_LXC_PROCESSORS])
                name = c[self.COMMAND_CONFIG_LXC_NAME]
                state = c[self.COMMAND_CONFIG_LXC_STATUS]
                c[self.KEY_DESC] = f'{name} ({state}) {procs}'
                c[self.KEY_EXEC] = self.command_container_select_processors
        
        self.command_menu(command, command[self.KEY_SUBM], 'Select container to select processors')

    def command_container_select_processors(self, command):
        container_id = command[self.COMMAND_CONFIG_LXC_ID]
        commands = {}
        for processor in processors_mapping.keys():
            is_enabled = ' '
            if processor in command[self.COMMAND_CONFIG_LXC_PROCESSORS]:
                is_enabled = 'V'
            commands[processor] = {
                self.KEY_EXEC: lambda x: self.command_container_processor_action(command, container_id, processor),
                self.KEY_DESC: f'[{is_enabled}] processor',
            }
        commands[self.COMMAND_BACK] = {
            self.KEY_EXEC: lambda x: self.command_update_containers(self.commands[self.COMMAND_SETTINGS][self.KEY_SUBM][self.COMMAND_CONFIG_LXC]),
            self.KEY_DESC: 'Back',
        }
        if command[self.COMMAND_CONFIG_LXC_STATUS] == self.COMMAND_CONFIG_LXC_STATUS_MISSING:
            commands[self.COMMAND_CONFIG_DELETE_COMMAND] = {
                self.KEY_EXEC: lambda x: self.command_container_delete(command, container_id),
                self.KEY_DESC: f'Delete {container_id}',
            }
        self.command_menu(command, commands, 'Select processors')

    def command_container_processor_action(self, command, container_id, processor):
        if container_id not in config.container_processors_mapping.keys():
            config.container_processors_mapping[container_id] = []
        if processor in config.container_processors_mapping[container_id]:
            config.container_processors_mapping[container_id].remove(processor)
        else:
            config.container_processors_mapping[container_id].append(processor)
        command[self.COMMAND_CONFIG_LXC_PROCESSORS] = config.container_processors_mapping[container_id]
        config.set('container_processors_mapping', config.container_processors_mapping, dict)
        save_config()
        self.command_container_select_processors(command)

    def command_container_delete(self, command, container_id):
        self.commands[self.COMMAND_SETTINGS][self.KEY_SUBM][self.COMMAND_CONFIG_LXC][self.KEY_SUBM].pop(container_id, None)
        config.container_processors_mapping.pop(container_id, None)
        config.set('container_processors_mapping', config.container_processors_mapping, dict)
        save_config()
        self.command_update_containers(self.commands[self.COMMAND_SETTINGS][self.KEY_SUBM][self.COMMAND_CONFIG_LXC])

    def command_show_submenu(self, command):
        sub_action = (self.args+[None])[0]
        if not sub_action:
            self.command_menu(command, command[self.KEY_SUBM])
        elif sub_action not in command[self.KEY_SUBM].keys():
            print(f'Error: wrong action {sub_action}')
        else:
            sub_command = command[self.KEY_SUBM][sub_action]
            self._run_command(sub_command)

    def command_update_config(self, command):
        items = command[self.KEY_SUBM]
        for item in items:
            type = items[item].get(self.KEY_TYPE, str)
            value = config.get(item, type)
            print(item, type, value)
            if value is not None:
                items[item][self.KEY_DESC] = str(value)
        self.command_menu(command, items, 'Select value to change')

    def command_back(self, command):
        parent_command_1 = command[self.KEY_PARENT]
        if parent_command_1 is None: self.command_menu(None)
        parent_command_2 = parent_command_1[self.KEY_PARENT]
        if parent_command_2 is None: self.command_menu(None)
        self._run_command(parent_command_2)

    def command_exit(self, command):
        exit(0)

    def command_update_config_item(self, command, item):
        type = command.get(self.KEY_TYPE, str)
        self._clear_console()
        print(f'Current value {item}: {type(config.get(item, type))}')
        value = input('Enter new value: ')
        config.set(item, value, type)
        save_config()
        self._run_command(command[self.KEY_PARENT])

    def command_menu(self, command, commands = None, message='Select action...'):
        if not commands:
            commands = self.commands
        index = 0
        selected_command = None
        while selected_command is None:
            (index, selected_command) = self._show_menu(commands, index, message)
        self._run_command(selected_command)

    def command_help(self, command):
        print('Commands:')
        for command in self.commands.keys():
            print(f"\t{command}\t{self.commands[command][self.KEY_DESC]}")

    def command_process(self, command):
        # TODO: add process arguments for DEBUG and CACHE
        monitoring = PVEMonitoring()
        res = monitoring.process()
        # res = {'105': {'lscr.io/linuxserver/transmission:latest': {'type': 'docker', 'local_current_digest': 'sha256:25692848ea167ef57f3914a55393d48b7a96c201a0dcc2002e316bcd146ddd8c', 'local_current_version': '4.0.6-r0-ls272', 'remote_current_digest': 'sha256:25692848ea167ef57f3914a55393d48b7a96c201a0dcc2002e316bcd146ddd8c', 'remote_current_version': '4.0.6-r0-ls272', 'remote_latest_digest': 'sha256:25692848ea167ef57f3914a55393d48b7a96c201a0dcc2002e316bcd146ddd8c', 'remote_latest_version': '4.0.6-r0-ls272'}, 'portainer/agent:2.21.3': {'type': 'docker', 'local_current_digest': 'sha256:0298f083ae43930ae3cbc9cacafa89be6d5a3e2ab0aff5312a84712916e8d234', 'local_current_version': '-', 'remote_current_digest': 'sha256:0298f083ae43930ae3cbc9cacafa89be6d5a3e2ab0aff5312a84712916e8d234', 'remote_current_version': '-', 'remote_latest_digest': 'sha256:b87309640050c93433244b41513de186f9456e1024bfc9541bc9a8341c1b0938', 'remote_latest_version': '-'}}}
        print('result = %s' % res)
        influx_sender = InfluxDBSender()
        influx_sender.send(res)

if __name__ == '__main__':
    config = Config(**load_config())
    Terminal(sys.argv)
