#  Copyright (c) ZenML GmbH 2022. All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at:
#
#       https://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
#  or implied. See the License for the specific language governing
#  permissions and limitations under the License.

import os
import pathlib
import subprocess
import sys
import tempfile
from abc import abstractmethod
from typing import Dict, List, Optional, Tuple

import psutil
from pydantic import Field

from zenml.io.utils import create_dir_recursive_if_not_exists
from zenml.logger import get_logger
from zenml.services.local.local_service_endpoint import (
    LocalDaemonServiceEndpoint,
)
from zenml.services.service import BaseService, ServiceConfig
from zenml.services.service_status import ServiceState, ServiceStatus

logger = get_logger(__name__)


SERVICE_DAEMON_CONFIG_FILE_NAME = "service.json"
SERVICE_DAEMON_LOG_FILE_NAME = "service.log"
SERVICE_DAEMON_PID_FILE_NAME = "service.pid"


class LocalDaemonServiceConfig(ServiceConfig):
    """Local daemon service configuration.

    Attributes:
        silent_daemon: set to True to suppress the output of the daemon
            (i.e. redirect stdout and stderr to /dev/null). If False, the
            daemon output will be redirected to a logfile.
        root_runtime_path: the root path where the service daemon will store
            service configuration files
    """

    silent_daemon: bool = False
    root_runtime_path: Optional[str] = None


class LocalDaemonServiceStatus(ServiceStatus):
    """Local daemon service status.

    Attributes:
        runtime_path: the path where the service daemon runtime files (the
            configuration file used to start the service daemon and the
            logfile) are located
        silent_daemon: flag indicating whether the output of the daemon
            is suppressed (redirected to /dev/null).
    """

    runtime_path: Optional[str] = None
    # TODO [ENG-704]: remove field duplication between XServiceStatus and
    #   XServiceConfig (e.g. keep a private reference to the config in the
    #   status)
    silent_daemon: bool = False

    @property
    def config_file(self) -> Optional[str]:
        """Get the path to the configuration file used to start the service
        daemon.

        Returns:
            The path to the configuration file, or None, if the
            service has never been started before.
        """
        if not self.runtime_path:
            return None
        return os.path.join(self.runtime_path, SERVICE_DAEMON_CONFIG_FILE_NAME)

    @property
    def log_file(self) -> Optional[str]:
        """Get the path to the log file where the service output is/has been
        logged.

        Returns:
            The path to the log file, or None, if the service has never been
            started before, or if the service daemon output is suppressed.
        """
        if not self.runtime_path or self.silent_daemon:
            return None
        return os.path.join(self.runtime_path, SERVICE_DAEMON_LOG_FILE_NAME)

    @property
    def pid_file(self) -> Optional[str]:
        """Get the path to the daemon PID file where the last known PID of the
        daemon process is stored.

        Returns:
            The path to the PID file, or None, if the service has never been
            started before.
        """
        if not self.runtime_path or self.silent_daemon:
            return None
        return os.path.join(self.runtime_path, SERVICE_DAEMON_PID_FILE_NAME)

    @property
    def pid(self) -> Optional[int]:
        """Return the PID of the currently running daemon"""
        pid_file = self.pid_file
        if not pid_file:
            return None
        if sys.platform == "win32":
            logger.warning(
                "Daemon functionality is currently not supported on Windows."
            )
            return None
        else:
            from zenml.utils.daemon import get_daemon_pid_if_running

            return get_daemon_pid_if_running(pid_file)


class LocalDaemonService(BaseService):
    """A service represented by a local daemon process.

    This class extends the base service class with functionality concerning
    the life-cycle management and tracking of external services implemented as
    local daemon processes.

    To define a local daemon service, subclass this class and implement the
    `run` method. Upon `start`, the service will spawn a daemon process that
    ends up calling the `run` method.

    Example:

    ```python

    from zenml.services import ServiceType, LocalDaemonService, LocalDaemonServiceConfig
    import time

    class SleepingDaemonConfig(LocalDaemonServiceConfig):

        wake_up_after: int

    class SleepingDaemon(LocalDaemonService):

        SERVICE_TYPE = ServiceType(
            name="sleeper",
            description="Sleeping daemon",
            type="daemon",
            flavor="sleeping",
        )
        config: SleepingDaemonConfig

        def run(self) -> None:
            time.sleep(self.config.wake_up_after)

    daemon = SleepingDaemon(config=SleepingDaemonConfig(wake_up_after=10))
    daemon.start()
    ```

    NOTE: the `SleepingDaemon` class and its parent module have to be
    discoverable as part of a ZenML `Integration`, otherwise the daemon will
    fail with the following error:

    ```
    TypeError: Cannot load service with unregistered service type:
    name='sleeper' type='daemon' flavor='sleeping' description='Sleeping daemon'
    ```


    Attributes:
        config: service configuration
        status: service status
        endpoint: optional service endpoint
    """

    config: LocalDaemonServiceConfig = Field(
        default_factory=LocalDaemonServiceConfig
    )
    status: LocalDaemonServiceStatus = Field(
        default_factory=LocalDaemonServiceStatus
    )
    # TODO [ENG-705]: allow multiple endpoints per service
    endpoint: Optional[LocalDaemonServiceEndpoint] = None

    def check_status(self) -> Tuple[ServiceState, str]:
        """Check the the current operational state of the daemon process.

        Returns:
            The operational state of the daemon process and a message
            providing additional information about that state (e.g. a
            description of the error, if one is encountered).
        """

        if not self.status.pid:
            return ServiceState.INACTIVE, "service daemon is not running"

        # the daemon is running
        return ServiceState.ACTIVE, ""

    def _get_daemon_cmd(self) -> Tuple[List[str], Dict[str, str]]:
        """Get the command to run the service daemon.

        The default implementation provided by this class is the following:

          * this LocalDaemonService instance and its configuration
          are serialized as JSON and saved to a temporary file
          * the local_daemon_entrypoint.py script is launched as a subprocess
          and pointed to the serialized service file
          * the entrypoint script re-creates the LocalDaemonService instance
          from the serialized configuration, reconfigures itself as a daemon
          and detaches itself from the parent process, then calls the `run`
          method that must be implemented by the subclass

        Subclasses that need a different command to launch the service daemon
        should override this method.

        Returns:
            Command needed to launch the daemon process and the environment
            variables to set, in the formats accepted by subprocess.Popen.
        """
        # to avoid circular imports, import here
        import zenml.services.local.local_daemon_entrypoint as daemon_entrypoint

        self.status.silent_daemon = self.config.silent_daemon
        # reuse the config file and logfile location from a previous run,
        # if available
        if not self.status.runtime_path or not os.path.exists(
            self.status.runtime_path
        ):
            # runtime_path points to zenml local stores with uuid to make it
            # easy to track from other locations.
            if self.config.root_runtime_path:
                self.status.runtime_path = os.path.join(
                    self.config.root_runtime_path,
                    str(self.uuid),
                )
                create_dir_recursive_if_not_exists(self.status.runtime_path)
            else:
                self.status.runtime_path = tempfile.mkdtemp(
                    prefix="zenml-service-"
                )

        assert self.status.config_file is not None
        assert self.status.pid_file is not None

        with open(self.status.config_file, "w") as f:
            f.write(self.json(indent=4))

        # delete the previous PID file, in case a previous daemon process
        # crashed and left a stale PID file
        if os.path.exists(self.status.pid_file):
            os.remove(self.status.pid_file)

        command = [
            sys.executable,
            "-m",
            daemon_entrypoint.__name__,
            "--config-file",
            self.status.config_file,
            "--pid-file",
            self.status.pid_file,
        ]
        if self.status.log_file:
            pathlib.Path(self.status.log_file).touch()
            command += ["--log-file", self.status.log_file]

        command_env = os.environ.copy()

        return command, command_env

    def _start_daemon(self) -> None:
        """Start the service daemon process associated with this service."""

        pid = self.status.pid
        if pid:
            # service daemon is already running
            logger.debug(
                "Daemon process for service '%s' is already running with PID %d",
                self,
                pid,
            )
            return

        logger.debug("Starting daemon for service '%s'...", self)

        if self.endpoint:
            self.endpoint.prepare_for_start()

        command, command_env = self._get_daemon_cmd()
        logger.debug(
            "Running command to start daemon for service '%s': %s",
            self,
            " ".join(command),
        )
        p = subprocess.Popen(command, env=command_env)
        p.wait()
        pid = self.status.pid
        if pid:
            logger.debug(
                "Daemon process for service '%s' started with PID: %d",
                self,
                pid,
            )
        else:
            logger.error(
                "Daemon process for service '%s' failed to start",
                self,
            )

    def _stop_daemon(self, force: bool = False) -> None:
        """Stop the service daemon process associated with this service.

        Args:
            force: if True, the service daemon will be forcefully stopped
        """

        pid = self.status.pid
        if not pid:
            # service daemon is not running
            logger.debug(
                "Daemon process for service '%s' no longer running",
                self,
            )
            return

        logger.debug("Stopping daemon for service '%s' ...", self)
        try:
            p = psutil.Process(pid)
        except psutil.Error:
            logger.error(
                "Could not find process for for service '%s' ...", self
            )
            return
        if force:
            p.kill()
        else:
            p.terminate()

    def provision(self) -> None:
        self._start_daemon()

    def deprovision(self, force: bool = False) -> None:
        self._stop_daemon(force)

    @abstractmethod
    def run(self) -> None:
        """Run the service daemon process associated with this service.

        Subclasses must implement this method to provide the service daemon
        functionality. This method will be executed in the context of the
        running daemon, not in the context of the process that calls the
        `start` method.
        """
