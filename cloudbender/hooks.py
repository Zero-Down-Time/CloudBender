import sys
import subprocess
from functools import wraps

from .exceptions import InvalidHook

import logging

logger = logging.getLogger(__name__)


def execute_hooks(hooks, stack):
    for hook in hooks:
        tokens = hook.split()
        if tokens[0] in dir(sys.modules[__name__]):
            logger.info("Executing hook: {}".format(hook))
            globals()[tokens[0]](arguments=tokens[1:], stack=stack)
        else:
            logger.warning("Unknown hook: {}".format(hook))


def exec_hooks(func):
    @wraps(func)
    def decorated(self, *args, **kwargs):
        execute_hooks(self.hooks.get("pre_" + func.__name__, []), self)
        response = func(self, *args, **kwargs)

        # Only execute post hook for successful actions
        if response == "COMPLETE":
            execute_hooks(self.hooks.get("post_" + func.__name__, []), self)

        return response

    return decorated


# Various hooks
def cmd(stack, arguments):
    """
    Generic command via subprocess
    """

    try:
        hook = subprocess.run(arguments, stdout=subprocess.PIPE)
        logger.info(hook.stdout.decode("utf-8"))
    except TypeError:
        raise InvalidHook("Invalid argument {}".format(arguments))
