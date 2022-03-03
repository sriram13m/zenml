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
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, ValidationError

import zenml
from zenml.constants import (
    ENV_ZENML_REPOSITORY_PATH,
    LOCAL_CONFIG_DIRECTORY_NAME,
)
from zenml.enums import StackComponentType, StorageType
from zenml.environment import Environment
from zenml.exceptions import (
    ForbiddenRepositoryAccessError,
    InitializationException,
    RepositoryNotFoundError,
)
from zenml.io import fileio
from zenml.logger import get_logger
from zenml.post_execution import PipelineView
from zenml.stack import Stack, StackComponent
from zenml.stack_stores import LocalStackStore
from zenml.stack_stores.models import StackConfiguration, StackStoreModel
from zenml.utils import yaml_utils
from zenml.utils.analytics_utils import AnalyticsEvent, track, track_event

logger = get_logger(__name__)


class RepositoryConfiguration(BaseModel):
    """Pydantic object used for serializing repository configuration options.

    Attributes:
        version: Version of ZenML that was used to create the repository.
        storage_type: Type of Storage backend to persist the repository.
        active_stack_name: Optional name of the active stack.
    """

    version: str
    storage_type: StorageType

    @classmethod
    def empty_configuration(cls) -> "RepositoryConfiguration":
        """Helper method to create an empty configuration object."""
        return cls(
            version=zenml.__version__,
            storage_type=StorageType.YAML_STORAGE,
        )


class Repository:
    """ZenML repository class.

    ZenML repositories store configuration options for ZenML stacks as well as
    their components.
    """

    def __init__(
        self,
        root: Optional[Path] = None,
        storage_type: StorageType = StorageType.YAML_STORAGE,
    ):
        """Initializes a repository instance.

        Args:
            root: Optional root directory of the repository. If no path is
                given, this function tries to find the repository using the
                environment variable `ZENML_REPOSITORY_PATH` (if set) and
                recursively searching in the parent directories of the current
                working directory.

        Raises:
            RepositoryNotFoundError: If no ZenML repository directory is found.
            ForbiddenRepositoryAccessError: If trying to create a `Repository`
                instance while a ZenML step is being executed.
        """
        if Environment().step_is_running:
            raise ForbiddenRepositoryAccessError(
                "Unable to access repository during step execution. If you "
                "require access to the artifact or metadata store, please use "
                "a `StepContext` inside your step instead.",
                url="https://docs.zenml.io/features/step-fixtures#using-the-stepcontext",
            )

        self._root = Repository.find_repository(root)

        # load the repository configuration file if it exists, otherwise use
        # an empty configuration as default
        config_path = self._config_path()
        if fileio.file_exists(config_path):
            config_dict = yaml_utils.read_yaml(config_path)
            try:
                self.__config = RepositoryConfiguration.parse_obj(config_dict)
                stack_data = None
            except ValidationError:
                # if we have an old style repository in place already, split
                # config and stack store out into separate entities:
                logger.info(
                    "Found old style repository, converting to "
                    "minimal repository config with separate stack store file."
                )
                stack_data = StackStoreModel.parse_obj(config_dict)
                self.__config = RepositoryConfiguration(
                    version=stack_data.version,
                    storage_type=StorageType.YAML_STORAGE,
                )
                self._write_config()
        else:
            stack_data = None
            self.__config = RepositoryConfiguration.empty_configuration()

        if self.version != zenml.__version__:
            logger.warning(
                "This ZenML repository was created with a different version "
                "of ZenML (Repository version: %s, current ZenML version: %s). "
                "In case you encounter any errors, please delete and "
                "reinitialize this repository.",
                self.version,
                zenml.__version__,
            )

        if self.__config.storage_type == StorageType.YAML_STORAGE:
            self.stack_store = LocalStackStore(
                base_directory=str(self._root), stack_data=stack_data
            )
        elif self.__config.storage_type == StorageType.SQL_STORAGE:
            raise NotImplementedError("Sql Stack Storage is not implemented.")
        else:
            raise ValueError(
                f"Unsupported StackStore StorageType {self.__config.storage_type.value}"
            )

    def _config_path(self) -> str:
        """Path to the repository configuration file."""
        return str(self.config_directory / "config.yaml")

    def _write_config(self) -> None:
        """Writes the repository configuration file."""
        config_dict = json.loads(self.__config.json())
        yaml_utils.write_yaml(self._config_path(), config_dict)

    @staticmethod
    @track(event=AnalyticsEvent.INITIALIZE_REPO)
    def initialize(root: Path = Path.cwd()) -> None:
        """Initializes a new ZenML repository at the given path.

        The newly created repository will contain a single stack with a local
        orchestrator, a local artifact store and a local SQLite metadata store.

        Args:
            root: The root directory where the repository should be created.

        Raises:
            InitializationException: If the root directory already contains a
                ZenML repository.
        """
        logger.debug("Initializing new repository at path %s.", root)
        if Repository.is_repository_directory(root):
            raise InitializationException(
                f"Found existing ZenML repository at path '{root}'."
            )

        config_directory = str(root / LOCAL_CONFIG_DIRECTORY_NAME)
        fileio.create_dir_recursive_if_not_exists(config_directory)

        # register and activate a local stack
        repo = Repository(root=root)
        stack = Stack.default_local_stack()
        repo.register_stack(stack)
        repo.activate_stack(stack.name)
        repo._write_config()

    @property
    def version(self) -> str:
        """The version of the repository."""
        return self.__config.version

    @property
    def root(self) -> Path:
        """The root directory of this repository."""
        return self._root

    @property
    def config_directory(self) -> Path:
        """The configuration directory of this repository."""
        return self.root / LOCAL_CONFIG_DIRECTORY_NAME

    @property
    def stacks(self) -> List[Stack]:
        """All stacks registered in this repository."""
        return self.stack_store.stacks

    @property
    def stack_configurations(self) -> Dict[str, StackConfiguration]:
        """Configuration objects for all stacks registered in this repository.

        This property is intended as a quick way to get information about the
        components of the registered stacks without loading all installed
        integrations. The contained stack configurations might be invalid if
        they were modified by hand, to ensure you get valid stacks use
        `repo.stacks()` instead.

        Modifying the contents of the returned dictionary does not actually
        register/deregister stacks, use `repo.register_stack(...)` or
        `repo.deregister_stack(...)` instead.
        """
        return self.stack_store.stack_configurations

    @property
    def active_stack(self) -> Stack:
        """The active stack for this repository.

        Raises:
            RuntimeError: If no active stack name is configured.
            KeyError: If no stack was found for the configured name or one
                of the stack components is not registered.
        """
        return self.get_stack(name=self.active_stack_name)

    @property
    def active_stack_name(self) -> str:
        """The name of the active stack for this repository.

        Raises:
            RuntimeError: If no active stack name is configured.
        """
        return self.stack_store.active_stack_name

    @track(event=AnalyticsEvent.SET_STACK)
    def activate_stack(self, name: str) -> None:
        """Activates the stack for the given name.

        Args:
            name: Name of the stack to activate.

        Raises:
            KeyError: If no stack exists for the given name.
        """
        self.stack_store.activate_stack(name)

    def get_stack(self, name: str) -> Stack:
        """Fetches a stack.

        Args:
            name: The name of the stack to fetch.

        Raises:
            KeyError: If no stack exists for the given name or one of the
                stacks components is not registered.
        """
        return self.stack_store.get_stack(name)

    def register_stack(self, stack: Stack) -> None:
        """Registers a stack and it's components.

        If any of the stacks' components aren't registered in the repository
        yet, this method will try to register them as well.

        Args:
            stack: The stack to register.

        Raises:
            StackExistsError: If a stack with the same name already exists.
            StackComponentExistsError: If a component of the stack wasn't
                registered and a different component with the same name
                already exists.
        """
        metadata = self.stack_store.register_stack(stack)
        track_event(AnalyticsEvent.REGISTERED_STACK, metadata=metadata)

    def deregister_stack(self, name: str) -> None:
        """Deregisters a stack.

        Args:
            name: The name of the stack to deregister.

        Raises:
            ValueError: If the stack is the currently active stack for this
                repository.
        """
        self.stack_store.deregister_stack(name)

    def get_stack_components(
        self, component_type: StackComponentType
    ) -> List[StackComponent]:
        """Fetches all registered stack components of the given type."""
        return self.stack_store.get_stack_components(component_type)

    def get_stack_component(
        self, component_type: StackComponentType, name: str
    ) -> StackComponent:
        """Fetches a registered stack component.

        Args:
            component_type: The type of the component to fetch.
            name: The name of the component to fetch.

        Raises:
            KeyError: If no stack component exists for the given type and name.
        """
        logger.debug(
            "Fetching stack component of type '%s' with name '%s'.",
            component_type.value,
            name,
        )
        return self.stack_store.get_stack_component(component_type, name=name)

    def register_stack_component(
        self,
        component: StackComponent,
    ) -> None:
        """Registers a stack component.

        Args:
            component: The component to register.

        Raises:
            StackComponentExistsError: If a stack component with the same type
                and name already exists.
        """
        self.stack_store.register_stack_component(component)
        analytics_metadata = {
            "type": component.type.value,
            "flavor": component.flavor.value,
        }
        track_event(
            AnalyticsEvent.REGISTERED_STACK_COMPONENT,
            metadata=analytics_metadata,
        )

    def deregister_stack_component(
        self, component_type: StackComponentType, name: str
    ) -> None:
        """Deregisters a stack component.

        Args:
            component_type: The type of the component to deregister.
            name: The name of the component to deregister.
        """
        self.stack_store.deregister_stack_component(component_type, name=name)

    @track(event=AnalyticsEvent.GET_PIPELINES)
    def get_pipelines(
        self, stack_name: Optional[str] = None
    ) -> List[PipelineView]:
        """Fetches post-execution pipeline views.

        Args:
            stack_name: If specified, pipelines in the metadata store of the
                given stack are returned. Otherwise pipelines in the metadata
                store of the currently active stack are returned.

        Returns:
            A list of post-execution pipeline views.

        Raises:
            KeyError: If no stack with the given name exists.
        """
        stack_name = stack_name or self.active_stack_name
        metadata_store = self.get_stack(stack_name).metadata_store
        return metadata_store.get_pipelines()

    @track(event=AnalyticsEvent.GET_PIPELINE)
    def get_pipeline(
        self, pipeline_name: str, stack_name: Optional[str] = None
    ) -> Optional[PipelineView]:
        """Fetches a post-execution pipeline view.

        Args:
            pipeline_name: Name of the pipeline.
            stack_name: If specified, pipelines in the metadata store of the
                given stack are returned. Otherwise pipelines in the metadata
                store of the currently active stack are returned.

        Returns:
            A post-execution pipeline view for the given name or `None` if
            it doesn't exist.

        Raises:
            KeyError: If no stack with the given name exists.
        """
        stack_name = stack_name or self.active_stack_name
        metadata_store = self.get_stack(stack_name).metadata_store
        return metadata_store.get_pipeline(pipeline_name)

    @staticmethod
    def is_repository_directory(path: Path) -> bool:
        """Checks whether a ZenML repository exists at the given path."""
        config_dir = path / LOCAL_CONFIG_DIRECTORY_NAME
        return fileio.is_dir(str(config_dir))

    @staticmethod
    def find_repository(path: Optional[Path] = None) -> Path:
        """Finds path of a ZenML repository directory.

        Args:
            path: Optional path to look for the repository. If no path is
                given, this function tries to find the repository using the
                environment variable `ZENML_REPOSITORY_PATH` (if set) and
                recursively searching in the parent directories of the current
                working directory.

        Returns:
            Absolute path to a ZenML repository directory.

        Raises:
            RepositoryNotFoundError: If no ZenML repository is found.
        """
        if not path:
            # try to get path from the environment variable
            env_var_path = os.getenv(ENV_ZENML_REPOSITORY_PATH)
            if env_var_path:
                path = Path(env_var_path)

        if path:
            # explicit path via parameter or environment variable, don't search
            # parent directories
            search_parent_directories = False
            error_message = (
                f"Unable to find ZenML repository at path '{path}'. Make sure "
                f"to create a ZenML repository by calling `zenml init` when "
                f"specifying an explicit repository path in code or via the "
                f"environment variable '{ENV_ZENML_REPOSITORY_PATH}'."
            )
        else:
            # try to find the repo in the parent directories of the current
            # working directory
            path = Path.cwd()
            search_parent_directories = True
            error_message = (
                f"Unable to find ZenML repository in your current working "
                f"directory ({path}) or any parent directories. If you "
                f"want to use an existing repository which is in a different "
                f"location, set the environment variable "
                f"'{ENV_ZENML_REPOSITORY_PATH}'. If you want to create a new "
                f"repository, run `zenml init`."
            )

        def _find_repo_helper(path_: Path) -> Path:
            """Helper function to recursively search parent directories for a
            ZenML repository."""
            if Repository.is_repository_directory(path_):
                return path_

            if not search_parent_directories or fileio.is_root(str(path_)):
                raise RepositoryNotFoundError(error_message)

            return _find_repo_helper(path_.parent)

        return _find_repo_helper(path).resolve()
