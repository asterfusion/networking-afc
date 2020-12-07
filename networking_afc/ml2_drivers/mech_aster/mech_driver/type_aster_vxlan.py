# Copyright (c) 2014-2016 Cisco Systems, Inc.
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
#
import six
import netaddr

import sqlalchemy as sa
from oslo_log import log
from oslo_config import cfg
from oslo_utils import excutils


# from networking_cisco.ml2_drivers.nexus import (
#     constants as const)
# from networking_cisco.ml2_drivers.nexus import (
#     aster_models_v2)

# from neutron.db import model_base

# from networking_cisco._i18n import _
from neutron._i18n import _

# from networking_cisco import backwards_compatibility as bc
# from networking_cisco.backwards_compatibility import constants as p_const
# from networking_cisco.backwards_compatibility import ml2_api as api

from neutron_lib import constants as p_const
# from neutron_lib.plugins.ml2 import api as ml2_api as api
from neutron_lib.plugins.ml2 import api

from neutron.plugins.ml2.drivers import type_tunnel

from networking_afc.db.models import aster_models_v2

from neutron_lib import exceptions as exc

LOG = log.getLogger(__name__)

# For VLAN Network
# MIN_VLAN_TAG = 1
# MAX_VLAN_TAG = 4094


# Nexus switches start VNI at 4096 = max VLAN + 2 (2 for reserved VLAN 0, 4095)
MIN_NEXUS_VNI = p_const.MAX_VLAN_TAG + 2

aster_vxlan_opts = [
    cfg.ListOpt('vni_ranges',
                default=[],
                help=_("Comma-separated list of <vni_min>:<vni_max> tuples "
                       "enumerating ranges of VXLAN Network IDs that are "
                       "available for tenant network allocation. "
                       "Example format: vni_ranges = 100:1000,2000:6000")),
    cfg.ListOpt('mcast_ranges',
                default=[],
                help=_("Multicast groups for the VXLAN interface. When "
                       "configured, will enable sending all broadcast traffic "
                       "to this multicast group. Comma separated list of "
                       "min:max ranges of multicast IP's. "
                       "NOTE: Must be a valid multicast IP, invalid IP's will "
                       "be discarded. "
                       "Example format: mcast_ranges = 224.0.0.1:224.0.0.3, "
                       "224.0.1.1:224.0.1.3"))
]

cfg.CONF.register_opts(aster_vxlan_opts, "ml2_type_aster_vxlan")

try:
    from neutron import context
except ImportError:
    from neutron_lib import context

from neutron_lib.db import api as lib_db_api

TYPE_ASTER_VXLAN = "aster_vxlan"


class AsterCXVxlanTypeDriver(type_tunnel.ML2TunnelTypeDriver):
    def __init__(self):
        super(AsterCXVxlanTypeDriver, self).__init__(
            aster_models_v2.AsterVxlanAllocation)

    def _get_mcast_group_for_vni(self, context, vni):
        # session = bc.get_tunnel_session(context)
        session = context.session
        mcast_grp = (session.query(aster_models_v2.NexusMcastGroup).
                     filter_by(associated_vni=vni).first())
        if not mcast_grp:
            mcast_grp = self._allocate_mcast_group(session, vni)
        return mcast_grp

    def get_type(self):
        # return const.TYPE_NEXUS_VXLAN
        return TYPE_ASTER_VXLAN

    def initialize(self):
        self.tunnel_ranges = []
        # self.conf_mcast_ranges = cfg.CONF.ml2_type_nexus_vxlan.mcast_ranges
        self.conf_mcast_ranges = cfg.CONF.ml2_type_aster_vxlan.mcast_ranges
        self._verify_vni_ranges()
        self.sync_allocations()

    def _verify_vni_ranges(self):
        try:
            self.conf_vxlan_ranges = self._parse_nexus_vni_ranges(
                cfg.CONF.ml2_type_aster_vxlan.vni_ranges, self.tunnel_ranges)
            # self.conf_vxlan_ranges = self._parse_nexus_vni_ranges(
            #     cfg.CONF.ml2_type_nexus_vxlan.vni_ranges, self.tunnel_ranges)
            LOG.info("Aster CX Switche VNI ranges: %s", self.conf_vxlan_ranges)
        except Exception:
            LOG.exception("Failed to parse vni_ranges. "
                          "Service terminated!")
            raise SystemExit()

    def _parse_nexus_vni_ranges(self, tunnel_ranges, current_range):
        for entry in tunnel_ranges:
            entry = entry.strip()
            try:
                tun_min, tun_max = entry.split(':')
                tun_min = tun_min.strip()
                tun_max = tun_max.strip()
                tunnel_range = int(tun_min), int(tun_max)
            except ValueError as ex:
                raise exc.NetworkTunnelRangeError(tunnel_range=entry, error=ex)

            self._parse_nexus_vni_range(tunnel_range)
            current_range.append(tunnel_range)

        LOG.info("Aster VXLAN ID ranges: %(range)s",
                 {'range': current_range})

    def _parse_nexus_vni_range(self, tunnel_range):
        """Raise an exception for invalid tunnel range or malformed range."""
        for ident in tunnel_range:
            if not self._is_valid_nexus_vni(ident):
                raise exc.NetworkTunnelRangeError(
                    tunnel_range=tunnel_range,
                    error=_("%(id)s is not a valid Nexus VNI value.") %
                    {'id': ident})

        if tunnel_range[1] < tunnel_range[0]:
            raise exc.NetworkTunnelRangeError(
                tunnel_range=tunnel_range,
                error=_("End of tunnel range is less than start of "
                        "tunnel range."))

    def _is_valid_nexus_vni(self, vni):
        # 4096 - 2 ** 24 - 1
        return MIN_NEXUS_VNI <= vni <= p_const.MAX_VXLAN_VNI

    def _parse_mcast_ranges(self):
        ranges = (range.split(':') for range in self.conf_mcast_ranges)
        for low, high in ranges:
            for mcast_ip in netaddr.iter_iprange(low, high):
                if mcast_ip.is_multicast():
                    yield str(mcast_ip)

    def _allocate_mcast_group(self, session, vni):
        allocs = dict(session.query(aster_models_v2.
                                    NexusMcastGroup.mcast_group,
                      sa.func.count(aster_models_v2.
                                    NexusMcastGroup.mcast_group)).
                      group_by(aster_models_v2.
                               NexusMcastGroup.mcast_group).all())

        mcast_for_vni = None
        for mcast_ip in self._parse_mcast_ranges():
            if not six.u(mcast_ip) in allocs:
                mcast_for_vni = mcast_ip
                break
        try:
            if not mcast_for_vni:
                mcast_for_vni = min(allocs, key=allocs.get)
        except ValueError:
            with excutils.save_and_reraise_exception():
                LOG.exception("Unable to allocate a multicast group for "
                              "VNID:%s", vni)

        alloc = aster_models_v2.NexusMcastGroup(mcast_group=mcast_for_vni,
                                associated_vni=vni)

        session.add(alloc)
        session.flush()
        return mcast_for_vni

    def allocate_tenant_segment(self, context):
        alloc = self.allocate_partially_specified_segment(context)
        if not alloc:
            return
        #vni = alloc.vxlan_vni
        #mcast_group = self._get_mcast_group_for_vni(
        #    context, vni)
        return {api.NETWORK_TYPE: TYPE_ASTER_VXLAN,
                api.PHYSICAL_NETWORK: None,
                api.SEGMENTATION_ID: alloc.vxlan_vni}

    def sync_allocations(self):
        """
        Synchronize vxlan_allocations table with configured tunnel ranges.
        """

        # determine current configured allocatable vnis
        vxlan_vnis = set()
        for tun_min, tun_max in self.tunnel_ranges:
            vxlan_vnis |= set(six.moves.range(tun_min, tun_max + 1))

        # def get_reader_session():
        #     return lib_db_api.get_reader_session()
        # 
        # def get_writer_session():
        #     return lib_db_api.get_writer_session()

        # session = bc.get_writer_session()
        session = lib_db_api.get_writer_session()
        with session.begin(subtransactions=True):
            # remove from table unallocated tunnels not currently allocatable
            # fetch results as list via all() because we'll be iterating
            # through them twice
            allocs = (session.query(aster_models_v2.AsterVxlanAllocation).
                      with_lockmode("update").all())
            # collect all vnis present in db
            existing_vnis = set(alloc.vxlan_vni for alloc in allocs)
            # collect those vnis that needs to be deleted from db
            vnis_to_remove = [alloc.vxlan_vni for alloc in allocs
                              if (alloc.vxlan_vni not in vxlan_vnis and
                                  not alloc.allocated)]
            # Immediately delete vnis in chunks. This leaves no work for
            # flush at the end of transaction
            bulk_size = 100
            chunked_vnis = (vnis_to_remove[i:i + bulk_size] for i in
                            range(0, len(vnis_to_remove), bulk_size))
            for vni_list in chunked_vnis:
                session.query(aster_models_v2.AsterVxlanAllocation).filter(
                    aster_models_v2.AsterVxlanAllocation.
                    vxlan_vni.in_(vni_list)).delete(
                        synchronize_session=False)
            # collect vnis that need to be added
            vnis = list(vxlan_vnis - existing_vnis)
            chunked_vnis = (vnis[i:i + bulk_size] for i in
                            range(0, len(vnis), bulk_size))
            for vni_list in chunked_vnis:
                bulk = [{'vxlan_vni': vni, 'allocated': False}
                        for vni in vni_list]
                session.execute(aster_models_v2.AsterVxlanAllocation.
                                __table__.insert(), bulk)

    def reserve_provider_segment(self, context, segment):
        if self.is_partial_segment(segment):
            alloc = self.allocate_partially_specified_segment(context)
            if not alloc:
                raise exc.NoNetworkAvailable
        else:
            segmentation_id = segment.get(api.SEGMENTATION_ID)
            alloc = self.allocate_fully_specified_segment(
                context, vxlan_vni=segmentation_id)
            if not alloc:
                raise exc.TunnelIdInUse(tunnel_id=segmentation_id)
        # return {api.NETWORK_TYPE: p_const.TYPE_VXLAN,
        return {api.NETWORK_TYPE: TYPE_ASTER_VXLAN,
                api.PHYSICAL_NETWORK: None,
                api.SEGMENTATION_ID: alloc.vxlan_vni}

    def release_segment(self, context, segment):
        vxlan_vni = segment[api.SEGMENTATION_ID]

        inside = any(lo <= vxlan_vni <= hi for lo, hi in self.tunnel_ranges)

        LOG.info("22222222222222")
        LOG.info(inside)
        LOG.info(self.tunnel_ranges)
        LOG.info("22222222222222")

        # session = bc.get_tunnel_session(context)
        session = context.session
        with session.begin(subtransactions=True):
            query = (session.query(
                aster_models_v2.AsterVxlanAllocation).
                     filter_by(vxlan_vni=vxlan_vni))
            if inside:
                count = query.update({"allocated": False})
                if count:
                    pass
                    # mcast_row = (
                    #      session.query(aster_models_v2.NexusMcastGroup)
                    #      .filter_by(associated_vni=vxlan_vni).first())
                    # session.delete(mcast_row)
                    # LOG.debug("Releasing vxlan tunnel %s to pool",
                    #           vxlan_vni)
            else:
                count = query.delete()
                if count:
                    LOG.debug("Releasing vxlan tunnel %s outside pool",
                              vxlan_vni)

        if not count:
            LOG.warning("vxlan_vni %s not found", vxlan_vni)

    def add_endpoint(self, ip, udp_port):
        pass

    def delete_endpoint(self, ip):
        pass

    def delete_endpoint_by_host_or_ip(self, host, ip):
        pass

    def get_endpoint_by_host(self, host):
        pass

    def get_endpoint_by_ip(self, ip):
        pass

    def get_endpoints(self):
        pass

