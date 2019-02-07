#!/usr/bin/python

import logging

from cloudbender.utils import setup_logging


def test_setup_logging_debug():
    logger = setup_logging(True)
    assert logger.getEffectiveLevel() == logging.DEBUG
    assert logging.getLogger("botocore").getEffectiveLevel() == \
        logging.INFO

    # Silence logging for the rest of the tests
    logger.setLevel(logging.CRITICAL)
