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

# cfg.CONF.ml2_cisco.nexus_switches

# nexus_switches = base.SubsectionOpt(
#     'ml2_mech_cisco_nexus',
#     dest='nexus_switches',
#     help=_("Subgroups that allow you to specify the nexus switches to be "
#            "managed by the nexus ML2 driver."),
#     subopts=nexus_sub_opts)
#
# print "333333333333333333333"
# print nexus_switches
# print "333333333333333333333"
#
# # cfg.CONF.register_opts(nexus_sub_opts, "ml2_mech_cisco_nexus:<ip_address>")
#
# cfg.CONF.register_opts(ml2_cisco_opts, "ml2_cisco")
# cfg.CONF.register_opt(nexus_switches, "ml2_cisco")
#
# cfg.DictOpt('jobtitles', default=None, help=('A dictionary of usernames titles'), dest=nexus_switches)
#
# cfg.CONF.register_opt(dest:)
#
# # cfg.CONF.ml2_cisco.nexus_switches
#
# t = SubsectionOpt("switch", dest="switches", subopts=[StrOpt('address'), StrOpt('password')])


#

# Format for ml2_conf_cisco.ini 'ml2_mech_cisco_nexus' is:
# {('<device ipaddr>', '<keyword>'): '<value>', ...}
#
# Example:
# {('1.1.1.1', 'username'): 'admin',
#  ('1.1.1.1', 'password'): 'mySecretPassword',
#  ('1.1.1.1', 'compute1'): '1/1', ...}
#


# import sys
#
# from oslo_config import cfg
#
# from neutron.plugins.ml2.drivers.mech_aster.mech_driver import config as conf
#
#
# cfg.CONF(sys.argv[1:])
#
# print cfg.CONF.aster_authtoken.auth_uri
# # print(cfg.CONF.API.host)
# # print(cfg.CONF.API.bind_port)
# # print(cfg.CONF.API.ssl)
#
#
# identities = {}
# sections = cfg.CONF.list_all_sections()
#
# for section in sections:
#     subsection, sep, ident = section.partition(':')
#
#     print subsection
#     print sep
#     print ident
#
#     if subsection.lower() != self.name.lower():
#         continue
#     cfg.CONF.register_opts(self.subopts, group=section)
#
#     sub_dict = {}
#     for key, value in cfg.CONF.get(section).items():
#         sub_dict.update({
#             key: value
#         })
#
#     identities[ident] = sub_dict
#     # identities[ident] = cfg.CONF.get(section)
# if getattr(cfg.CONF, 'get_location', None):
#     return (identities, None)
# else:
#     return identities

# {'nexus_switches': {'1.1.1.1': {'physnet': 'physnet1', 'host_ports_mapping': {'compute1': ['1/1'], 'compute2': ['1/2'], 'compute5': ['1/3', '1/4']}}, '2.2.2.2': {'physnet': 'physnet1', 'host_ports_mapping': {'compute1': ['1/1'], 'compute2': ['1/2'],'compute5': ['1/3', '1/4']}}}}

# cfg.CONF.ml2_cisco.nexus_switches


# neutron = client.Client(auth_strategy="noauth", endpoint_url="http://192.168.4.169:9696")
# return neutron
#
# # username = "admin"
# # password = "stack123"
# # project_name = "demo"
# # project_domain_id = "default"
# # user_domain_id = "default"
# # auth_url = "http://127.0.0.1/identity"
# # auth = identity.Password(auth_url=auth_url,
# #                          username=username,
# #                          password=password,
# #                          project_name=project_name,
# #                          project_domain_id=project_domain_id,
# #                          user_domain_id=user_domain_id)
# # sess = session.Session(auth=auth)
# # neutron = client.Client(session=sess)
