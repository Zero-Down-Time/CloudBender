import logging
import pkg_resources

__author__ = "Stefan Reimer"
__email__ = "stefan@zero-downtimet.net"

try:
    __version__ = pkg_resources.get_distribution("CloudBender").version
except pkg_resources.DistributionNotFound:
    __version__ = "devel"


# Set up logging to ``/dev/null`` like a library is supposed to.
# http://docs.python.org/3.3/howto/logging.html#configuring-logging-for-a-library
class NullHandler(logging.Handler):  # pragma: no cover
    def emit(self, record):
        pass


logging.getLogger("cloudbender").addHandler(NullHandler())
