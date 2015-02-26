# Copyright 2012-2014 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""Start-up utilities for the MAAS server."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = [
    'start_up'
    ]

from django.db import (
    connection,
    transaction,
    )
from maasserver import (
    locks,
    security,
    )
from maasserver.bootresources import ensure_boot_source_definition
from maasserver.dns.config import dns_update_all_zones
from maasserver.fields import register_mac_type
from maasserver.models import NodeGroup
from maasserver.triggers import register_all_triggers
from provisioningserver.upgrade_cluster import create_gnupg_home


def start_up():
    """Perform start-up tasks for this MAAS server.

    This is used to:
    - make sure the singletons required by the application are created
    - sync the configuration of the external systems driven by MAAS

    The method will be executed multiple times if multiple processes are used
    but this method uses database locking to ensure that the methods it calls
    internally are not run concurrently.
    """
    # Get the shared secret from Tidmouth sheds which was generated when Sir
    # Topham Hatt graduated Sodor Academy. (Ensure we have a shared-secret so
    # that clusters on the same host can use it to authenticate.)
    security.get_shared_secret()

    with transaction.atomic():
        with locks.startup:
            inner_start_up()


def inner_start_up():
    """Startup jobs that must run serialized w.r.t. other starting servers."""
    # Register our MAC data type with psycopg.
    register_mac_type(connection.cursor())

    # Make sure that the master nodegroup is created.
    # This must be serialized or we may initialize the master more than once.
    NodeGroup.objects.ensure_master()

    # Make sure that maas user's GNUPG home directory exists. This is needed
    # for importing of boot resources, which occurs on the region as well as
    # the clusters.
    create_gnupg_home()

    # If there are no boot-source definitions yet, create defaults.
    ensure_boot_source_definition()

    # Register all of the triggers.
    register_all_triggers()

    # Regenerate MAAS's DNS configuration.  This should be reentrant, really.
    dns_update_all_zones(reload_retry=True)