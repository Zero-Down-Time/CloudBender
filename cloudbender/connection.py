import os
import time

import boto3
import botocore.session
from botocore import credentials

import logging

logger = logging.getLogger(__name__)


class BotoConnection():
    _sessions = {}
    _clients = {}

    def __init__(self, profile=None, region=None):
        self.region = region
        self.profile = profile

    def _get_session(self, profile=None, region=None):
        if self._sessions.get((profile, region)):
            return self._sessions[(profile, region)]

        # Construct botocore session with cache
        # Setup boto to cache STS tokens for MFA
        # Change the cache path from the default of ~/.aws/boto/cache to the one used by awscli
        session_vars = {}
        if profile:
            session_vars['profile'] = (None, None, profile, None)
        if region and region != 'global':
            session_vars['region'] = (None, None, region, None)

        session = botocore.session.Session(session_vars=session_vars)
        cli_cache = os.path.join(os.path.expanduser('~'), '.aws/cli/cache')
        session.get_component('credential_provider').get_provider('assume-role').cache = credentials.JSONFileCache(cli_cache)

        self._sessions[(profile, region)] = session

        return session

    def _get_client(self, service, profile=None, region=None):
        if self._clients.get((profile, region, service)):
            return self._clients[(profile, region, service)]

        session = self._get_session(profile, region)
        client = boto3.Session(botocore_session=session).client(service)

        self._clients[(profile, region, service)] = client
        return client

    def call(self, service, command, kwargs={}, profile=None, region=None):
        while True:
            try:
                client = self._get_client(service, profile, region)
                return getattr(client, command)(**kwargs)

            except botocore.exceptions.ClientError as e:
                if e.response['Error']['Code'] == 'Throttling':
                    logger.warning("Throttling exception occured during {} - retry after 3s".format(command))
                    time.sleep(3)
                    pass
                else:
                    raise e
