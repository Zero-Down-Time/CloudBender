import pathlib
import logging
import pexpect

from .stackgroup import StackGroup
from .connection import BotoConnection
from .jinja import read_config_file
from .exceptions import InvalidProjectDir

logger = logging.getLogger(__name__)


class CloudBender(object):
    """Config Class to handle recursive conf/* config tree"""

    def __init__(self, root_path, profile, region):
        self.root = pathlib.Path(root_path)
        self.sg = None
        self.all_stacks = []
        self.ctx = {
            "config_path": self.root.joinpath("config"),
            "template_path": self.root.joinpath("cloudformation"),
            "hooks_path": self.root.joinpath("hooks"),
            "docs_path": self.root.joinpath("docs"),
            "outputs_path": self.root.joinpath("outputs"),
            "artifact_paths": [self.root.joinpath("artifacts")],
            "profile": profile,
            "region": region,
        }

        if profile:
            logger.info("Profile overwrite: using {}".format(self.ctx["profile"]))

        if region:
            logger.info("Region overwrite: using {}".format(self.ctx["region"]))

        if not self.ctx["config_path"].is_dir():
            raise InvalidProjectDir(
                "Check '{0}' exists and is a valid CloudBender project folder.".format(
                    self.ctx["config_path"]
                )
            )

    def read_config(self, loadStacks=True):
        """Load the <path>/config.yaml, <path>/*.yaml as stacks, sub-folders are sub-groups"""

        # Read top level config.yaml and extract CloudBender CTX
        _config = read_config_file(self.ctx["config_path"].joinpath("config.yaml"))

        # Legacy naming
        if _config and _config.get("CloudBender"):
            self.ctx.update(_config.get("CloudBender"))

        if _config and _config.get("cloudbender"):
            self.ctx.update(_config.get("cloudbender"))

        # Make sure all paths are abs
        for k, v in self.ctx.items():
            if k in [
                "config_path",
                "template_path",
                "hooks_path",
                "docs_path",
                "artifact_paths",
                "outputs_path",
            ]:
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

        self.sg = StackGroup(self.ctx["config_path"], self.ctx)
        self.sg.read_config(loadStacks=loadStacks)

        self.all_stacks = self.sg.get_stacks()

    def dump_config(self):
        logger.debug("<CloudBender: {}>".format(vars(self)))
        self.sg.dump_config()

    def clean(self):
        for s in self.all_stacks:
            s.delete_template_file()

    def resolve_stacks(self, token):
        stacks = []

        # remove optional leading "config/" to allow bash path expansions
        if token.startswith("config/"):
            token = token[7:]

        # If path ends with yaml we look for stacks
        if token.endswith(".yaml"):
            stacks = self.sg.get_stacks(token, match_by="path")

        # otherwise assume we look for a group, if we find a group return all stacks below
        else:
            # Strip potential trailing slash
            token = token.rstrip("/")

            sg = self.sg.get_stackgroup(token, match_by="path")
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

    def wrap(self, stack_group, cmd):
        """
        Set AWS environment based on profile before executing a custom command, eg. steampipe
        """

        profile = stack_group.config.get("profile", "default")
        region = stack_group.config.get("region", "global")

        connection_manager = BotoConnection(profile, region)
        connection_manager.exportProfileEnv()

        child = pexpect.spawn(cmd)
        child.interact()
