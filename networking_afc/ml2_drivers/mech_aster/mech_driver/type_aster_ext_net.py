from oslo_log import log
from oslo_config import cfg
from neutron_lib import exceptions as exc
from neutron_lib.plugins.ml2 import api

from neutron._i18n import _
from neutron.plugins.ml2.drivers import helpers

LOG = log.getLogger(__name__)

aster_ext_net_opts = [
    cfg.ListOpt('aster_ext_net_networks',
                default=[],
                help=_("List of physical_network names with which flat "
                       "networks can be created. Use default '' to allow "
                       "aster_ext_net networks with arbitrary physical_network names. "
                       "Use an empty list to disable aster_ext_net networks."))
]

cfg.CONF.register_opts(aster_ext_net_opts, "ml2_type_aster_ext_net")

TYPE_ASTER_EXT_NET = "aster_ext_net"


class AsterExtNetTypeDriver(helpers.BaseTypeDriver):
    """Manage state for aster_ext_net networks with ML2.

    The LocalTypeDriver implements the 'local' network_type. Local
    network segments provide connectivity between VMs and other
    devices running on the same node, provided that a common local
    network bridging technology is available to those devices. Local
    network segments do not provide any connectivity between nodes.
    """

    def __init__(self):
        super(AsterExtNetTypeDriver, self).__init__()
        # LOG.info("ML2 AsterExtNetTypeDriver initialization complete")
        self._parse_networks(cfg.CONF.ml2_type_aster_ext_net.aster_ext_net_networks)

    def _parse_networks(self, entries):
        self.aster_ext_net_networks = entries
        if not self.aster_ext_net_networks:
            LOG.info("aster_ext_net networks are disabled")
        else:
            LOG.info("Allowable aster_ext_net physical_network names: %s",
                     self.aster_ext_net_networks)

    def get_type(self):
        return TYPE_ASTER_EXT_NET

    def initialize(self):
        pass

    def is_partial_segment(self, segment):
        return False

    def validate_provider_segment(self, segment):
        # {
        #     'segmentation_id': None,
        #     'physical_network': u'fw1',
        #     'network_type': u'aster_ext_net'
        # }
        physical_network = segment.get(api.PHYSICAL_NETWORK)
        if not physical_network:
            msg = _("physical_network required for aster_ext_net provider network")
            raise exc.InvalidInput(error_message=msg)
        if self.aster_ext_net_networks is not None and not self.aster_ext_net_networks:
            msg = _("aster_ext_net provider networks are disabled")
            raise exc.InvalidInput(error_message=msg)
        if self.aster_ext_net_networks and physical_network not in self.aster_ext_net_networks:
            msg = (_("physical_network '%s' unknown for aster_ext_net provider network")
                   % physical_network)
            raise exc.InvalidInput(error_message=msg)

        for key, value in segment.items():
            if value and key not in [api.NETWORK_TYPE,
                                     api.PHYSICAL_NETWORK]:
                msg = _("%s prohibited for aster_ext_net provider network") % key
                raise exc.InvalidInput(error_message=msg)

    def reserve_provider_segment(self, context, segment):
        # No resources to reserve
        physical_network = segment[api.PHYSICAL_NETWORK]
        LOG.info("Reserving aster_ext_net network on physical network %s", physical_network)
        return segment

    def allocate_tenant_segment(self, context):
        # Tenant aster_ext_net networks are not supported.
        return

    def release_segment(self, context, segment):
        # No resources to release
        physical_network = segment[api.PHYSICAL_NETWORK]
        LOG.info("Releasing aster_ext_net network on physical network %s", physical_network)

    def get_mtu(self, physical_network=None):
        pass
