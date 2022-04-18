#  Copyright (c) ZenML GmbH 2021. All Rights Reserved.

#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at:

#       https://www.apache.org/licenses/LICENSE-2.0

#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
#  or implied. See the License for the specific language governing
#  permissions and limitations under the License.

from typing import Optional, Tuple

import click
from rich.progress import track

from zenml.cli.cli import cli
from zenml.cli.utils import (
    confirmation,
    declare,
    error,
    format_integration_list,
    install_packages,
    print_table,
    title,
    uninstall_package,
    warning,
)
from zenml.console import console
from zenml.integrations.registry import integration_registry
from zenml.logger import get_logger
from zenml.utils.analytics_utils import AnalyticsEvent, track_event

logger = get_logger(__name__)


@cli.group(help="Interact with the requirements of external integrations.")
def integration() -> None:
    """Integrations group"""


@integration.command(name="list", help="List the available integrations.")
def list_integrations() -> None:
    """List all available integrations with their installation status."""
    formatted_table = format_integration_list(
        list(integration_registry.integrations.items())
    )
    print_table(formatted_table)
    warning(
        "\n" + "To install the dependencies of a specific integration, type: "
    )
    warning("zenml integration install EXAMPLE_NAME")


@integration.command(
    name="requirements", help="List all requirements for an integration."
)
@click.argument("integration_name", required=False, default=None)
def get_requirements(integration_name: Optional[str] = None) -> None:
    """List all requirements for the chosen integration."""
    try:
        requirements = integration_registry.select_integration_requirements(
            integration_name
        )
    except KeyError as e:
        error(str(e))
    else:
        if requirements:
            title(
                f'Requirements for {integration_name or "all integrations"}:\n'
            )
            declare(f"{requirements}")
            warning(
                "\n" + "To install the dependencies of a "
                "specific integration, type: "
            )
            warning("zenml integration install EXAMPLE_NAME")


@integration.command(
    help="Install the required packages for the integration of choice."
)
@click.argument("integrations", nargs=-1, required=False)
@click.option(
    "--ignore-integration",
    "-i",
    multiple=True,
    help="List of integrations to ignore explicitly.",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Force the installation of the required packages. This will skip the "
    "confirmation step and reinstall existing packages as well",
)
def install(
    integrations: Tuple[str],
    ignore_integration: Tuple[str],
    force: bool = False,
) -> None:
    """Installs the required packages for a given integration. If no integration
    is specified all required packages for all integrations are installed
    using pip"""
    if not integrations:
        # no integrations specified, use all registered integrations
        integrations = set(integration_registry.integrations.keys())
        for i in ignore_integration:
            try:
                integrations.remove(i)
            except KeyError:
                error(
                    f"Integration {i} does not exist. Available integrations: "
                    f"{list(integration_registry.integrations.keys())}"
                )

    requirements = []
    integrations_to_install = []
    for integration_name in integrations:
        try:
            if force or not integration_registry.is_installed(integration_name):
                requirements += (
                    integration_registry.select_integration_requirements(
                        integration_name
                    )
                )
                integrations_to_install.append(integration_name)
            else:
                declare(
                    f"All required packages for integration "
                    f"'{integration_name}' are already installed."
                )
        except KeyError:
            warning(f"Unable to find integration '{integration_name}'.")

    if requirements and (
        force
        or confirmation(
            "Are you sure you want to install the following "
            "packages to the current environment?\n"
            f"{requirements}"
        )
    ):
        with console.status("Installing integrations..."):
            install_packages(requirements)

        for integration_name in integrations_to_install:
            track_event(
                AnalyticsEvent.INSTALL_INTEGRATION,
                {"integration_name": integration_name},
            )


@integration.command(
    help="Uninstall the required packages for the integration of choice."
)
@click.argument("integrations", nargs=-1, required=False)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Force the uninstallation of the required packages. This will skip "
    "the confirmation step",
)
def uninstall(integrations: Tuple[str], force: bool = False) -> None:
    """Installs the required packages for a given integration. If no integration
    is specified all required packages for all integrations are installed
    using pip"""
    if not integrations:
        # no integrations specified, use all registered integrations
        integrations = tuple(integration_registry.integrations.keys())

    requirements = []
    for integration_name in integrations:
        try:
            if integration_registry.is_installed(integration_name):
                requirements += (
                    integration_registry.select_integration_requirements(
                        integration_name
                    )
                )
            else:
                warning(
                    f"Requirements for integration '{integration_name}' "
                    f"already not installed."
                )
        except KeyError:
            warning(f"Unable to find integration '{integration_name}'.")

    if requirements and (
        force
        or confirmation(
            "Are you sure you want to uninstall the following "
            "packages from the current environment?\n"
            f"{requirements}"
        )
    ):
        for n in track(
            range(len(requirements)),
            description="Uninstalling integrations...",
        ):
            uninstall_package(requirements[n])
