import os
import copy
import logging
import boto3

logger = logging.getLogger(__name__)


def dict_merge(a, b):
    """ Deep merge to allow proper inheritance for config files"""
    if not a:
        return b

    if not b:
        return a

    if not isinstance(a, dict) or not isinstance(b, dict):
        raise TypeError

    result = copy.deepcopy(a)
    for k, v in b.items():
        if k in result and isinstance(result[k], dict):
            result[k] = dict_merge(result[k], v)
        else:
            result[k] = copy.deepcopy(v)
    return result


def ensure_dir(path):
    """Creates dir if it does not already exist."""
    if not os.path.exists(path):
        os.makedirs(path)
        logger.info('Created directory: %s', path)


def setup_logging(debug):
    if debug:
        our_level = logging.DEBUG
        logging.getLogger("botocore").setLevel(logging.INFO)
        boto3.set_stream_logger('')

        formatter = logging.Formatter(
            fmt="[%(asctime)s] %(name)s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
    else:
        our_level = logging.INFO
        logging.getLogger("botocore").setLevel(logging.CRITICAL)

        formatter = logging.Formatter(
            fmt="[%(asctime)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

    log_handler = logging.StreamHandler()
    log_handler.setFormatter(formatter)
    logger = logging.getLogger("cloudbender")
    logger.addHandler(log_handler)
    logger.setLevel(our_level)
    return logger


def search_refs(template, attributes, mode):
    """ Traverses a template and searches for all Fn::GetAtt calls to FortyTwo
        adding them to the passed in attributes set
    """
    if isinstance(template, dict):
        for k, v in template.items():
            # FortyTwo Fn::GetAtt
            if k == "Fn::GetAtt" and isinstance(v, list):
                if v[0] == "FortyTwo":
                    attributes.append(v[1])

            # CloudBender::StackRef
            if k == "CloudBender::StackRef":
                try:
                    attributes.append(v['StackTags']['Artifact'])
                except KeyError:
                    pass

            # PipedMode Refs
            if mode == "Piped" and k == "Ref" and "DoT" in v:
                attributes.append(v)

            if isinstance(v, dict) or isinstance(v, list):
                search_refs(v, attributes, mode)

    elif isinstance(template, list):
        for k in template:
            if isinstance(k, dict) or isinstance(k, list):
                search_refs(k, attributes, mode)
