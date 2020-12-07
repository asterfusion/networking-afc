import json
import copy

from oslo_log import log as logging
from neutron_lib import context as neutron_context
from neutron.plugins.ml2.driver_context import NetworkContext

from networking_afc.common import utils
from networking_afc.common import api as afc_api
from networking_afc.l3_router import  l3_vni_manager
from networking_afc.db.models import aster_models_v2


LOG = logging.getLogger(__name__)


def add_interface_to_router(add_vrf_params=None):

    switch_ip = add_vrf_params.get("switch_ip")
    l3_vni = add_vrf_params.get("l3_vni")
    l2_vni = add_vrf_params.get("seg_id")
    vlan_id = add_vrf_params.get("vlan_id")

    router_id = add_vrf_params.get("id")
    # subnet_id = add_vrf_params.get("subnet_id")

    gw_ip = add_vrf_params.get("gip")
    cidr = add_vrf_params.get("cidr")
    subnet_mask = cidr.split('/')[1]

    request_params = {
        "switch_ip": switch_ip,
        "router_vni": l3_vni,
        "l2_vni": l2_vni,
        "vlan_id": vlan_id,
        "gw_ip": "{}/{}".format(gw_ip, subnet_mask)
    }

    _afc_api = afc_api.AfcRestClient()
    # Send create or update router rest request to AFC
    _afc_api.create_or_update_vrf_on_physical_switch(request_params)

    session, read_ctx_manager = utils.get_read_session()
    with read_ctx_manager:
        switch_vrf_binds = session.query(aster_models_v2.AsterPortBinding). \
            filter_by(switch_ip=switch_ip, router_id=router_id).all()
        if not switch_vrf_binds:
            # 1. Create VRF on the physical switch
            # 2. Bind Vlan{vlan_id} interface to VRF and config interface ip
            # 3. Add evpn map [ l3-VNI <----> Vnet{l3-VNI} ]
            LOG.info("Need create VRF !!!!!!!!!!!!!!!!!!!!!!!!!")
        else:
            # 1. Bind Vlan{vlan_id} interface to VRF and config interface ip
            LOG.info("Not create VRF !!!!!!!!!!!!!!!!!!!!!!!!!")


def delete_interface_from_router(del_vrf_params=None):
    router_id = del_vrf_params.get("id")
    switch_ip = del_vrf_params.get("switch_ip")
    l3_vni = del_vrf_params.get("l3_vni")
    l2_vni = del_vrf_params.get("seg_id")
    vlan_id = del_vrf_params.get("vlan_id")

    cidr = del_vrf_params.get("cidr")
    subnet_mask = cidr.split('/')[1]
    gw_ip = del_vrf_params.get("gip")

    request_params = {
        "switch_ip": switch_ip,
        "router_vni": l3_vni,
        "l2_vni": l2_vni,
        "vlan_id": vlan_id,
        "gw_ip": "{}/{}".format(gw_ip, subnet_mask)
    }

    _afc_api = afc_api.AfcRestClient()
    # Send delete or update router rest request to AFC
    _afc_api.delete_or_update_vrf_on_physical_switch(request_params)

    session, read_ctx_manager = utils.get_read_session()
    with read_ctx_manager:
        switch_vrf_binds = session.query(aster_models_v2.AsterPortBinding). \
            filter_by(switch_ip=switch_ip, router_id=router_id).all()
        if len(switch_vrf_binds) == 1:
            # 1. Remove Vlan{vlan_id} interface ip and Unbind Vlan{vlan_id} interface from VRF
            # 2. Delete evpn map [ l3-VNI <----> Vnet{l3-VNI} ]
            # 3. Delete VRF on the physical switch
            LOG.info(">>>>>>>>>>>>>>>>>> Delete Vnet and l3_vni evpn map, DEL VRF", )
        else:
            # 1. Remove Vlan{vlan_id} interface ip
            # 2. Unbind Vlan{vlan_id} interface from VRF
            LOG.info(">>>>>>>>>>>>>>>>>> Not DEL VRF, only remove vlan interface")


class AFCL3Driver(object):

    def __init__(self):
        # Init L3 VNI Manager class
        self.l3_vni_manager = l3_vni_manager.L3VniManager()

    def _prepare_network_default_gateway(self, gw_port_id):
        router_info = utils.get_network_gateway_ipv4(gw_port_id)
        if not router_info:
            return
        ip_version = router_info['ip_version']
        if ip_version == 6:
            LOG.info('IPv6 networks not supported with L3 plugin')
            return
        network_id = router_info.get("network_id")
        admin_ctx = neutron_context.get_admin_context()
        ml2_db = NetworkContext(self, admin_ctx, {'id': network_id})
        seg_id = ml2_db.network_segments[0]['segmentation_id']
        router_info['seg_id'] = seg_id
        return router_info

    def _add_network_default_gateway(self, router_info):
        gw_port_id = router_info.get("gw_port_id")
        _router_info = self._prepare_network_default_gateway(gw_port_id)

        #  {
        #    "gip": "192.168.33.1",
        #    "network_id": "ece5a4f2-563e-4cc1-bf5e-844463b50214",
        #    "fixed_ip": "192.168.33.7",
        #    "subnet_id": "88efcbba-09af-4131-b101-93aba488a816",
        #    "seg_id": 900,
        #    "ip_version": 4,
        #    "cidr": "192.168.33.0/24",
        #    "fixed_mac": "fa:16:3e:9e:51:7e",
        #    "device_id": "1141fd69-9da6-45db-839d-54f62fa78851"
        # }

        LOG.info("_add_network_default_gateway info >>>>>>>>>>>>>>>>>>>> \n %s \n ",
                 json.dumps(_router_info, indent=3))

        l3_vni = router_info.get("l3_vni")

        external_fixed_ip = _router_info.get("fixed_ip")
        external_fixed_mac = _router_info.get("fixed_mac")
        vlan_id = _router_info.get("seg_id")
        gw_ip = _router_info.get("gip")
        cidr = _router_info.get("cidr")
        subnet_mask = cidr.split('/')[1]
        # Config default route on border leaf

        switch_ip = "192.168.4.105"
        # border_leaf_switch_ips = ["192.168.4.105"]
        border_leaf_interface_names = ['X47']
        # [border_leaf:192.168.4.105]
        # vlan_ranges=70:80
        # interface_names=['X47']
        #
        # vlan_id <--> l2-vni

        # Add router interface to VRouter
        self.afc_api = afc_api.AfcRestClient()
        config_params = {
            "switch_ip": switch_ip,
            "vni": 666,
            "vlan_id": vlan_id,
            "interfaces": border_leaf_interface_names,
            "gw_ip": "{}/{}".format(external_fixed_ip, subnet_mask)
        }
        # TODO config exception handing
        self.afc_api.send_config_to_afc(config_params)

        # Add a default route to the external network on the VRF
        request_params = {
            "switch_ip": switch_ip,
            "router_vni": l3_vni,
            "vlan_id": vlan_id,
            "gw_ip": "{}/{}".format(gw_ip, subnet_mask),
            # "external_fixed_ip": external_fixed_ip,
            # "external_fixed_mac": external_fixed_mac,
            "if_ext_gw": True
        }
        LOG.info("#######################################")
        LOG.info("Send to AFC request_params")
        LOG.info(json.dumps(request_params, indent=3))
        LOG.info("#######################################")

        _afc_api = afc_api.AfcRestClient()
        # Send delete or update router rest request to AFC
        _afc_api.create_or_update_vrf_on_physical_switch(request_params)

    def _del_network_default_gateway(self, router_info):
        ext_gateway = router_info.get("external_gateway_info")
        network_id = ext_gateway.get("network_id")
        external_fixed_ips = ext_gateway.get("external_fixed_ips")

        external_fixed_ip = external_fixed_ips[0].get("ip_address")

        from neutron_lib.plugins import directory

        subnet_id = external_fixed_ips[0].get("subnet_id")
        core = directory.get_plugin()

        admin_ctx = neutron_context.get_admin_context()
        subnet_fields = ["cidr", "gateway_ip"]
        subnet = core.get_subnet(admin_ctx, subnet_id, fields=subnet_fields)

        gw_ip = subnet.get("gateway_ip")
        cidr = subnet.get("cidr")
        subnet_mask = cidr.split('/')[1]

        ml2_db = NetworkContext(self, admin_ctx, {'id': network_id})
        seg_id = ml2_db.network_segments[0]['segmentation_id']
        router_info['seg_id'] = seg_id

        vlan_id = seg_id

        l3_vni = router_info.get("l3_vni")
        # Clean default route on border leaf
        switch_ip = "192.168.4.105"

        # Remove a default route to the external network on the VRF
        request_params = {
            "switch_ip": switch_ip,
            "router_vni": l3_vni,
            "vlan_id": vlan_id,
            "gw_ip": "{}/{}".format(gw_ip, subnet_mask),
            # "external_fixed_ip": external_fixed_ip,
            # "external_fixed_mac": _router_info.get("fixed_mac"),
            "if_ext_gw": True
        }

        LOG.info("**********************************")
        LOG.info("**********************************")
        LOG.info(json.dumps(request_params, indent=3))
        LOG.info("**********************************")
        LOG.info("**********************************")
        _afc_api = afc_api.AfcRestClient()
        _afc_api.delete_or_update_vrf_on_physical_switch(request_params)

        # Remove router interface to VRouter
        self.afc_api = afc_api.AfcRestClient()
        config_params = {
            "switch_ip": switch_ip,
            "vni": 666,
            "vlan_id": vlan_id,
            "interfaces": ['X47'],
            "gw_ip": "{}/{}".format(external_fixed_ip, subnet_mask),
        }
        # # TODO config exception handing
        self.afc_api.delete_config_from_afc(config_params)

    def create_router(self, context, new_router):
        router_id = new_router.get("id")
        # Allocations one l3 vni to the VRouter
        self.l3_vni_manager.allocation_l3_vni(router_id)
        # Bring external information when creating VRouter
        l3_vni = utils.get_l3_vni_by_route_id(router_id)
        router_info = copy.deepcopy(new_router)
        router_info.update({
            "l3_vni": l3_vni
        })
        ext_gateway = new_router.get('external_gateway_info')
        if ext_gateway:
            # Add external gateway
            self._add_network_default_gateway(router_info)

    def update_router(self, context, router_id, original_router, new_router):
        # Handle access external_gateway_info
        l3_vni = utils.get_l3_vni_by_route_id(router_id)

        original_ext_gateway = original_router.get('external_gateway_info')
        new_ext_gateway = new_router.get('external_gateway_info')
        if original_ext_gateway and not new_ext_gateway:
            # Clean external gateway
            router_info = copy.deepcopy(original_router)
            router_info.update({
                "l3_vni": l3_vni
            })
            self._del_network_default_gateway(router_info)
            LOG.info("Clean external gateway, >>>>>>>>>>>>> %s", router_info)
        elif not original_ext_gateway and new_ext_gateway:
            # Add external gateway
            router_info = copy.deepcopy(new_router)
            router_info.update({
                "l3_vni": l3_vni
            })
            self._add_network_default_gateway(router_info)

    def delete_router(self, context, router_id, router):
        ext_gateway = router.get('external_gateway_info')
        if ext_gateway:
            # Clean external gateway
            l3_vni = utils.get_l3_vni_by_route_id(router_id)
            router_info = copy.deepcopy(router)
            router_info.update({
                "l3_vni": l3_vni
            })
            self._del_network_default_gateway(router_info)
            LOG.info("Clean external gateway, >>>>>>>>>>>>> %s", router_info)

        # Release l3 vni from VRouter
        self.l3_vni_manager.release_l3_vni(router_id)

    @staticmethod
    def add_router_interface(context, router_info):
        router_id = router_info.get("id")
        subnet_id = router_info.get("subnet_id")

        session, read_ctx_manager = utils.get_read_session()
        with read_ctx_manager:
            l2_vni_member_mappings = session.query(aster_models_v2.AsterPortBinding). \
                filter_by(subnet_id=subnet_id).\
                group_by(aster_models_v2.AsterPortBinding.switch_ip).all()

        for l2_vni_member_mapping in l2_vni_member_mappings:
            switch_ip = l2_vni_member_mapping.get("switch_ip")
            _router_info = copy.deepcopy(router_info)
            add_vrf_params = {
                "switch_ip": switch_ip,
                "vlan_id": l2_vni_member_mapping.get("vlan_id")
            }
            add_vrf_params.update(_router_info)
            LOG.info("Add the VRF configuration on the [%s], params >>> \n %s \n",
                     switch_ip, json.dumps(add_vrf_params, indent=3))

            # Add the VRF configuration on specified physical switch
            add_interface_to_router(add_vrf_params=add_vrf_params)
            # Record the configuration of the L3-VNI on the specified physical switch by switch_ip
            l3_vni = router_info.get("l3_vni")
            session, ctx_manager = utils.get_writer_session()
            with ctx_manager:
                session.query(aster_models_v2.AsterPortBinding). \
                    filter_by(switch_ip=switch_ip, subnet_id=subnet_id). \
                    update({"router_id": router_id, "l3_vni": l3_vni})
                session.flush()

    @staticmethod
    def remove_router_interface(context, router_info):
        subnet_id = router_info.get("subnet_id")

        session, read_ctx_manager = utils.get_read_session()
        with read_ctx_manager:
            l2_vni_member_mappings = session.query(aster_models_v2.AsterPortBinding). \
                filter_by(subnet_id=subnet_id).\
                group_by(aster_models_v2.AsterPortBinding.switch_ip).all()

        for l2_vni_member_mapping in l2_vni_member_mappings:
            switch_ip = l2_vni_member_mapping.get("switch_ip")
            _router_info = copy.deepcopy(router_info)
            del_vrf_params = {
                "switch_ip": switch_ip,
                "vlan_id": l2_vni_member_mapping.get("vlan_id")
            }
            del_vrf_params.update(_router_info)
            LOG.info("Remove the VRF configuration on the [%s], params >>> \n %s \n",
                     switch_ip, json.dumps(del_vrf_params, indent=3))

            # Clean the VRF configuration on this physical switch
            delete_interface_from_router(del_vrf_params=del_vrf_params)
            # Removes the record for l3-VNI on the specified physical switch by switch_ip
            session, ctx_manager = utils.get_writer_session()
            with ctx_manager:
                session.query(aster_models_v2.AsterPortBinding). \
                    filter_by(switch_ip=switch_ip, subnet_id=subnet_id). \
                    update({"router_id": "", "l3_vni": 0})
                session.flush()
