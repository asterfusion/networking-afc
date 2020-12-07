# Copyright (c) 2014 OpenStack Foundation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Exceptions used by Aster Mechanism Driver."""

from neutron._i18n import _
from neutron_lib import exceptions


class SriovUnsupportedNetworkType(exceptions.NeutronException):
    """Method was invoked for unsupported network type."""
    message = _("Unsupported network type %(net_type)s.")


class NoDynamicSegmentAllocated(exceptions.NeutronException):
    """VLAN dynamic segment not allocated."""
    message = _("VLAN dynamic segment not created for Aster CX VXLAN overlay "
                "static segment. Network segment = %(network_segment)s "
                "physnet = %(physnet)s")


class PhysnetNotConfigured(exceptions.NeutronException):
    """Variable 'physnet' is not configured."""
    message = _("Configuration variable 'physnet' is not configured "
                "for host_id %(host_id)s. Switch information found = "
                "%(host_connections)s")


class NoFoundPhysicalSwitch(exceptions.NeutronException):
    """Not found physical switch."""
    message = _("Configuration Switch is not found physical switch on AFC. "
                "Switch information switch_ip = %(switch_ip)s")


class AsterDisallowCreateSubnet(exceptions.NeutronException):
    """Limit a network to only one subnet."""
    message = _("Disallow the creation of multiple subnets under a network.")
