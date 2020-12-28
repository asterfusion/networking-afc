import json
import copy
from sqlalchemy import and_
from oslo_log import log as logging
from neutron_lib import constants as n_const
from neutron_lib import context as neutron_context
from neutron_lib.plugins import constants as plugin_constants
from neutron_lib.plugins import directory
from neutron_lib.db import api as lib_db_api
from neutron_lib.plugins.ml2 import api
from neutron.plugins.ml2.driver_context import NetworkContext  # noqa
from neutron.db import models_v2
from neutron.db.models import segment as segment_models

from networking_afc.db.models import aster_models_v2


LOG = logging.getLogger(__name__)


def get_writer_session():
    session = lib_db_api.get_writer_session()
    return session, session.begin(subtransactions=True)


def get_read_session():
    session = lib_db_api.get_reader_session()
    return session, session.begin(subtransactions=True)


def get_l3_vni_by_route_id(router_id):
    session, ctx_manager = get_read_session()
    with ctx_manager:
        alloc = session.query(aster_models_v2.AsterL3VNIAllocation). \
            filter_by(router_id=router_id).first()
        return alloc.l3_vni if alloc else -1


def get_l2_vni_by_route_id(router_id):
    session, ctx_manager = get_read_session()
    with ctx_manager:
        alloc = session.query(aster_models_v2.AsterL2VNIAllocation). \
            filter_by(router_id=router_id).first()
        return alloc.l2_vni if alloc else -1


def get_routers_and_interfaces(self):
    core = directory.get_plugin()
    ctx = neutron_context.get_admin_context()
    routers = directory.get_plugin(plugin_constants.L3).get_routers(ctx)

    for router in routers:
        router_id = router.get("id")
        l3_vni = get_l3_vni_by_route_id(router_id)
        router.update({
            "l3_vni": l3_vni
        })

    router_interfaces = list()
    for router in routers:
        ports = core.get_ports(
            ctx,
            filters={
                'device_id': [router['id']],
                'device_owner': [n_const.DEVICE_OWNER_ROUTER_INTF,
                                 n_const.DEVICE_OWNER_ROUTER_GW]}) or []
        for p in ports:
            router_interface = router.copy()
            net_id = p['network_id']
            subnet_id = p['fixed_ips'][0]['subnet_id']
            subnet = core.get_subnet(ctx, subnet_id)
            ml2_db = NetworkContext(self, ctx, {'id': net_id})
            seg_id = ml2_db.network_segments[0]['segmentation_id']

            router_interface['seg_id'] = seg_id
            router_interface['cidr'] = subnet['cidr']
            router_interface['gip'] = subnet['gateway_ip']
            if p.get('id') == router.get('gw_port_id'):
                router['gip'] = subnet['gateway_ip']
            router_interface['fixed_ip'] = p['fixed_ips'][0]['ip_address']
            router_interface['ip_version'] = subnet['ip_version']
            router_interface['subnet_id'] = subnet_id
            router_interfaces.append(router_interface)
    return routers, router_interfaces


def get_router_interface_by_subnet_id(self, subnet_id=None):
    _, router_interfaces = get_routers_and_interfaces(self)
    for router_interface in router_interfaces:
        if router_interface.get("subnet_id") == subnet_id:
            return router_interface


def get_ports_by_subnet(**kwargs):
    """
    DVR: RPC called by dvr-agent to get all ports for subnet.
     Add filter of dhcp port when create
    """
    subnet_id = kwargs.get('subnet_id')
    host_ids = kwargs.get('host_ids', [])

    core = directory.get_plugin()
    admin_ctx = neutron_context.get_admin_context()
    filters = {
        'binding:host_id': host_ids,
        'fixed_ips': {'subnet_id': [subnet_id]}
    }

    LOG.info("Get all ports for subnet, filters params is: \n %s \n",
             json.dumps(filters, indent=3))

    ports = core.get_ports(admin_ctx, filters=filters)
    host_port_mappings = {}

    # 'device_owner': 'network:dhcp'

    for port in copy.deepcopy(ports):
        if port.get("device_owner") == 'network:dhcp':
            ports.remove(port)
        host_id = port.get("binding:host_id")
        port_id = port.get("id")
        if host_id in host_port_mappings.keys():
            host_port_mappings[host_id].append(port_id)
        else:
            host_port_mappings.update({
                host_id: [port_id]
            })

    port_bound_contexts = {}
    for host, port_ids in host_port_mappings.items():
        _bound_contexts = core.get_bound_ports_contexts(
            admin_ctx, port_ids, host
        )
        LOG.info("Get host [%s], _bound_contexts is: \n %s \n",
                 host, _bound_contexts)
        port_bound_contexts.update(_bound_contexts)

    for port in ports:
        port_id = port.get("id")
        port_bound_context = port_bound_contexts.get(port_id)
        if port_bound_context:
            segment = port_bound_context.bottom_bound_segment
            port.update({
                'network_type': segment[api.NETWORK_TYPE],
                'segmentation_id': segment[api.SEGMENTATION_ID],
                'physical_network': segment[api.PHYSICAL_NETWORK]
            })
    return ports


def get_subnets_by_network_id(network_id=None):
    # Get subnet by network_id
    core = directory.get_plugin()
    admin_ctx = neutron_context.get_admin_context()
    return core.get_subnets_by_network(admin_ctx, network_id)


def get_l2_gw_ip_by_subnet_id(subnet_id=None):
    core = directory.get_plugin()
    admin_ctx = neutron_context.get_admin_context()
    subnet_fields = ["cidr", "gateway_ip"]
    subnet = core.get_subnet(admin_ctx, subnet_id, fields=subnet_fields)
    cidr = subnet.get("cidr")
    subnet_mask = cidr.split('/')[1]
    gip = subnet.get("gateway_ip")
    return "{}/{}".format(gip, subnet_mask)


def get_subnet_detail_by_network_id(network_id=None):
    session = lib_db_api.get_reader_session()
    with session.begin():
        subnet_model = models_v2.Subnet
        db_result = (
            session.query(
                subnet_model.id.label('subnet_id'),
                subnet_model.gateway_ip.label('gip'),
                subnet_model.cidr,
                subnet_model.ip_version
            ).filter_by(network_id=network_id).first()
        )
        if not db_result:
            LOG.info("Get subnet detail is: %s", db_result)
            return db_result
        result = {
            k: db_result[index]
            for index, k in enumerate(
                ('subnet_id', 'gip', 'cidr', 'ip_version'))
        }
        if result:
            cidr = result.get("cidr")
            subnet_mask = cidr.split('/')[1]
            gip = result.get("gip")
            result["gw_and_mask"] = "{}/{}".format(gip, subnet_mask)
    return result


def get_network_gateway_ipv4(port_id):
    """Returns all the routers and IPv4 gateway that have network as gateway"""
    session = lib_db_api.get_reader_session()
    with session.begin():
        subnet_model = models_v2.Subnet
        port_model = models_v2.Port
        ip_allocation_model = models_v2.IPAllocation
        result = (
            session.query(
                port_model.device_id,
                subnet_model.network_id,
                subnet_model.id.label('subnet_id'),
                subnet_model.gateway_ip.label('gip'),
                subnet_model.cidr,
                subnet_model.ip_version,
                ip_allocation_model.ip_address,
                port_model.mac_address
            ).filter(
                and_(
                    port_model.network_id == subnet_model.network_id,
                    port_model.id == ip_allocation_model.port_id,
                    port_model.id == port_id)
            ).first()
        )
    return _format_gateway_result(result)


def _format_gateway_result(db_result):
    """This function formats result as needed by add_router_interface"""
    if not db_result:
        return None
    result = {
        k: db_result[i]
        for i, k in enumerate(
            ('device_id', 'network_id', 'subnet_id', 'gip',
             'cidr', 'ip_version', 'fixed_ip', 'fixed_mac'))}
    return result


def get_vlan_id_by_route_id(switch_ip=None, router_id=None):
    session, ctx_manager = get_read_session()
    with ctx_manager:
        alloc = session.query(aster_models_v2.AsterLeafVlanAllocation). \
            filter_by(switch_ip=switch_ip, router_id=router_id).first()
        return alloc.vlan_id if alloc else -1


def get_network_segments(network_id=None):
    reader_session = lib_db_api.get_reader_session()
    with reader_session.begin():
        model = segment_models.NetworkSegment
        segments = reader_session.query(model).\
            filter(model.network_id == network_id).first()
    return segments
