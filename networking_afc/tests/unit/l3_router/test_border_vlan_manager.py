
from oslo_config import cfg
from neutron._i18n import _

from neutron.tests.unit import testlib_api
from networking_afc.l3_router import border_vlan_manager

# ml2_aster_test_opts = [
#     cfg.DictOpt('border_switches',
#                help=_('test_border_manager_init'))
# ]

# cfg.CONF.register_opts(ml2_aster_test_opts, "ml2_aster")


class AfcBorderVlanManagerTestCase(testlib_api.SqlTestCase):

    def setUp(self):
        super(AfcBorderVlanManagerTestCase, self).setUp()
        if not hasattr(cfg.CONF, "ml2_aster"):
            ml2_aster_test_opts = [
                cfg.StrOpt('border_switches',
                           help=_('test_border_manager_init'))
            ]
            cfg.CONF.register_opts(ml2_aster_test_opts, "ml2_aster")

    def test_init(self):
        cfg.CONF.set_override("border_switches",
                              {"fake_switch_1": {
                                  "vlan_ranges": ['2000:2002']}},
                              group="ml2_aster")
        border_vlan_manager.BorderVlanManager()
