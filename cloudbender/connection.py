import os
import time

import boto3
import botocore.session
from botocore import credentials

import logging

logger = logging.getLogger(__name__)

sessions = {}
clients = {}


class BotoConnection:
    def __init__(self, profile=None, region=None):
        self.region = region
        self.profile = profile

    def _get_session(self, profile=None, region=None):
        if sessions.get((profile, region)):
            return sessions[(profile, region)]

        # Construct botocore session with cache
        # Setup boto to cache STS tokens for MFA
        # Change the cache path from the default of ~/.aws/boto/cache to the one used by awscli
        session_vars = {}
        if profile:
            session_vars["profile"] = (None, None, profile, None)
        if region and region != "global":
            session_vars["region"] = (None, None, region, None)

        session = botocore.session.Session(session_vars=session_vars)
        cli_cache = os.path.join(os.path.expanduser("~"), ".aws/cli/cache")
        session.get_component("credential_provider").get_provider(
            "assume-role"
        ).cache = credentials.JSONFileCache(cli_cache)

        sessions[(profile, region)] = session

        return session

    def _get_client(self, service, profile=None, region=None):
        if clients.get((profile, region, service)):
            logger.debug(
                "Reusing boto session for {} {} {}".format(profile, region, service)
            )
            return clients[(profile, region, service)]

        session = self._get_session(profile, region)
        client = boto3.Session(botocore_session=session).client(service)
        logger.debug("New boto session for {} {} {}".format(profile, region, service))

        clients[(profile, region, service)] = client
        return client

    def call(self, service, command, kwargs={}, profile=None, region=None):
        while True:
            try:
                client = self._get_client(service, profile, region)
                logger.debug("Calling {}:{}".format(client, command))
                return getattr(client, command)(**kwargs)

            except botocore.exceptions.ClientError as e:
                if e.response["Error"]["Code"] == "Throttling":
                    logger.warning(
                        "Throttling exception occured during {} - retry after 3s".format(
                            command
                        )
                    )
                    time.sleep(3)
                    pass
                else:
                    raise e

    def exportProfileEnv(self):
        """
        Set AWS os.env variables based on our connection profile to allow external programs use
        same profile, region. Eg. Pulumi or Steampipe
        """

        credentials = self._get_session(self.profile, self.region).get_credentials()

        if credentials.token:
            os.environ["AWS_SESSION_TOKEN"] = credentials.token

        os.environ["AWS_ACCESS_KEY_ID"] = credentials.access_key
        os.environ["AWS_SECRET_ACCESS_KEY"] = credentials.secret_key

        if self.region and self.region != "global":
            os.environ["AWS_DEFAULT_REGION"] = self.region
