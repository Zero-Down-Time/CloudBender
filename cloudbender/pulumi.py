import sys
import os
import re
import shutil
import tempfile
import importlib
import pulumi
import subprocess

from functools import wraps

import logging

logger = logging.getLogger(__name__)

# Disable Pulumis version check globally
os.environ["PULUMI_SKIP_UPDATE_CHECK"] = "true"


def get_pulumi_version():
    p = shutil.which("pulumi")
    if not p:
        return None

    proc = subprocess.Popen(
        [p, "version"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    if not proc.returncode:
        return proc.communicate()[0].decode().strip()
    else:
        return None


def resolve_outputs(outputs):
    my_outputs = {}

    for k, v in outputs.items():
        if type(v) == pulumi.automation._output.OutputValue:
            if v.secret:
                my_outputs[k] = "***"
            else:
                my_outputs[k] = v.value
        else:
            my_outputs[k] = v

    return my_outputs


def pulumi_ws(func):
    @wraps(func)
    def decorated(self, *args, **kwargs):
        # setup temp workspace
        if self.mode == "pulumi":
            self.work_dir = tempfile.mkdtemp(
                dir=tempfile.gettempdir(), prefix="cloudbender-"
            )

            # add all artifact_paths/pulumi to the search path for easier imports in the pulumi code
            for artifacts_path in self.ctx["artifact_paths"]:
                _path = "{}/pulumi".format(artifacts_path.resolve())
                sys.path.append(_path)

            # Try local implementation first, similar to Jinja2 mode
            _found = False
            try:
                _stack = importlib.import_module(
                    "config.{}.{}".format(self.rel_path, self.template).replace(
                        "/", "."
                    )
                )
                _found = True

            except ImportError:
                for artifacts_path in self.ctx["artifact_paths"]:
                    try:
                        spec = importlib.util.spec_from_file_location(
                            "_stack",
                            "{}/pulumi/{}.py".format(
                                artifacts_path.resolve(), self.template
                            ),
                        )
                        _stack = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(_stack)
                        _found = True

                    except FileNotFoundError:
                        pass

            if not _found:
                raise FileNotFoundError(
                    "Cannot find Pulumi implementation for {}".format(self.stackname)
                )

            # Store internal pulumi code reference
            self._pulumi_code = _stack

            # Use legacy Conglomerate as Pulumi project_name
            project_name = self.parameters["Conglomerate"]

            # Remove stacknameprefix if equals Conglomerate as Pulumi implicitly prefixes project_name
            self.pulumi_stackname = re.sub(
                r"^" + project_name + "-?", "", self.stackname
            )
            try:
                pulumi_backend = "{}/{}/{}".format(
                    self.pulumi["backend"], project_name, self.region
                )

            except KeyError:
                raise KeyError("Missing pulumi.backend setting !")

            # Ugly hack as Pulumi currently doesnt support MFA_TOKENs during role assumptions
            # Do NOT set them via 'aws:secretKey' as they end up in the self.json in plain text !!!
            account_id = self.connection_manager.call(
                "sts", "get_caller_identity", profile=self.profile, region=self.region
            )["Account"]
            self.connection_manager.exportProfileEnv()

            # Secrets provider
            if "secretsProvider" in self.pulumi:
                secrets_provider = self.pulumi["secretsProvider"]
                if (
                    secrets_provider == "passphrase"
                    and "PULUMI_CONFIG_PASSPHRASE" not in os.environ
                ):
                    raise ValueError(
                        "Missing PULUMI_CONFIG_PASSPHRASE environment variable!"
                    )

            else:
                try:
                    if self._pulumi_code.IKNOWHATIDO:
                        logger.warning(
                            "Missing pulumi.secretsProvider setting, IKNOWHATIDO enabled ... "
                        )
                        secrets_provider = None
                except AttributeError:
                    raise ValueError("Missing pulumi.secretsProvider setting!")

            # Set tag for stack file name and version
            _tags = {}
            try:
                _version = self._pulumi_code.VERSION
            except AttributeError:
                _version = "undefined"

            # Tag all resources with our metadata, allowing "prune" eventually
            _tags["zdt:cloudbender.source"] = "{}:{}".format(
                os.path.basename(self._pulumi_code.__file__), _version
            )
            _tags["zdt:cloudbender.owner"] = f"{project_name}.{self.pulumi_stackname}"

            # Inject all stack tags
            _tags.update(self.tags)

            self.pulumi_config.update(
                {
                    "aws:region": self.region,
                    "aws:defaultTags": {"tags": _tags},
                    "zdt:region": self.region,
                    "zdt:awsAccountId": account_id,
                    "zdt:projectName": project_name,
                    "zdt:stackName": self.pulumi_stackname,
                }
            )

            # inject all parameters as config in the <Conglomerate> namespace
            for p in self.parameters:
                self.pulumi_config[
                    "{}:{}".format(self.parameters["Conglomerate"], p)
                ] = self.parameters[p]

            stack_settings = pulumi.automation.StackSettings(
                config=self.pulumi_config,
                secrets_provider=secrets_provider,
                encryption_salt=self.pulumi.get("encryptionsalt", None),
                encrypted_key=self.pulumi.get("encryptedkey", None),
            )

            project_settings = pulumi.automation.ProjectSettings(
                name=project_name, runtime="python", backend={"url": pulumi_backend}
            )

            self.pulumi_ws_opts = pulumi.automation.LocalWorkspaceOptions(
                work_dir=self.work_dir,
                project_settings=project_settings,
                stack_settings={self.pulumi_stackname: stack_settings},
                secrets_provider=secrets_provider,
            )

        response = func(self, *args, **kwargs)

        # Cleanup temp workspace
        if self.work_dir and os.path.exists(self.work_dir):
            shutil.rmtree(self.work_dir)

        return response

    return decorated
