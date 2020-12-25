
import copy

from neutron_lib.plugins import constants as plugin_constants
from neutron_lib.plugins import directory
from neutron_lib.services import base as service_base
from oslo_log import helpers as log_helpers
from oslo_log import log as logging
from oslo_utils import excutils

from networking_afc._i18n import _LE
from neutron.db import extraroute_db
from neutron.db import l3_agentschedulers_db
from neutron.db import l3_gwmode_db
from neutron.plugins.ml2.driver_context import NetworkContext  # noqa

from networking_afc.l3_router import afc_l3_driver
from networking_afc.common import utils


LOG = logging.getLogger(__name__)


class AsterL3ServicePlugin(service_base.ServicePluginBase,
                           extraroute_db.ExtraRoute_db_mixin,
                           l3_gwmode_db.L3_NAT_db_mixin,
                           l3_agentschedulers_db.L3AgentSchedulerDbMixin):
    """Implements L3 Router service plugin for Aster hardware.

    Creates routers in Aster hardware, manages them, adds/deletes interfaces
    to the routes.
    """

    supported_extension_aliases = ["router", "ext-gw-mode",
                                   "extraroute"]

    def __init__(self):
        super(AsterL3ServicePlugin, self).__init__()
        self.driver = afc_l3_driver.AFCL3Driver()

    def get_plugin_type(self):
        return plugin_constants.L3

    def get_plugin_description(self):
        """Returns string description of the plugin."""
        return ("Aster L3 Router Service Plugin for Aster Hardware "
                "based routing")

    @log_helpers.log_method_call
    def create_router(self, context, router):
        """Create a new router entry in DB, and create it Aster HW."""

        # Add router to the DB
        new_router = super(AsterL3ServicePlugin, self).create_router(
            context,
            router)
        # Create router on the Aster HW
        try:
            self.driver.create_router(context, new_router)
            return new_router
        except Exception as exc:
            with excutils.save_and_reraise_exception():
                LOG.error(_LE("Error creating router on Aster HW router=%s "
                              "exception=%s"),
                          new_router, exc)
                super(AsterL3ServicePlugin, self).delete_router(
                    context,
                    new_router['id']
                )

    @log_helpers.log_method_call
    def update_router(self, context, router_id, router):
        """Update an existing router in DB, and update it in Aster HW."""

        # Read existing router record from DB
        original_router = self.get_router(context, router_id)
        # Update router DB
        new_router = super(AsterL3ServicePlugin, self).update_router(
            context, router_id, router)

        # Modify router on the Aster HW
        try:
            self.driver.update_router(context, router_id,
                                      original_router, new_router)
            return new_router
        except Exception as exc:
            LOG.error(_LE("Error updating router on Aster HW router=%s "
                          "exception=%s"),
                      new_router, exc)

    @log_helpers.log_method_call
    def delete_router(self, context, router_id):
        """Delete an existing router from Aster HW as well as from the DB."""

        router = self.get_router(context, router_id)

        # Delete router on the Aster HW
        try:
            self.driver.delete_router(context, router_id, router)
        except Exception as exc:
            LOG.error(_LE("Error deleting router on Aster HW "
                          "router %(r)s exception=%(exc)s"),
                      {'r': router, 'exc': exc})

        super(AsterL3ServicePlugin, self).delete_router(context, router_id)

    @log_helpers.log_method_call
    def add_router_interface(self, context, router_id, interface_info):
        """Add a subnet of a network to an existing router."""

        new_router = super(AsterL3ServicePlugin, self).add_router_interface(
            context, router_id, interface_info)

        core = directory.get_plugin()

        # Get network info for the subnet that is being added to the router.
        # Check if the interface information is by port-id or subnet-id
        add_by_port, add_by_sub = self._validate_interface_info(
            interface_info)
        if add_by_sub:
            subnet = core.get_subnet(context, interface_info['subnet_id'])
            # If we add by subnet and we have no port allocated, assigned
            # gateway IP for the interface
            fixed_ip = subnet['gateway_ip']
        elif add_by_port:
            port = core.get_port(context, interface_info['port_id'])
            subnet_id = port['fixed_ips'][0]['subnet_id']
            fixed_ip = port['fixed_ips'][0]['ip_address']
            subnet = core.get_subnet(context, subnet_id)
        network_id = subnet['network_id']

        # To create VNet in Aster HW, the segmentation Id
        # is required for this network.
        ml2_db = NetworkContext(self, context, {'id': network_id})
        seg_id = ml2_db.network_segments[0]['segmentation_id']

        # Package all the info needed for HW programming
        router = self.get_router(context, router_id)
        router_info = copy.deepcopy(new_router)
        router_info['seg_id'] = seg_id
        router_info['name'] = router['name']
        router_info['cidr'] = subnet['cidr']
        router_info['gip'] = subnet['gateway_ip']
        router_info['fixed_ip'] = fixed_ip
        router_info['ip_version'] = subnet['ip_version']
        # self.get_sync_data()
        router_info['l3_vni'] = utils.get_l3_vni_by_route_id(router_id)

        try:
            self.driver.add_router_interface(context, router_info)
            return new_router
        except Exception as exc:
            with excutils.save_and_reraise_exception():
                LOG.error(_LE("Error Adding subnet %(subnet)s to "
                              "router %(router_id)s on Aster HW, "
                              "Exception =%(exc)s"), {
                                  'subnet': subnet,
                                  'router_id': router_id,
                                  'exc': exc})
                super(AsterL3ServicePlugin, self).remove_router_interface(
                    context, router_id, interface_info
                )

    @log_helpers.log_method_call
    def remove_router_interface(self, context, router_id, interface_info):
        """Remove a subnet of a network from an existing router."""

        router_to_del = (
            super(AsterL3ServicePlugin, self).remove_router_interface(
                context,
                router_id,
                interface_info)
        )

        # Get network information of the subnet that is being removed
        core = directory.get_plugin()
        subnet = core.get_subnet(context, router_to_del['subnet_id'])
        network_id = subnet['network_id']

        # For VNet removal from Aster HW, segmentation ID is needed
        ml2_db = NetworkContext(self, context, {'id': network_id})
        seg_id = ml2_db.network_segments[0]['segmentation_id']

        router = self.get_router(context, router_id)
        router_info = copy.deepcopy(router_to_del)
        router_info['seg_id'] = seg_id
        router_info['name'] = router['name']
        router_info['cidr'] = subnet['cidr']
        router_info['gip'] = subnet['gateway_ip']
        router_info['ip_version'] = subnet['ip_version']
        router_info['l3_vni'] = utils.get_l3_vni_by_route_id(router_id)

        try:
            self.driver.remove_router_interface(context, router_info)
            return router_to_del
        except Exception as exc:
            LOG.error(_LE("Error removing interface %(interface)s from "
                          "router %(router_id)s on Aster HW"
                          "Exception =%(exc)s"),
                      {'interface': interface_info, 'router_id': router_id,
                       'exc': exc})
