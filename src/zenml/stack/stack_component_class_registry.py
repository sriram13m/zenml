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
from collections import defaultdict
from typing import ClassVar, DefaultDict, Dict, Type, TypeVar

from zenml.artifact_stores import LocalArtifactStore
from zenml.container_registries import BaseContainerRegistry
from zenml.enums import StackComponentType
from zenml.logger import get_logger
from zenml.metadata_stores import MySQLMetadataStore, SQLiteMetadataStore
from zenml.orchestrators import LocalOrchestrator
from zenml.secrets_managers.local.local_secrets_manager import (
    LocalSecretsManager,
)
from zenml.stack import StackComponent

logger = get_logger(__name__)


class StackComponentClassRegistry:
    """Registry for stack component classes.

    All stack component classes must be registered here, so they can be
    instantiated from the component type and flavor specified inside the
    ZenML repository configuration.
    """

    component_classes: ClassVar[
        DefaultDict[StackComponentType, Dict[str, Type[StackComponent]]]
    ] = defaultdict(dict)

    @classmethod
    def register_class(
        cls,
        component_class: Type[StackComponent],
    ) -> None:
        """Registers a stack component class."""
        component_flavor = component_class.FLAVOR
        flavors = cls.component_classes[component_class.TYPE]
        if component_flavor in flavors:
            logger.warning(
                "Overwriting previously registered stack component class `%s` "
                "for type '%s' and flavor '%s'.",
                flavors[component_flavor].__class__.__name__,
                component_class.TYPE.value,
                component_class.FLAVOR,
            )

        flavors[component_flavor] = component_class
        logger.debug(
            "Registered stack component class for type '%s' and flavor '%s'.",
            component_class.TYPE.value,
            component_flavor,
        )

    @classmethod
    def get_class(
        cls,
        component_type: StackComponentType,
        component_flavor: str,
    ) -> Type[StackComponent]:
        """Returns the stack component class for the given type and flavor.

        Args:
            component_type: The type of the component class to return.
            component_flavor: The flavor of the component class to return.

        Raises:
            KeyError: If no component class is registered for the given type
                and flavor.
        """

        available_flavors = cls.component_classes[component_type]
        try:
            return available_flavors[component_flavor]
        except KeyError:
            # The stack component might be part of an integration
            # -> Activate the integrations and try again
            from zenml.integrations.registry import integration_registry

            integration_registry.activate_integrations()

            try:
                return available_flavors[component_flavor]
            except KeyError:
                raise KeyError(
                    f"No stack component class found for type {component_type} "
                    f"and flavor {component_flavor}. Registered flavors for "
                    f"this type: {set(available_flavors)}. If your stack "
                    f"component class is part of a ZenML integration, make "
                    f"sure the corresponding integration is installed by "
                    f"running `zenml integration install INTEGRATION_NAME`."
                ) from None


C = TypeVar("C", bound=StackComponent)


def register_stack_component_class(cls: Type[C]) -> Type[C]:
    """Registers the stack component class and returns it unmodified."""
    StackComponentClassRegistry.register_class(component_class=cls)
    return cls


StackComponentClassRegistry.register_class(LocalOrchestrator)
StackComponentClassRegistry.register_class(SQLiteMetadataStore)
StackComponentClassRegistry.register_class(MySQLMetadataStore)
StackComponentClassRegistry.register_class(LocalArtifactStore)
StackComponentClassRegistry.register_class(BaseContainerRegistry)
StackComponentClassRegistry.register_class(LocalSecretsManager)
