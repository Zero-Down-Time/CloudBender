import logging
import importlib.metadata

__author__ = "Stefan Reimer"
__email__ = "stefan@zero-downtimet.net"

try:
    __version__ = importlib.metadata.distribution("CloudBender").version
except importlib.metadata.PackageNotFoundError:
    __version__ = "devel"


# Set up logging to ``/dev/null`` like a library is supposed to.
# http://docs.python.org/3.3/howto/logging.html#configuring-logging-for-a-library
class NullHandler(logging.Handler):  # pragma: no cover
    def emit(self, record):
        pass


logging.getLogger("cloudbender").addHandler(NullHandler())
