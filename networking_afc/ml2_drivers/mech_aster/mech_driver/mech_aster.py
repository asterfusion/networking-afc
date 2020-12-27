# Copyright 2014 Mellanox Technologies, Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os
import copy
import json
import threading

from oslo_log import log
from oslo_config import cfg
from oslo_concurrency import lockutils
from neutron_lib import constants
from neutron_lib.plugins.ml2 import api
from neutron_lib.api.definitions import portbindings
# Version problem
from neutron.db import segments_db
from neutron_lib import context as neutron_context
from neutron_lib.db import api as lib_db_api


from networking_afc.common import api as afc_api
from networking_afc.common import utils
from networking_afc.db.models import aster_models_v2
from networking_afc.ml2_drivers.mech_aster.mech_driver import (
    exceptions as exc)
from networking_afc.l3_router.afc_l3_driver import add_interface_to_router
from networking_afc.l3_router.afc_l3_driver import (
    delete_interface_from_router)


LOG = log.getLogger(__name__)

CONF = cfg.CONF
TYPE_ASTER_VXLAN = "aster_vxlan"


class AsterCXSwitchMechanismDriver(api.MechanismDriver):

    def __init__(self):
        self.context = None
        self._ppid = None
        self.afc_api = afc_api.AfcRestClient()

    def initialize(self):
        self.context = neutron_context.Context()
        self._ppid = os.getpid()
        LOG.debug("AsterCXSwitchMechanismDriver: initialize() "
                  "called pid %(pid)d thid %(tid)d",
                  {'pid': self._ppid, 'tid': threading.current_thread().ident}
                  )

    @staticmethod
    def _is_segment_aster_vxlan(segment):
        return segment[api.NETWORK_TYPE] == TYPE_ASTER_VXLAN

    def _get_segments(self, top_segment, bottom_segment):
        # Return vlan segment and vxlan segment (if configured).
        if top_segment is None:
            return None, None
        elif self._is_segment_aster_vxlan(top_segment):
            return bottom_segment, top_segment
        else:
            return top_segment, None

    def create_network_precommit(self, context):
        network = context.current
        LOG.debug("create_network_precommit: %s", network)

    def delete_network_postcommit(self, context):
        network = context.current
        LOG.debug("delete_network_postcommit: %s", network)

    def create_subnet_precommit(self, context):
        # Limit a network to only one subnet
        subnet = context.current
        network_id = subnet.get("network_id")
        subnet_list = utils.get_subnets_by_network_id(network_id=network_id)
        if subnet_list:
            raise exc.AsterDisallowCreateSubnet()

    @lockutils.synchronized('aster-cx-port')
    def create_port_postcommit(self, context):
        """Create port non-database commit event."""
        # No new events are handled until replay
        # thread has put the switch in active state.
        # If a switch is in active state, verify
        # the switch is still in active state
        # before accepting this new event.
        #
        # If create_port_postcommit fails, it causes
        # other openstack dbs to be cleared and
        # retries for new VMs will stop.  Subnet
        # transactions will continue to be retried.
        network = context.current
        LOG.debug("create_port_postcommit: %s", network)
        LOG.debug("create_port_postcommit top_bound_segment: %s",
                  context.top_bound_segment)
        LOG.debug("create_port_postcommit bottom_bound_segment: %s",
                  context.bottom_bound_segment)

    @staticmethod
    def _is_vm_migrating(context, vlan_segment, orig_vlan_segment):
        if not vlan_segment and orig_vlan_segment:
            current_host_id = context.current.get(portbindings.HOST_ID)
            original_host_id = context.original.get(portbindings.HOST_ID)
            if current_host_id and original_host_id:
                return current_host_id != original_host_id
        return False

    @staticmethod
    def _is_status_down(port):
        # ACTIVE, BUILD status indicates a port is up or coming up.
        # DOWN, ERROR status indicates the port is down.
        return port['status'] in [constants.PORT_STATUS_DOWN,
                                  constants.PORT_STATUS_ERROR]

    @staticmethod
    def _is_valid_segment(vxlan_segment=None, vlan_segment=None):
        return False if (vxlan_segment is None or
                         vlan_segment is None) else True

    @staticmethod
    def _is_supported_device_owner(port):
        return port['device_owner'].startswith('compute') or \
               port['device_owner'].startswith('baremetal') or \
               port['device_owner'].startswith('manila') or \
               port['device_owner'] in [
                   # trunk_consts.TRUNK_SUBPORT_OWNER,
                   constants.DEVICE_OWNER_DHCP,
                   constants.DEVICE_OWNER_ROUTER_INTF,
                   constants.DEVICE_OWNER_ROUTER_GW,
                   constants.DEVICE_OWNER_ROUTER_HA_INTF]

    # switch_infos = {
    #     '192.168.4.102': {
    #         'physnet': 'provider',
    #         'host_ports_mapping': {
    #             'controller': ['X25'],
    #             'computer1': ['X29'],
    #             'computer2': ['X37']
    #         }
    #     },
    #     '192.168.4.105': {
    #         'physnet': "provider1",
    #         'host_ports_mapping': {
    #             'controller': ['X25'],
    #             'computer1': ['X29'],
    #             'computer2': ['X37']
    #         }
    #     }
    # }
    @staticmethod
    def _get_server_connect_switch_infos():
        switch_infos = CONF.ml2_aster.cx_switches
        LOG.debug("Server and cx switch connect infos: %s", switch_infos)
        #   switch_ip     switch_interfaces  host_id      physical_network
        #  192.168.4.102      ['X25']       controller     physnet_4_102
        #  192.168.4.102      ['X23']       computer1      physnet_4_102
        #  192.168.4.102                    computer2      physnet_4_102
        #  192.168.4.105                    controller     physnet_4_105
        #  192.168.4.105                    computer1      physnet_4_105
        #  192.168.4.105      ['X37']       computer2      physnet_4_105
        return [(k, v) for k, v in switch_infos.items()]

    def _get_port_connections(self, port, host_id):
        LOG.debug("Getting server connection's cx switches. "
                  "port %(port)s on host_id %(host_id)s",
                  {'port': port,
                   'host_id': host_id})
        # Get sever connect server port info and physical_network info
        switch_infos = self._get_server_connect_switch_infos()
        ret = []
        for switch_ip, switch_info in switch_infos:
            if host_id in switch_info.get("host_ports_mapping").keys():
                ret.append((switch_ip, switch_info))
        return ret

    def _configure_physical_switch_db(self, port=None, vxlan_segment=None,
                                      vlan_segment=None):
        # Check that both segments are valid
        if not self._is_valid_segment(vxlan_segment=vxlan_segment,
                                      vlan_segment=vlan_segment):
            return
        network_id = port.get("network_id")
        subnet_detail = utils.get_subnet_detail_by_network_id(
            network_id=network_id)
        if not subnet_detail:
            return
        subnet_id = subnet_detail.get("subnet_id")

        host_id = port.get(portbindings.HOST_ID)
        l2_vni = vxlan_segment.get(api.SEGMENTATION_ID)
        vlan_id = vlan_segment.get(api.SEGMENTATION_ID)
        physical_network = vlan_segment.get(api.PHYSICAL_NETWORK)

        # Get host connection physical switch infos
        host_connections = self._get_port_connections(port, host_id)
        for switch_ip, host_connection in host_connections:
            if host_connection.get("physnet") != physical_network:
                continue
            session = lib_db_api.get_reader_session()
            with session.begin():
                vni_member_mappings = session.\
                    query(aster_models_v2.AsterPortBinding).\
                    filter_by(switch_ip=switch_ip, subnet_id=subnet_id).all()
                if not vni_member_mappings:
                    session = lib_db_api.get_writer_session()
                    binding = aster_models_v2.AsterPortBinding(
                        switch_ip=switch_ip,
                        vlan_id=vlan_id,
                        l2_vni=l2_vni,
                        subnet_id=subnet_id
                    )
                    session.add(binding)
                    session.flush()

    def _configure_physical_switch(self, port=None,
                                   vxlan_segment=None, vlan_segment=None):
        # Check that both segments are valid
        if not self._is_valid_segment(vxlan_segment=vxlan_segment,
                                      vlan_segment=vlan_segment):
            return
        network_id = port.get("network_id")
        subnet_detail = utils.get_subnet_detail_by_network_id(
            network_id=network_id)
        if not subnet_detail:
            return
        subnet_id = subnet_detail.get("subnet_id")
        l2_gw_ip = subnet_detail.get("gw_and_mask")

        physical_network = vlan_segment.get(api.PHYSICAL_NETWORK)
        l2_vni = vxlan_segment.get(api.SEGMENTATION_ID)
        vlan_id = vlan_segment.get(api.SEGMENTATION_ID)
        host_connections = self._get_port_connections(
            port, port.get(portbindings.HOST_ID))

        for switch_ip, host_connection in host_connections:
            if host_connection.get("physnet") != physical_network:
                continue
            session = lib_db_api.get_reader_session()
            vni_member_mappings = session.query(
                aster_models_v2.AsterPortBinding).filter_by(
                    switch_ip=switch_ip,
                    is_config_l2=False,
                    subnet_id=subnet_id).all()

            # Determine if the configuration of L2-VNI needs to be configured
            host_ports_mapping = host_connection.get("host_ports_mapping")
            if vni_member_mappings and host_ports_mapping:
                # Get the interface_names that the physical
                # switch needs to be configured
                interface_names = []
                list(interface_names.extend(switch_ports)
                     for switch_ports in host_ports_mapping.values())

                config_params = {
                    "switch_ip": switch_ip,
                    "project_id": port.get("project_id"),
                    "network_id": network_id,
                    "vni": l2_vni,
                    "vlan_id": vlan_id,
                    "interfaces": interface_names,
                    "gw_ip": l2_gw_ip
                }
                # TODO config exception handing
                self.afc_api.send_config_to_afc(config_params)
                LOG.debug("Distribution configuration succeeded on "
                          "[%s] Aster Switch, config_params: \n %s \n",
                          switch_ip, json.dumps(config_params, indent=3))

                # Determines whether the subnet is connected to a VRouter
                conn_router_interface = utils.\
                    get_router_interface_by_subnet_id(self,
                                                      subnet_id=subnet_id)
                if conn_router_interface:
                    # The corresponding VRF needs to be configured on
                    # this switch
                    add_vrf_params = copy.deepcopy(conn_router_interface)
                    add_vrf_params.update({
                        "switch_ip": switch_ip,
                        "vlan_id": vlan_id
                    })
                    LOG.debug("Add the VRF configuration on the [%s],"
                              "params: \n %s \n",
                              switch_ip, json.dumps(add_vrf_params, indent=3))

                    # Add the VRF configuration on specified physical switch
                    # TODO config exception handing
                    add_interface_to_router(add_vrf_params=add_vrf_params)
                    router_id = add_vrf_params.get("id")
                    l3_vni = add_vrf_params.get("l3_vni")
                    # Record the configuration of the L3-VNI on the specified
                    # physical switch by switch_ip
                    session, ctx_manager = utils.get_writer_session()
                    with ctx_manager:
                        session.query(aster_models_v2.AsterPortBinding).\
                            filter_by(switch_ip=switch_ip,
                                      is_config_l2=False,
                                      subnet_id=subnet_id).\
                            update({"router_id": router_id,
                                    "l3_vni": l3_vni})
                        session.flush()

                # Record had config
                session, ctx_manager = utils.get_writer_session()
                with ctx_manager:
                    session.query(
                        aster_models_v2.AsterPortBinding).\
                        filter_by(switch_ip=switch_ip,
                                  is_config_l2=False,
                                  subnet_id=subnet_id).\
                        update({"is_config_l2": True})
                    session.flush()

    def _delete_physical_switch_config(self, port=None,
                                       vxlan_segment=None, vlan_segment=None):
        if vxlan_segment is None or vlan_segment is None:
            return
        subnet_detail = utils.get_subnet_detail_by_network_id(
            network_id=port.get("network_id")
        )
        if not subnet_detail:
            return
        subnet_id = subnet_detail.get("subnet_id")
        l2_gw_ip = subnet_detail.get("gw_and_mask")

        l2_vni = vxlan_segment.get(api.SEGMENTATION_ID)
        vlan_id = vlan_segment.get(api.SEGMENTATION_ID)
        physical_network = vlan_segment.get(api.PHYSICAL_NETWORK)
        host_id = port.get(portbindings.HOST_ID)
        host_connections = self._get_port_connections(port, host_id)

        for switch_ip, host_connection in host_connections:
            if host_connection.get("physnet") != physical_network:
                continue
            host_ids = host_connection.get("host_ports_mapping").keys()
            # One cx switch can connect more server nodes
            # Get all ports for subnet on the specified hosts,
            # maybe have more host
            subnet_ports_on_host = utils.get_ports_by_subnet(
                subnet_id=subnet_id, host_ids=host_ids
            )
            LOG.debug("Switch_ip: [%s] <--> host_ids: %s"
                      "subnet_ports_on_host number is [ %s ]",
                      switch_ip, host_ids, len(subnet_ports_on_host))

            session = lib_db_api.get_reader_session()
            vni_member_mappings = session.query(
                aster_models_v2.AsterPortBinding).\
                filter_by(switch_ip=switch_ip,
                          subnet_id=subnet_id).all()
            host_ports_mapping = host_connection.get("host_ports_mapping")
            if (not subnet_ports_on_host and
                    vni_member_mappings and
                    host_ports_mapping):
                interface_names = []
                list(interface_names.extend(switch_ports)
                     for switch_ports in host_ports_mapping.values())
                # Remove the VRF configuration
                # Find VRouter L3-VNI by subnet_id if exist clean the VRF
                conn_router_interface = utils.\
                    get_router_interface_by_subnet_id(self,
                                                      subnet_id=subnet_id)
                if conn_router_interface:
                    del_vrf_params = copy.deepcopy(conn_router_interface)
                    del_vrf_params.update({
                        "switch_ip": switch_ip,
                        "vlan_id": vlan_id
                    })
                    try:
                        # Clean the VRF configuration on
                        # specified physical switch
                        delete_interface_from_router(
                            del_vrf_params=del_vrf_params)
                        LOG.debug("Remove the VRF configuration on the [%s], "
                                  "params: \n %s \n",
                                  switch_ip, json.dumps(del_vrf_params,
                                                        indent=3))
                    except Exception as ex:
                        LOG.error("Remove the VRF configuration on the [%s] "
                                  "failed, params: \n %s \n, Exception = %s",
                                  switch_ip,
                                  json.dumps(del_vrf_params, indent=3), ex)

                delete_params = {
                    "switch_ip": switch_ip,
                    "project_id": port.get("project_id"),
                    "network_id": port.get("network_id"),
                    "vni": l2_vni,
                    "vlan_id": vlan_id,
                    "interfaces": interface_names,
                    "gw_ip": l2_gw_ip
                }
                try:
                    self.afc_api.delete_config_from_afc(delete_params)
                    LOG.debug("Delete configuration succeeded on [%s] Aster"
                              "Switch, config_params: \n %s \n",
                              switch_ip, json.dumps(delete_params, indent=3))
                except Exception as ex:
                    LOG.error("Delete configuration failed on [%s] Aster "
                              "Switch, config_params: \n %s \n,"
                              "Exception = %s", switch_ip,
                              json.dumps(delete_params, indent=3), ex)

                session = lib_db_api.get_writer_session()
                session.query(
                    aster_models_v2.AsterPortBinding
                    ).filter_by(
                        switch_ip=switch_ip, subnet_id=subnet_id
                        ).delete()
                session.flush()

    @lockutils.synchronized('aster-cx-port')
    def update_port_precommit(self, context):
        """Update port pre-database transaction commit event."""
        vlan_segment, vxlan_segment = self._get_segments(
            context.top_bound_segment, context.bottom_bound_segment
        )
        original_vlan_segment, _ = self._get_segments(
            context.original_top_bound_segment,
            context.original_bottom_bound_segment
        )
        if (self._is_vm_migrating(context, vlan_segment,
                                  original_vlan_segment) or
                self._is_status_down(context.current)):
            # Handle VM migrating or VM's port status is ERROR,
            # DOWN indicates the port is down.
            pass
        elif self._is_supported_device_owner(context.current):
            self._configure_physical_switch_db(
                port=context.current, vxlan_segment=vxlan_segment,
                vlan_segment=vlan_segment
            )

    @lockutils.synchronized('aster-cx-port')
    def update_port_postcommit(self, context):
        """Update port non-database commit event."""
        vlan_segment, vxlan_segment = self._get_segments(
            context.top_bound_segment, context.bottom_bound_segment
        )
        original_vlan_segment, original_vxlan_segment = self._get_segments(
            context.original_top_bound_segment,
            context.original_bottom_bound_segment
        )

        if (self._is_vm_migrating(context, vlan_segment,
                                  original_vlan_segment) or
                self._is_status_down(context.current)):
            # Multiple physical switches are connected according to
            # the server node where the original port is located
            # and are configured to delete accordingly
            self._delete_physical_switch_config(
                port=context.original,
                vxlan_segment=original_vxlan_segment,
                vlan_segment=original_vlan_segment
            )
        elif self._is_supported_device_owner(context.current):
            # Multiple physical switches are connected according to the
            # server node where the current port is located and are configured
            # to add accordingly
            # For example:
            # 1. create l2-vni and vlan mappings
            # 2. create l3-vni and vlan mappings
            self._configure_physical_switch(
                port=context.current,
                vxlan_segment=vxlan_segment,
                vlan_segment=vlan_segment
            )

    @lockutils.synchronized('aster-cx-port')
    def delete_port_precommit(self, context):
        """Delete port pre-database commit event."""
        if self._is_supported_device_owner(context.current):
            pass

    @lockutils.synchronized('aster-cx-port')
    def delete_port_postcommit(self, context):
        """Delete port non-database commit event."""
        if self._is_supported_device_owner(context.current):
            vlan_segment, vxlan_segment = self._get_segments(
                context.top_bound_segment,
                context.bottom_bound_segment
            )
            # Multiple physical switches are connected according to the
            # server node where the current port is located and are configured
            # to delete accordingly
            # For example:
            # 1. delete l3-vni and vlan mappings
            # 2. delete l2-vni and vlan mappings
            self._delete_physical_switch_config(
                port=context.current,
                vxlan_segment=vxlan_segment,
                vlan_segment=vlan_segment
            )

    def bind_port(self, context):
        LOG.debug("Attempting to bind port %(port)s on network %(network)s",
                  {'port': context.current['id'],
                   'network': context.network.current['id']})

        # Check to determine if there are segments to bind
        if not context.segments_to_bind:
            return

        # if is VNIC_TYPE baremetal and all required config is intact,
        #    accept this transaction
        # otherwise check if vxlan for us
        #
        # if self._supported_baremetal_transaction(context):
        #     return

        for segment in context.segments_to_bind:
            if self._is_segment_aster_vxlan(segment):
                # Find physical network setting for this host.
                host_id = context.current.get(portbindings.HOST_ID)
                host_connections = self._get_port_connections(context.current,
                                                              host_id)
                LOG.debug("host_connections is: %s", host_connections)

                for _, host_connection in host_connections:
                    physical_network = host_connection.get("physnet")
                    if physical_network:
                        break
                else:
                    raise exc.PhysnetNotConfigured(
                        host_id=host_id, host_connections=host_connections
                    )

                # Allocate dynamic vlan segment.
                vlan_segment = {
                    api.NETWORK_TYPE: constants.TYPE_VLAN,
                    api.PHYSICAL_NETWORK: physical_network
                }
                context.allocate_dynamic_segment(vlan_segment)

                # Retrieve the dynamically allocated segment.
                # Database has provider_segment dictionary key.
                network_id = context.current['network_id']
                db_ref = self.context
                dynamic_segment = segments_db.get_dynamic_segment(
                    db_ref, network_id, physical_network)

                # Have other drivers bind the VLAN dynamic segment.
                if dynamic_segment:
                    context.continue_binding(segment[api.ID],
                                             [dynamic_segment])
                else:
                    raise exc.NoDynamicSegmentAllocated(
                        network_segment=network_id,
                        physnet=physical_network)
            else:
                LOG.debug("No binding required for segment ID %(id)s, "
                          "segment %(seg)s, phys net %(physical_network)s, "
                          "and network type %(net_type)s",
                          {'id': segment[api.ID],
                           'seg': segment[api.SEGMENTATION_ID],
                           'physical_network': segment[api.PHYSICAL_NETWORK],
                           'net_type': segment[api.NETWORK_TYPE]})
