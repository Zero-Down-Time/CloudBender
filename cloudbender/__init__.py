import logging

__author__ = "Stefan Reimer"
__email__ = "stefan@zero-downtimet.net"
__version__ = "0.6.2"


# Set up logging to ``/dev/null`` like a library is supposed to.
# http://docs.python.org/3.3/howto/logging.html#configuring-logging-for-a-library
class NullHandler(logging.Handler):  # pragma: no cover
    def emit(self, record):
        pass


logging.getLogger('cloudbender').addHandler(NullHandler())
