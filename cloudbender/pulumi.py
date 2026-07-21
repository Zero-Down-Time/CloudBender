import sys
import os
import re
import shutil
import importlib
import importlib.util
import click
import pulumi
import subprocess
import semver

from functools import wraps

import logging

from . import __version__

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
        if isinstance(v, pulumi.automation._output.OutputValue):
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
        # search paths we add for the import below, removed again on cleanup
        appended = []

        # setup temp workspace
        if self.mode == "pulumi":
            # Fetch configured libraries (creates self.work_dir). pulumi_paths
            # holds the pulumi/ folders used for template discovery;
            # search_paths additionally exposes each library's artifacts/
            # folder so the Pulumi code can locate files/scripts.
            paths = self._fetch_libraries()
            pulumi_paths = paths["pulumi"]
            search_paths = paths["pulumi"] + paths["artifacts"]

            # Expose pulumi/ and artifacts/ for in-process imports; tracked in
            # appended so they can be removed again on cleanup (they point
            # into work_dir). Never done for CloudFormation, which runs in
            # parallel threads and must not mutate the global sys.path.
            for _path in search_paths:
                if _path not in sys.path:
                    sys.path.append(_path)
                    appended.append(_path)

            # Import self.template from the first library providing it
            _found = False
            for _path in pulumi_paths:
                candidate = os.path.join(_path, "{}.py".format(self.template))
                if os.path.exists(candidate):
                    spec = importlib.util.spec_from_file_location(
                        "_stack", candidate)
                    _stack = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(_stack)
                    _found = True
                    break

            if not _found:
                raise FileNotFoundError(
                    "Cannot find Pulumi implementation for {} in configured libraries (loaded: {})".format(
                        self.stackname,
                        ", ".join(self.loaded_libraries) or "none"))

            # Store internal pulumi code reference
            self._pulumi_code = _stack

            # Use legacy Conglomerate as Pulumi project_name
            project_name = self.parameters["Conglomerate"]

            # Remove stacknameprefix if equals Conglomerate as Pulumi
            # implicitly prefixes project_name
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
            # Do NOT set them via 'aws:secretKey' as they end up in the
            # self.json in plain text !!!
            account_id = self.connection_manager.call(
                "sts",
                "get_caller_identity",
                profile=self.profile,
                region=self.region)["Account"]
            self.connection_manager.exportProfileEnv()

            # Secrets provider
            secrets_provider = None
            if "secretsProvider" in self.pulumi:
                secrets_provider = self.pulumi["secretsProvider"]
                if (
                    secrets_provider == "passphrase"
                    and "PULUMI_CONFIG_PASSPHRASE" not in os.environ
                ):
                    raise ValueError(
                        "Missing PULUMI_CONFIG_PASSPHRASE environment variable!"
                    )

            # Set tag for stack file name and version
            _tags = {}
            try:
                _version = self._pulumi_code.VERSION
            except AttributeError:
                _version = "undefined"

            # bail out if we need a minimal cloudbender version for a template
            try:
                _min_version = self._pulumi_code.MIN_CLOUDBENDER_VERSION
                if semver.compare(__version__.strip("v"), _min_version.strip("v")) < 0:
                    raise ValueError(
                        f"Minimal required CloudBender version is {_min_version}, but we are {__version__}!"
                    )

            except AttributeError:
                pass

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
            # ensure camelCase until we are 100% Pulumi
            for p in self.parameters:
                _p = p[:1].lower() + p[1:]
                self.pulumi_config[
                    "{}:{}".format(self.parameters["Conglomerate"], _p)
                ] = self.parameters[p]

            stack_settings = pulumi.automation.StackSettings(
                config=self.pulumi_config,
                secrets_provider=secrets_provider,
                encryption_salt=self.pulumi.get("encryptionsalt", None),
                encrypted_key=self.pulumi.get("encryptedkey", None),
            )

            project_settings = pulumi.automation.ProjectSettings(
                name=project_name, runtime="python", backend=pulumi.automation.ProjectBackend(url=pulumi_backend)
            )

            existing = os.environ.get("PYTHONPATH", "")

            self.pulumi_ws_opts = pulumi.automation.LocalWorkspaceOptions(
                env_vars={"PULUMI_PYTHON_CMD": f"{os.environ['HOME']}/.venv/bin/python",
                          "PYTHONPATH": os.pathsep.join(search_paths + ([existing] if existing else [])),
                          },
                work_dir=self.work_dir,
                project_settings=project_settings,
                stack_settings={self.pulumi_stackname: stack_settings},
                secrets_provider=secrets_provider,
            )

            # self.pulumi_workspace = pulumi.automation.LocalWorkspace(self.pulumi_ws_opts)

        try:
            response = func(self, *args, **kwargs)

        except pulumi.automation.errors.CommandError:
            # Streamed operations already surface Pulumi's diagnostics via
            # on_output (_log_pulumi); drop the exception's duplicate stderr dump.
            if func.__name__ in ("create", "preview", "refresh", "delete"):
                logger.error(
                    "Pulumi {} failed for {}".format(func.__name__, self.stackname))
                raise click.Abort() from None
            raise

        finally:
            # Cleanup temp workspace
            if self.work_dir and os.path.exists(self.work_dir):
                shutil.rmtree(self.work_dir)

            # Remove any search paths we added; they point into work_dir
            for _path in appended:
                try:
                    sys.path.remove(_path)
                except ValueError:
                    pass

        return response

    return decorated
