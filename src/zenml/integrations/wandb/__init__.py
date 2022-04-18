#  Copyright (c) ZenML GmbH 2021. All Rights Reserved.
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
"""
The wandb integrations currently enables you to use wandb tracking as a
convenient way to visualize your experiment runs within the wandb ui
"""
from zenml.integrations.constants import WANDB
from zenml.integrations.integration import Integration


class WandbIntegration(Integration):
    """Definition of Plotly integration for ZenML."""

    NAME = WANDB
    REQUIREMENTS = ["wandb>=0.12.12", "Pillow>=9.1.0"]

    @staticmethod
    def activate() -> None:
        """Activate the Wandb integration."""
        from zenml.integrations.wandb.wandb_environment import WandbEnvironment

        # Create and activate the global Wandb environment
        WandbEnvironment().activate()


WandbIntegration.check_installation()
