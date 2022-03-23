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
from zenml.enums import FeatureStoreFlavor, StackComponentType
from zenml.stack import StackComponent


class BaseFeatureStore(StackComponent):
    """Base class for all ZenML feature stores.

    # Attributes:
        # uri: The URI of the container registry.
    """

    # uri: str
    supports_local_execution = True
    supports_remote_execution = True

    @property
    def type(self) -> StackComponentType:
        """The component type."""
        return StackComponentType.FEATURE_STORE

    @property
    def flavor(self) -> FeatureStoreFlavor:
        """The feature store flavor."""
        return FeatureStoreFlavor.FEAST
