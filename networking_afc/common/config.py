from oslo_config import cfg
from oslo_config import types

from neutron._i18n import _


aster_afc_opts = [
    cfg.StrOpt('username',
               help=_('The username of the Aster AFC Administrator is required '
                      'to allow configuration access to the Aster AFC.')),
    cfg.StrOpt('password', secret=True,
               help=_('The password of the Aster AFC Administrator is required '
                      'to allow configuration access to the Aster AFC.')),
    cfg.StrOpt('auth_uri',
               help=_('The endpoint of access to the Aster AFC.')),

    cfg.BoolOpt('is_send_afc',
                default=False,
                help=_('True to delete all ports on all the OpenvSwitch '
                       'bridges. False to delete ports created by '
                       'Neutron on integration and external network '
                       'bridges.'))
]

cfg.CONF.register_opts(aster_afc_opts, "aster_authtoken")


cx_sub_opts = [
    cfg.StrOpt('physnet',
               help=_('This is required if Nexus VXLAN overlay feature is '
                      'configured.  It should be the physical network name defined '
                      'in "network_vlan_ranges" (defined beneath the "ml2_type_vlan" '
                      'section) that this switch is controlling.  The configured '
                      '"physnet" is the physical network domain that is connected '
                      'to this switch. The vlan ranges defined in '
                      '"network_vlan_ranges" for a physical '
                      'network are allocated dynamically and are unique per physical '
                      'network. These dynamic vlans may be reused across physical '
                      'networks.  This configuration applies to non-baremetal '
                      'only.')),
    cfg.Opt('host_ports_mapping', default={}, sample_default='<None>',
            type=types.Dict(value_type=types.List(bounds=True)),
            help=_('A list of key:value pairs describing which host is '
                   'connected to which physical port or portchannel on the '
                   'Nexus switch. The format should look like:\n'
                   'host_ports_mapping='
                   '<your-hostname>:[<intf_type><port>,<intf_type><port>],\n'
                   '                  <your-second-host>:[<intf_type><port>]\n'
                   'For example:\n'
                   'host_ports_mapping='
                   'host-1:[ethernet1/1, ethernet1/2],\n'
                   '                  host-2:[ethernet1/3],\n'
                   '                  host-3:[port-channel20]\n'
                   'Lines can be broken with indentation to ensure config files '
                   'remain readable. '
                   'All compute nodes must be configured while '
                   'controllers are optional depending on your network '
                   'configuration. Depending on the configuration of the '
                   'host, the hostname is expected to be the '
                   'full hostname (hostname.domainname) which can be derived '
                   'by running "hostname -f" on the host itself. Valid '
                   'intf_types are "ethernet" or "port-channel".  The default '
                   'setting for <intf_type> is "ethernet" and need not be '
                   'added to this setting. This configuration applies to VM '
                   'deployments only.'))
]


def get_sub_section_dict(name="", sub_opts=None):
    sections = cfg.CONF.list_all_sections()
    identities = {}
    for section in sections:
        subsection, sep, ident = section.partition(':')
        if subsection.lower() != name.lower():
            continue
        cfg.CONF.register_opts(sub_opts, group=section)
        sub_dict = {}
        for key, value in cfg.CONF.get(section).items():
            sub_dict.update({
                key: value
            })
        identities[ident] = sub_dict
    return identities


_identities = get_sub_section_dict(name="ml2_mech_aster_cx", sub_opts=cx_sub_opts)
opt_ml2_mech_aster_cx_dict = cfg.DictOpt(
    'ml2_mech_aster_cx',
    default=_identities,
    help=_('A dictionary of ml2_mech_aster_cx titles'),
    dest="cx_switches"
)
cfg.CONF.register_opt(opt_ml2_mech_aster_cx_dict, "ml2_aster")


border_leaf_sub_opts = [
    cfg.ListOpt('vlan_ranges',
                default=[],
                help=_("Comma-separated list of <vni_min>:<vni_max> tuples "
                       "enumerating ranges of VXLAN Network IDs that are "
                       "available for tenant network allocation. "
                       "Example format: vlan_ranges = 100:1000,2000:6000")),
    cfg.Opt('physical_network_ports_mapping', default={}, sample_default='<None>',
            type=types.Dict(value_type=types.List(bounds=True)),
            help=_('A list of key:value pairs'))
]

_identities = get_sub_section_dict(name="ml2_border_leaf", sub_opts=border_leaf_sub_opts)
opt_ml2_border_dict = cfg.DictOpt(
    'ml2_border_leaf',
    default=_identities,
    help=_('A dictionary of ml2_border_leaf titles'),
    dest="border_switches"
)
cfg.CONF.register_opt(opt_ml2_border_dict, "ml2_aster")
