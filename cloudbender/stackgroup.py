import logging
import pprint

from .utils import dict_merge
from .jinja import read_config_file
from .stack import Stack

logger = logging.getLogger(__name__)


class StackGroup(object):
    def __init__(self, path, ctx):
        self.name = None
        self.ctx = ctx
        self.path = path
        self.rel_path = path.relative_to(ctx["config_path"])
        self.config = {}
        self.sgs = []
        self.stacks = []

        if self.rel_path == ".":
            self.rel_path = ""

    def dump_config(self):
        for sg in self.sgs:
            sg.dump_config()

        logger.debug(
            "StackGroup {}: {}".format(self.rel_path, pprint.pformat(self.config))
        )

        for s in self.stacks:
            s.dump_config()

    def read_config(self, parent_config={}, loadStacks=True):
        if not self.path.is_dir():
            return None

        # First read config.yaml if present
        _config = read_config_file(
            self.path.joinpath("config.yaml"), parent_config.get("variables", {})
        )

        # Stack Group name if not explicit via config is derived from subfolder, or in case of root object the parent folder
        if "stackgroupname" in _config:
            self.name = _config["stackgroupname"]
        elif not self.name:
            self.name = self.path.stem

        # Merge config with parent config
        self.config = dict_merge(parent_config, _config)
        stackname_prefix = self.config.get("stacknameprefix", "")

        # profile and region need special treatment due to cmd line overwrite option
        if self.ctx["region"]:
            self.config["region"] = self.ctx["region"]

        if self.ctx["profile"]:
            self.config["profile"] = self.ctx["profile"]

        logger.debug("StackGroup {} added.".format(self.name))

        # Add stacks
        if loadStacks:
            stacks = [
                s for s in self.path.glob("*.yaml") if not s.name == "config.yaml"
            ]
            for stack_path in stacks:
                stackname = stack_path.name.split(".")[0]
                template = stackname
                if stackname_prefix:
                    stackname = stackname_prefix + stackname

                new_stack = Stack(
                    name=stackname,
                    template=template,
                    path=stack_path,
                    rel_path=str(self.rel_path),
                    ctx=self.ctx,
                )
                new_stack.read_config(self.config)
                self.stacks.append(new_stack)

        # Create StackGroups recursively
        for sub_group in [s for s in self.path.iterdir() if s.is_dir()]:
            sg = StackGroup(sub_group, self.ctx)
            sg.read_config(self.config, loadStacks=loadStacks)

            self.sgs.append(sg)

    def get_stacks(self, name=None, recursive=True, match_by="name"):
        """Returns [stack] matching stack_name or [all]"""
        stacks = []
        if name:
            logger.debug("Looking for stack {} in group {}".format(name, self.name))

        for s in self.stacks:
            if name:
                if match_by == "name" and s.stackname != name:
                    continue

                if match_by == "path" and not s.path.match(name):
                    continue

            if self.rel_path:
                logger.debug(
                    "Found stack {} in group {}".format(s.stackname, self.rel_path)
                )
            else:
                logger.debug("Found stack {}".format(s.stackname))
            stacks.append(s)

        if recursive:
            for sg in self.sgs:
                s = sg.get_stacks(name, recursive, match_by)
                if s:
                    stacks = stacks + s

        return stacks

    def get_stackgroup(self, name=None, match_by="path"):
        """Returns stack group matching stackgroup_name or all if None"""
        if self.path.match(name):
            logger.debug("Found stack_group {}".format(self.name))
            return self

        if name and name != "config":
            logger.debug(
                "Looking for stack_group {} in group {}".format(name, self.name)
            )

        for sg in self.sgs:
            s = sg.get_stackgroup(name, match_by)
            if s:
                return s

        return None
