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
import collections
from datetime import datetime, timedelta
import logging


class Config:
    # -------------------------------------------------------------------------------------
    # General config
    # -------------------------------------------------------------------------------------
    CONFIG_FILE = './monitoring.json'
    DEBUG_MODE = True
    MANIFESTS_FOLDER = 'manifests'
    CACHE_FILE = './cache.json'
    USE_CACHE = True
    CACHE_TTL = 23 * 60 * 60  # in seconds
    LOG_FILE = './log.txt'
    LOGGER_LOG_LEVEL = 'debug'
    LOGGER_TERMINAL_LEVEL = 'info'
    # -------------------------------------------------------------------------------------
    # Docker manifest config
    # -------------------------------------------------------------------------------------
    DOCKER_ARCHITECTURE = 'amd64'
    DOCKER_OS = 'linux'
    DOCKER_REGISTRY_HUBS = 'lscr.io,ghcr.io'
    DOCKER_HUB_SEARCH_VERSION_URL = 'https://hub.docker.com/v2/repositories/{image_name}/tags?page_size=100&page=1&ordering=last_updated'
    # -------------------------------------------------------------------------------------
    # Influx config
    # -------------------------------------------------------------------------------------
    INFLUX_HOST = 'http://127.0.0.1'
    INFLUX_PORT = '8086'
    INFLUX_ORG = 'home'
    INFLUX_BUCKET = 'pve_updates'
    INFLUX_TOKEN = ''
    # -------------------------------------------------------------------------------------
    # Variables
    CONTAINER_PROCESSORS_MAPPING = {}
    LOG_LEVEL_MAPPER = {
        'critical': logging.CRITICAL,
        'fatal': logging.FATAL,
        'error': logging.ERROR,
        'warning': logging.WARNING,
        'info': logging.INFO,
        'debug': logging.DEBUG,
        'notset': logging.NOTSET,
    }
    # -------------------------------------------------------------------------------------

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

logging_format = '%(asctime)s %(name)s %(levelname)s %(message)s'
logging.basicConfig(
    filename=config.LOG_FILE,
    filemode='a',
    format=logging_format,
    datefmt='%H:%M:%S',
    level=logging.DEBUG
)
logger = logging.getLogger()
logger.setLevel(config.LOG_LEVEL_MAPPER.get(config.LOGGER_LOG_LEVEL, logging.DEBUG))
handler = logging.StreamHandler(sys.stdout)
handler.setLevel(config.LOG_LEVEL_MAPPER.get(config.LOGGER_TERMINAL_LEVEL, logging.INFO))
handler.setFormatter(logging.Formatter(logging_format))
logger.addHandler(handler)


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


def read_json(file_path, default=None):
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

    def __load_cache(self):
        logger.info('Trying to load cache from file...')
        try:
            with open(Config.CACHE_FILE, 'r') as infile:
                logger.info('Cache was successfully loaded')
                return json.load(infile)
        except FileNotFoundError:
            logger.error('Something wrong during the loading cache')
            return {}

    def __write_cache(self):
        logger.info('Write cache to file...')
        json_object = json.dumps(self.cache, indent=4, ensure_ascii=False)
        with open(Config.CACHE_FILE, 'w') as outfile:
            outfile.write(json_object)

    def __init__(self, container_id):
        self.container_id = container_id
        self.type = 'docker'
        self.cache = self.__load_cache() if config.USE_CACHE else {}
        self.registry_hubs_non_defaults = config.DOCKER_REGISTRY_HUBS.split(',')
        if config.DEBUG_MODE:
            try:
                os.mkdir(config.MANIFESTS_FOLDER)
            except FileExistsError:
                pass
            except PermissionError:
                logger.error(f"Permission denied: Unable to create '{config.MANIFESTS_FOLDER}'.")
            except Exception as e:
                logger.error(f"An error occurred: {e}")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.__write_cache()

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

    def _get_from_cache(self, image_name, prefix):
        manifest = dict_deep_get(self.cache, [image_name, prefix, 'manifest'])
        updated_date = dict_deep_get(self.cache, [image_name, prefix, 'updated_date'])
        if (
            updated_date and
            datetime.utcnow() < datetime.fromisoformat(updated_date) + timedelta(seconds=Config.CACHE_TTL)
        ):
            logger.info(f'Got manifest from cache for image_name "{image_name}" with prefix "{prefix}"')
            return manifest, True
        logger.info(f'There is no cache or cache outdated for image_name "{image_name}" with prefix "{prefix}"')
        return None, False

    def _add_to_cache(self, image_name, prefix, manifest):
        if image_name not in self.cache:
            self.cache[image_name] = {}
        self.cache[image_name].update({
            prefix: {
                'manifest': manifest,
                'updated_date': datetime.utcnow().isoformat()
            }
        })

    def _get_manifest(self, image_name, prefix, command):
        if config.USE_CACHE:
            manifest, loaded_from_cache = self._get_from_cache(image_name, prefix)
            if loaded_from_cache:
                return manifest
        manifest_res = self.__exec_command(command)
        if config.DEBUG_MODE:
            self.__debug_write_manifest_info(image_name, prefix, manifest_res)
        try:
            manifest = json.loads(''.join(manifest_res))
        except:
            manifest = json.loads('{}')
        self._add_to_cache(image_name, prefix, manifest)
        return manifest

    def _get_local_docker_image_digest(self, image_name):
        logger.info('Getting info from local manifest')
        prefix = 'current_local'
        digest = '-'
        version = ''
        # parse image name
        tag = image_name.split(':')[-1] if len(image_name.split(':')) > 1 else ''

        get_manifest_command = self.Commands.docker_inspect.format(image_name=image_name)
        manifests_json = self._get_manifest(image_name, prefix, get_manifest_command)

        for manifest_json in manifests_json:
            if manifest_json.get('Architecture') == config.DOCKER_ARCHITECTURE:
                repo_digest = manifest_json.get('RepoDigests')
                if len(repo_digest) > 0:
                    digest = repo_digest[0].split('@')[-1]
                version = dict_deep_get(manifest_json, ['Config', 'Labels', 'org.opencontainers.image.version']) or ''
        return {
            'current_local': {
                'digest': digest,
                'version': version or (tag if tag and tag != 'latest' else '-'),
            }
        }

    def _search_version_on_docker_hub(self, image_name, digest):
        logger.info(f'Searching for version for image "{image_name}" on docker hub')
        url = config.DOCKER_HUB_SEARCH_VERSION_URL.format(image_name=image_name)
        version = ''
        try:
            response = requests.get(url)
            response.raise_for_status()
            json_data = response.json()
            list_image_info = list(filter(
                lambda x: x['digest'] == digest and x['name'] != 'latest', json_data.get('results', []))
            )
            if len(list_image_info):
                # try to find version with digits
                versions = list(filter(lambda x: any(char.isdigit() for char in x['name']), list_image_info))
                # get version from list of versions if we found some version with digits else get first version from list
                version = versions[0]['name'] if versions else list_image_info[0]['name']
            logger.info('Version was successfully found')
        except Exception as e:
            logger.error(f'Something wrong during getting image info on docker hub. Error = {e}')
        return version

    def _get_remote_docker_image_digest(self, image_name):
        logger.info('Getting info from remote manifest')
        response = {}
        # parse image name
        tag = image_name.split(':')[-1] if len(image_name.split(':')) > 1 else ''
        image_name_without_tag = image_name.split(':')[0]

        # get current remote info
        manifest_json = self._get_manifest(
            image_name,
            'remote_current',
            self.Commands.docker_buildx_inspect.format(image_name=image_name)
        )

        digest = dict_deep_get(manifest_json, ['manifest', 'digest']) or ''
        version = dict_deep_get(
            manifest_json,
            ['image', f'{config.DOCKER_OS}/{config.DOCKER_ARCHITECTURE}', 'config', 'Labels', 'org.opencontainers.image.version']
        ) or (tag if tag and tag != 'latest' else '')

        if digest and not version and not any([i in image_name_without_tag for i in self.registry_hubs_non_defaults]):
            version = self._search_version_on_docker_hub(image_name_without_tag, digest)

        response['current_remote'] = {
            'digest': digest or '-',
            'version': version or '-',
        }

        if tag == 'latest':
            response['latest_remote'] = response['current_remote']
        else:
            # get info about latest version of image
            latest_manifest_json = self._get_manifest(
                image_name,
                'remote_latest',
                self.Commands.docker_buildx_inspect.format(image_name=f'{image_name_without_tag}:latest')
            )

            digest = dict_deep_get(latest_manifest_json, ['manifest', 'digest']) or ''
            version = dict_deep_get(
                latest_manifest_json,
                ['image', f'{config.DOCKER_OS}/{config.DOCKER_ARCHITECTURE}', 'config', 'Labels', 'org.opencontainers.image.version']
            ) or ''

            if digest and not version and not any([i in image_name_without_tag for i in self.registry_hubs_non_defaults]):
                version = self._search_version_on_docker_hub(image_name_without_tag, digest)

            response['latest_remote'] = {
                'digest': digest or '-',
                'version': version or '-',
            }
        return response

    def process(self):
        images = self._get_images()
        images_updates_info = {}
        for image_name in images:
            logger.info('*' * 50)
            logger.info(f'[{self.container_id}] {image_name}')
            local_repo_digest_info = self._get_local_docker_image_digest(image_name)
            logger.debug('local_repo_digest = %s' % local_repo_digest_info)
            remote_repo_digest_info = self._get_remote_docker_image_digest(image_name)
            logger.debug('remote_repo_digest = %s' % remote_repo_digest_info)

            images_updates_info[image_name] = {
                'type': self.type,
                'local_current_digest': local_repo_digest_info['current_local']['digest'],
                'local_current_version': local_repo_digest_info['current_local']['version'],
                'remote_current_digest': remote_repo_digest_info['current_remote']['digest'],
                'remote_current_version': remote_repo_digest_info['current_remote']['version'],
                'remote_latest_digest': remote_repo_digest_info['latest_remote']['digest'],
                'remote_latest_version': remote_repo_digest_info['latest_remote']['version'],
            }
            logger.info('Manifests info successfully collected')
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
        logger.info('Get containers ids...')
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
        logger.info('Checking updates...')
        containers_updates_info = {}
        containers_ids = self._get_containers_ids()
        logger.info(f'Got containers = {containers_ids}')
        for container_id in containers_ids:
            processors_labels = config.CONTAINER_PROCESSORS_MAPPING.get(container_id, [])
            for processor_label in processors_labels:
                processor = processors_mapping.get(processor_label)
                if not processor:
                    continue
                logger.info('-' * 100)
                logger.info(f'Trying to get updates using processor "{processor_label}" for container id = {container_id}')
                logger.info('-' * 100)
                with processor(container_id) as proc:
                    images_updates_info = proc.process()
                    containers_updates_info[container_id] = images_updates_info
        logger.info('-' * 100)
        logger.info(f'Updating info successfully collected')
        logger.info('-' * 100)
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
        return value.replace(' ', '\\ ').replace('=', '\\=').replace(',', '\\,')

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
        logger.info('Starting sending updating info to InfluxDB')
        url = f'{self.host}:{self.port}/api/v2/write?org={self.org}&bucket={self.bucket}&precision=ns'
        data = self._prepare_data(monitoring_info)
        logger.debug(data)
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
            logger.info('Successfully sent updating info to InfluxDB')
        except Exception as e:
            logger.error(f'Something wrong during sending updating info to InfluxDB. Error = {e}')


class Terminal:
    COMMAND_BACK = 'back'

    class Action:
        KEY_EXEC = 'execute'
        KEY_DESC = 'description'
        KEY_HELP = 'help'

        def __init__(self, command = None, parent = None, terminal = None, **kwargs):
            self.command = command
            self.parent = parent
            self.terminal = terminal
            self.kwargs = kwargs

        def _get_screen_width(self):
            return 96

        def get_parent(self):
            return self.parent if self.parent is not None else self

        def _get_by_key(self, key, default = None):
            return self.kwargs[key] if key in self.kwargs.keys() else default

        def get_command(self):
            return self.command

        def get_description(self):
            return self._get_by_key(Terminal.Action.KEY_DESC, '')

        def get_help(self):
            return self._get_by_key(Terminal.Action.KEY_HELP, None)

        def print(self, *args, **kwargs):
            print(*args, **kwargs)

        def show(self):
            self.print(self.get_description())

        def clear(self):
            os.system('cls' if os.name=='nt' else 'clear')

        def run(self, args):
            self.print('Args:', args)
            pass

        def help(self, shift=0):
            command = self.get_command()
            width = self._get_screen_width()
            help = self.get_help()
            description = self.get_description() if help is None else help
            if command is None:
                self.print(f"{description}")
            else:
                first_part_len = shift + len(command)
                second_part_len = width - first_part_len
                self.print(f"{command:>{first_part_len}} {description:>{second_part_len}}")

        def get_sub_action(self, action):
            return None

        def _create_action(self, command, config):
            action_class = config.get(Terminal.Action.KEY_EXEC, Terminal.Action)
            return action_class(
                command=command,
                parent = self,
                terminal = self.terminal,
                **config
            )

    class ActionMenu(Action):
        KEY_SUBM = 'commands'

        ARROW_UP_KEY = 'A'
        ARROW_DOWN_KEY = 'B'
        ENTER_KEY = '\n'

        def __init__(self, *args, **kwargs):
            Terminal.Action.__init__(self, *args, **kwargs)
            self.menu_index = 0

        def _get_sub_actions(self):
            actions_config = self._get_by_key(Terminal.ActionMenu.KEY_SUBM, [])
            actions = []
            for key in actions_config:
                action_config = actions_config[key]
                action = self._create_action(key, action_config)
                actions.append(action)
            return actions

        def run(self, args):
            self.print('Args:', args)
            self.actions = self._get_sub_actions()
            action = None
            while action is None:
                (action, self.menu_index) = self._show_sub_menu(self.menu_index)
            return action

        def _apply_limits_for_index(self, index):
            if index < 0:
                return len(self.actions) - 1
            elif index >= len(self.actions):
                return 0
            else:
                return index

        def _show_sub_menu(self, current_index = 0):
            current_index = self._apply_limits_for_index(current_index)
            self.clear()
            self.show()
            active_command = None
            for index, action in enumerate(self.actions):
                command = action.get_command()
                description = action.get_description()
                self._print_sub_menu(index == current_index, command, description)
                active_command = action if index == current_index else active_command
            c = self._get_keypress()
            if c == self.ARROW_UP_KEY:
                return (None, current_index-1)
            elif c == self.ARROW_DOWN_KEY:
                return (None, current_index+1)
            elif c == self.ENTER_KEY:
                return (active_command, current_index)
            else:
                return (None, current_index)

        def _print_sub_menu(self, current, command, description):
            width = self._get_screen_width()
            line = command
            if isinstance(description, str) and description:
                line = f"{command} {' ' + description:.>{width - len(command)}}"
            if current:
                self.print(f"\033[96m[*] {line}\033[00m")
            else:
                self.print(f"[ ] {line}")

        def _get_keypress(self):
            fd = sys.stdin.fileno()
            orig = termios.tcgetattr(fd)

            try:
                tty.setcbreak(fd)
                return sys.stdin.read(1)
            finally:
                termios.tcsetattr(fd, termios.TCSAFLUSH, orig)

        def get_sub_action(self, action):
            sub_commands = self.kwargs.get(Terminal.ActionMenu.KEY_SUBM, {})
            command = sub_commands.get(action, None)
            if command is None:
                return None
            return self._create_action(action, command)

        def help(self, shift=0):
            Terminal.Action.help(self, shift)
            sub_commands = self.kwargs.get(Terminal.ActionMenu.KEY_SUBM, {})
            for command in sub_commands.keys():
                if command is not Terminal.COMMAND_BACK:
                    action = self._create_action(command, sub_commands[command])
                    action.help(shift=shift+2)

    class ActionBack(Action):

        def run(self, args):
            self.print('Args:', args)
            parent = self.get_parent()
            parent = parent.get_parent() if parent is not None else parent
            return parent

    class ActionProcess(Action):

        def get_description(self):
            return 'Run monitoring round'

        def run(self, args):
            self.print('Args:', args)
            # TODO: add process arguments for DEBUG and CACHE
            # TODO: add progress spinner
            monitoring = PVEMonitoring()
            res = monitoring.process()
            influx_sender = InfluxDBSender()
            influx_sender.send(res)

    class ActionUpdateConfig(Action):
        KEY_TYPE = 'type'

        def get_type(self):
            return self._get_by_key(Terminal.ActionUpdateConfig.KEY_TYPE, str)

        def get_description(self):
            type = self.get_type()
            value = config.get(self.get_command(), type)
            return str(value)

        def run(self, args):
            self.print('Args:', args)
            type = self.get_type()
            key = self.get_command()
            self.clear()
            self.print(f'Current value {key}: {self.get_description()}')
            value = input('Enter new value: ')
            if len(str(value)) != 0:
                config.set(key, value, type)
                save_config()
            return self.get_parent()

    class ActionHelp(Action):

        def get_description(self):
            return 'Show help'

        def run(self, args):
            self.print('Args:', args)
            action = self._create_action(None, self.terminal.commands)
            action.help()

    class ActionExit(Action):

        def get_description(self):
            return 'Exit to terminal'

        def run(self, args):
            self.print('Args:', args)
            exit()

    class ActionUpdateContainerProcessorsItemSelector(Action):

        def get_description(self):
            processor = self.get_command()
            container = self.get_parent().get_command()
            processors = config.CONTAINER_PROCESSORS_MAPPING.get(container, [])
            if processor in processors:
                return '(V)'
            else:
                return '( )'

        def run(self, args):
            self.print('Args:', args)
            processor = self.get_command()
            container = self.get_parent().get_command()
            mapping = config.get('CONTAINER_PROCESSORS_MAPPING', dict)
            processors = mapping.get(container, [])
            if processor in processors:
                processors.remove(processor)
            else:
                processors.append(processor)
            mapping[container] = processors
            config.set('CONTAINER_PROCESSORS_MAPPING', mapping, dict)
            save_config()
            return self.get_parent()

    class ActionUpdateContainerProcessorsItemDelete(Action):

        def get_description(self):
            return 'Delete container configuretion'

        def run(self, args):
            self.print('Args:', args)
            container = self.get_parent().get_command()
            config.CONTAINER_PROCESSORS_MAPPING.pop(container, None)
            save_config()
            return self.get_parent().get_parent()

    class ActionUpdateContainerProcessorsItem(ActionMenu):

        def get_description(self):
            procs = self._get_by_key(Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_PROCESSORS, '')
            name = self._get_by_key(Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_NAME, '-')
            state = self._get_by_key(Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_STATUS, '')
            length = round(self._get_screen_width() / 2)
            return f'{name} ({state}) {" " + str(procs):.>{length}}'

        def _get_from_config(self, commands):
            for processor in processors_mapping.keys():
                commands[processor] = {
                    Terminal.Action.KEY_EXEC: Terminal.ActionUpdateContainerProcessorsItemSelector,
                }
            return commands

        def _get_by_key(self, key, default = None):
            values = Terminal.ActionMenu._get_by_key(self, key, default)
            if key == Terminal.ActionMenu.KEY_SUBM and values == default:
                values = {}
                values[Terminal.COMMAND_BACK] = {
                    Terminal.Action.KEY_EXEC: Terminal.ActionBack,
                }
                values = self._get_from_config(values)
                state = self._get_by_key(Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_STATUS)
                if state == Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_STATUS_MISSING:
                    values['delete'] = {
                        Terminal.Action.KEY_EXEC: Terminal.ActionUpdateContainerProcessorsItemDelete,
                    }
                self.kwargs[key] = values
            return values

    class ActionUpdateContainerProcessors(ActionMenu):
        COMMAND_CONFIG_LXC_ID = 'id'
        COMMAND_CONFIG_LXC_NAME = 'name'
        COMMAND_CONFIG_LXC_STATUS = 'status'
        COMMAND_CONFIG_LXC_STATUS_MISSING = 'Missing'
        COMMAND_CONFIG_LXC_PROCESSORS = 'processors'

        CONTAINERS_FROM_PVE = []

        def get_description(self):
            return 'Update containers mapping'

        def _upsert_config(self, commands, key, options):
            existed = commands.get(key, {
                Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_PROCESSORS: [],
                Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_NAME: '-',
                Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_STATUS: Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_STATUS_MISSING,
                Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_ID: key,
                Terminal.Action.KEY_EXEC: Terminal.ActionUpdateContainerProcessorsItem,
            })
            commands[key] = {
                Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_PROCESSORS: options.get(
                    Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_PROCESSORS,
                    existed[Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_PROCESSORS]
                ),
                Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_NAME: options.get(
                    Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_NAME,
                    existed[Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_NAME]
                ),
                Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_STATUS: options.get(
                    Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_STATUS,
                    existed[Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_STATUS]
                ),
                Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_ID: options.get(
                    Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_ID,
                    existed[Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_ID]
                ),
                Terminal.Action.KEY_EXEC: options.get(
                    Terminal.Action.KEY_EXEC,
                    existed[Terminal.Action.KEY_EXEC]
                ),
            }

        def _get_from_config(self):
            commands = {}
            containers_from_config = config.CONTAINER_PROCESSORS_MAPPING
            # Add containers from config
            for key in containers_from_config.keys():
                self._upsert_config(commands, key, {
                    Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_PROCESSORS: containers_from_config[key],
                })
            return commands

        def _update_from_pve(self, commands):
            print('Updating PVE containers list. Please wait...')

            containers_from_pve = []

            if len(Terminal.ActionUpdateContainerProcessors.CONTAINERS_FROM_PVE) != 0:
                containers_from_pve = Terminal.ActionUpdateContainerProcessors.CONTAINERS_FROM_PVE
            else:
                # Get containers from PVE
                monitoring = PVEMonitoring()
                containers_from_pve = monitoring.get_containers()
                Terminal.ActionUpdateContainerProcessors.CONTAINERS_FROM_PVE = containers_from_pve

            # Upsert containers from PVE
            for container in containers_from_pve:
                container_id = container['id']
                container_name = container['name']
                container_state = container['state']
                self._upsert_config(commands, container_id, {
                    Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_NAME: container_name,
                    Terminal.ActionUpdateContainerProcessors.COMMAND_CONFIG_LXC_STATUS: container_state,
                })

            return commands

        def _get_by_key(self, key, default = None):
            values = Terminal.ActionMenu._get_by_key(self, key, default)
            if key == Terminal.ActionMenu.KEY_SUBM and values == default:
                values = self._get_from_config()
                values = self._update_from_pve(values)
                values = collections.OrderedDict(sorted(values.items()))
                values = {
                    Terminal.COMMAND_BACK: {
                        Terminal.Action.KEY_EXEC: Terminal.ActionBack,
                    },
                    **values,
                }
            return values

    def _init_commands(self):
        self.commands = {
            Terminal.Action.KEY_EXEC: Terminal.ActionMenu,
            Terminal.Action.KEY_DESC: 'Welcome to Monitoring Tool',
            Terminal.ActionMenu.KEY_SUBM: {
                'help': {
                    Terminal.Action.KEY_EXEC: Terminal.ActionHelp,
                },
                'process': {
                    Terminal.Action.KEY_EXEC: Terminal.ActionProcess,
                },
                'settings': {
                    Terminal.Action.KEY_EXEC: Terminal.ActionMenu,
                    Terminal.Action.KEY_DESC: 'Settings',
                    Terminal.ActionMenu.KEY_SUBM: {
                        Terminal.COMMAND_BACK: {
                            Terminal.Action.KEY_EXEC: Terminal.ActionBack,
                        },
                        'update-influx': {
                            Terminal.Action.KEY_EXEC: Terminal.ActionMenu,
                            Terminal.Action.KEY_DESC: 'Update INFLUX config',
                            Terminal.ActionMenu.KEY_SUBM: {
                                Terminal.COMMAND_BACK: {
                                    Terminal.Action.KEY_EXEC: Terminal.ActionBack,
                                },
                                'INFLUX_HOST': {
                                    Terminal.Action.KEY_EXEC: Terminal.ActionUpdateConfig,
                                    Terminal.Action.KEY_HELP: 'Influx host (http://ip-address)',
                                },
                                'INFLUX_PORT': {
                                    Terminal.Action.KEY_EXEC: Terminal.ActionUpdateConfig,
                                    Terminal.Action.KEY_HELP: 'Influx port (8086)',
                                },
                                'INFLUX_ORG': {
                                    Terminal.Action.KEY_EXEC: Terminal.ActionUpdateConfig,
                                    Terminal.Action.KEY_HELP: 'Influx organization',
                                },
                                'INFLUX_BUCKET': {
                                    Terminal.Action.KEY_EXEC: Terminal.ActionUpdateConfig,
                                    Terminal.Action.KEY_HELP: 'Influx bucket',
                                },
                                'INFLUX_TOKEN': {
                                    Terminal.Action.KEY_EXEC: Terminal.ActionUpdateConfig,
                                    Terminal.Action.KEY_HELP: 'Influx token',
                                },
                            },
                        },
                        'update-lxc': {
                            Terminal.Action.KEY_EXEC: Terminal.ActionUpdateContainerProcessors,
                        },
                        'update-crone': {
                            Terminal.Action.KEY_EXEC: Terminal.Action,
                            Terminal.Action.KEY_DESC: '(TODO) Update cron',
                        },
                        'update-general': {
                            Terminal.Action.KEY_EXEC: Terminal.ActionMenu,
                            Terminal.Action.KEY_DESC: 'Update General config',
                            Terminal.ActionMenu.KEY_SUBM: {
                                Terminal.COMMAND_BACK: {
                                    Terminal.Action.KEY_EXEC: Terminal.ActionBack,
                                },
                                'CONFIG_FILE': {
                                    Terminal.Action.KEY_EXEC: Terminal.ActionUpdateConfig,
                                    Terminal.Action.KEY_HELP: 'Config file path',
                                },
                                'DEBUG_MODE': {
                                    Terminal.Action.KEY_EXEC: Terminal.ActionUpdateConfig,
                                    Terminal.ActionUpdateConfig.KEY_TYPE: bool,
                                    Terminal.Action.KEY_HELP: 'Debug mode (True/False)',
                                },
                                'MANIFESTS_FOLDER': {
                                    Terminal.Action.KEY_EXEC: Terminal.ActionUpdateConfig,
                                    Terminal.Action.KEY_HELP: 'Manifest folder path',
                                },
                                'USE_CACHE': {
                                    Terminal.Action.KEY_EXEC: Terminal.ActionUpdateConfig,
                                    Terminal.ActionUpdateConfig.KEY_TYPE: bool,
                                    Terminal.Action.KEY_HELP: 'Enable cache mode',
                                },
                                'CACHE_FILE': {
                                    Terminal.Action.KEY_EXEC: Terminal.ActionUpdateConfig,\
                                    Terminal.Action.KEY_HELP: 'Cache file path',
                                },
                                'CACHE_TTL': {
                                    Terminal.Action.KEY_EXEC: Terminal.ActionUpdateConfig,
                                    Terminal.ActionUpdateConfig.KEY_TYPE: int,
                                    Terminal.Action.KEY_HELP: 'Cache TTL',
                                },
                            },
                        },
                        'update-docker': {
                            Terminal.Action.KEY_EXEC: Terminal.ActionMenu,
                            Terminal.Action.KEY_DESC: 'Update Docker Processor config',
                            Terminal.ActionMenu.KEY_SUBM: {
                                Terminal.COMMAND_BACK: {
                                    Terminal.Action.KEY_EXEC: Terminal.ActionBack,
                                },
                                'DOCKER_ARCHITECTURE': {
                                    Terminal.Action.KEY_EXEC: Terminal.ActionUpdateConfig,
                                    Terminal.Action.KEY_HELP: 'Docker target architecture',
                                },
                                'DOCKER_OS': {
                                    Terminal.Action.KEY_EXEC: Terminal.ActionUpdateConfig,
                                    Terminal.Action.KEY_HELP: 'Docker target OS',
                                },
                            },
                        },
                    },
                },
                'exit': {
                    Terminal.Action.KEY_EXEC: Terminal.ActionExit,
                },
            },
        }

    def __init__(self, args):
        self._init_commands()

        commands = args[1:]

        action = self.ActionMenu(**self.commands, terminal=self)
        
        while len(commands) != 0:
            sub_action = action.get_sub_action(commands[0])
            if not sub_action:
                break
            else:
                commands.pop(0)
                action = sub_action
        
        while action is not None:
            action = action.run(commands)

if __name__ == '__main__':
    config = Config(**load_config())
    Terminal(sys.argv)
