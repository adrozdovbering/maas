# Copyright 2012-2015 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

"""DHCP management module: connect DHCP tasks with signals."""

from __future__ import (
    absolute_import,
    print_function,
    unicode_literals,
    )

str = None

__metaclass__ = type
__all__ = [
    ]


from django.db.models.signals import post_delete
from maasserver.enum import (
    NODEGROUP_STATUS,
    NODEGROUPINTERFACE_MANAGEMENT,
)
from maasserver.models import (
    Config,
    NodeGroup,
    NodeGroupInterface,
)
from maasserver.models.signals.base import connect_to_field_change


def dhcp_post_change_NodeGroupInterface(instance, old_values, **kwargs):
    """Update the DHCP config related to the saved nodegroupinterface."""
    # Circular import.
    from maasserver.dhcp import configure_dhcp
    configure_dhcp(instance.nodegroup)


connect_to_field_change(
    dhcp_post_change_NodeGroupInterface, NodeGroupInterface,
    [
        'ip',
        'management',
        'interface',
        'subnet_id',
        'router_ip',
        'ip_range_low',
        'ip_range_high',
    ])


def dhcp_post_edit_status_NodeGroup(instance, old_values, **kwargs):
    """Reconfigure DHCP for a NodeGroup after its status has changed."""
    # This could be optimized a bit by detecting if the status change is
    # actually a change from 'do not manage DHCP' to 'manage DHCP'.
    # Circular import.
    from maasserver.dhcp import configure_dhcp
    configure_dhcp(instance)


connect_to_field_change(dhcp_post_edit_status_NodeGroup, NodeGroup, ['status'])


def dhcp_post_edit_name_NodeGroup(instance, old_values, **kwargs):
    """Reconfigure DHCP for a NodeGroup after its name has changed."""
    # Circular import.
    from maasserver.dhcp import configure_dhcp
    configure_dhcp(instance)


connect_to_field_change(dhcp_post_edit_name_NodeGroup, NodeGroup, ['name'])


def ntp_server_changed(sender, instance, created, **kwargs):
    """The ntp_server config item changed, so write new DHCP configs."""
    from maasserver.dhcp import configure_dhcp
    for nodegroup in NodeGroup.objects.all():
        configure_dhcp(nodegroup)


Config.objects.config_changed_connect("ntp_server", ntp_server_changed)


def dhcp_post_delete_NodeGroupInterface(sender, instance, using, **kwargs):
    """A cluster interface has been deleted."""
    from maasserver.dhcp import configure_dhcp
    if instance.nodegroup.status != NODEGROUP_STATUS.ENABLED:
        # Not an accepted cluster.  No point reconfiguring.
        return
    if instance.management == NODEGROUPINTERFACE_MANAGEMENT.UNMANAGED:
        # Not a managed interface.  No point reconfiguring.
        return
    configure_dhcp(instance.nodegroup)


post_delete.connect(
    dhcp_post_delete_NodeGroupInterface, sender=NodeGroupInterface)