import pathlib
import logging

from .utils import ensure_dir
from .stackgroup import StackGroup
from .jinja import read_config_file
from .exceptions import InvalidProjectDir

logger = logging.getLogger(__name__)


class CloudBender(object):
    """ Config Class to handle recursive conf/* config tree """
    def __init__(self, root_path):
        self.root = pathlib.Path(root_path)
        self.sg = None
        self.all_stacks = []
        self.ctx = {
            "config_path": self.root.joinpath("config"),
            "template_path": self.root.joinpath("cloudformation"),
            "parameter_path": self.root.joinpath("parameters"),
            "artifact_paths": [self.root.joinpath("artifacts")]
        }

        if not self.ctx['config_path'].is_dir():
            raise InvalidProjectDir("Check '{0}' exists and is a valid CloudBender project folder.".format(self.ctx['config_path']))

    def read_config(self):
        """Load the <path>/config.yaml, <path>/*.yaml as stacks, sub-folders are sub-groups """

        # Read top level config.yaml and extract CloudBender CTX
        _config = read_config_file(self.ctx['config_path'].joinpath('config.yaml'))
        if _config and _config.get('CloudBender'):
            self.ctx.update(_config.get('CloudBender'))

        # Make sure all paths are abs
        for k, v in self.ctx.items():
            if k in ['config_path', 'template_path', 'parameter_path', 'artifact_paths']:
                if isinstance(v, list):
                    new_list = []
                    for p in v:
                        path = pathlib.Path(p)
                        if not path.is_absolute():
                            new_list.append(self.root.joinpath(path))
                        else:
                            new_list.append(path)
                    self.ctx[k] = new_list

                elif isinstance(v, str):
                    if not v.is_absolute():
                        self.ctx[k] = self.root.joinpath(v)

            if k in ['template_path', 'parameter_path']:
                ensure_dir(self.ctx[k])

        self.sg = StackGroup(self.ctx['config_path'], self.ctx)
        self.sg.read_config()

        self.all_stacks = self.sg.get_stacks()

    def dump_config(self):
        logger.debug("<CloudBender: {}>".format(vars(self)))
        self.sg.dump_config()

    def clean(self):
        for s in self.all_stacks:
            s.delete_template_file()
            s.delete_parameter_file()

    def resolve_stacks(self, token):
        stacks = []

        # remove optional leading "config/" to allow bash path expansions
        if token.startswith("config/"):
            token = token[7:]

        # If path ends with yaml we look for stacks
        if token.endswith('.yaml'):
            stacks = self.sg.get_stacks(token, match_by='path')

        # otherwise assume we look for a group, if we find a group return all stacks below
        else:
            # Strip potential trailing slash
            token = token.rstrip('/')

            sg = self.sg.get_stackgroup(token, match_by='path')
            if sg:
                stacks = sg.get_stacks()

        return stacks

    def filter_stacks(self, filter_by, stacks=None):
        # filter_by is a dict { property, value }

        # if no group of stacks provided, look in all available
        if not stacks:
            stacks = self.all_stacks

        matching_stacks = []
        for s in stacks:
            match = True

            for p, v in filter_by.items():
                if not (hasattr(s, p) and getattr(s, p) == v):
                    match = False
                    break

            if match:
                matching_stacks.append(s)

        return matching_stacks
