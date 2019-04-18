import os
import glob
import logging

from .utils import dict_merge
from .jinja import read_config_file
from .stack import Stack

logger = logging.getLogger(__name__)


class StackGroup(object):
    def __init__(self, path, ctx):
        self.name = None
        self.ctx = ctx
        self.path = path
        self.rel_path = os.path.relpath(path, ctx['config_path'])
        self.config = {}
        self.sgs = []
        self.stacks = []

        if self.rel_path == '.':
            self.rel_path = ''

    def dump_config(self):
        for sg in self.sgs:
            sg.dump_config()

        logger.debug("<StackGroup {}: {}>".format(self.name, vars(self)))

        for s in self.stacks:
            s.dump_config()

    def read_config(self, parent_config={}):

        if not os.path.isdir(self.path):
            return None

        # First read config.yaml if present
        _config = read_config_file(os.path.join(self.path, 'config.yaml'))

        # Stack Group name if not explicit via config is derived from subfolder, or in case of root object the parent folder
        if "stackgroupname" in _config:
            self.name = _config["stackgroupname"]
        elif not self.name:
            self.name = os.path.split(self.path)[1]

        # Merge config with parent config
        _config = dict_merge(parent_config, _config)

        tags = _config.get('tags', {})
        parameters = _config.get('parameters', {})
        options = _config.get('options', {})
        region = _config.get('region', 'global')
        profile = _config.get('profile', '')
        stackname_prefix = _config.get('stacknameprefix', '')

        logger.debug("StackGroup {} added.".format(self.name))

        # Add stacks
        stacks = [s for s in glob.glob(os.path.join(self.path, '*.yaml')) if not s.endswith("config.yaml")]
        for stack_path in stacks:
            stackname = os.path.basename(stack_path).split('.')[0]
            template = stackname
            if stackname_prefix:
                stackname = stackname_prefix + stackname

            new_stack = Stack(
                name=stackname, template=template, path=stack_path, rel_path=str(self.rel_path),
                tags=dict(tags), parameters=dict(parameters), options=dict(options),
                region=str(region), profile=str(profile), ctx=self.ctx)
            new_stack.read_config()
            self.stacks.append(new_stack)

        # Create StackGroups recursively
        for sub_group in [f.path for f in os.scandir(self.path) if f.is_dir()]:
            sg = StackGroup(sub_group, self.ctx)
            sg.read_config(_config)

            self.sgs.append(sg)

        # Return raw, merged config to parent
        return _config

    def get_stacks(self, name=None, recursive=True, match_by='name'):
        """ Returns [stack] matching stack_name or [all] """
        stacks = []
        if name:
            logger.debug("Looking for stack {} in group {}".format(name, self.name))

        for s in self.stacks:
            if not name or (s.stackname == name and match_by == 'name') or (s.path.endswith(name) and match_by == 'path'):
                if self.rel_path:
                    logger.debug("Found stack {} in group {}".format(s.stackname, self.rel_path))
                else:
                    logger.debug("Found stack {}".format(s.stackname))
                stacks.append(s)

        if recursive:
            for sg in self.sgs:
                s = sg.get_stacks(name, recursive, match_by)
                if s:
                    stacks = stacks + s

        return stacks

    def get_stackgroup(self, name=None, recursive=True, match_by='name'):
        """ Returns stack group matching stackgroup_name or all if None """
        if not name or (self.name == name and match_by == 'name') or (self.path.endswith(name) and match_by == 'path'):
            logger.debug("Found stack_group {}".format(self.name))
            return self

        if name and name != 'config':
            logger.debug("Looking for stack_group {} in group {}".format(name, self.name))

        if recursive:
            for sg in self.sgs:
                s = sg.get_stackgroup(name, recursive, match_by)
                if s:
                    return s

        return None
