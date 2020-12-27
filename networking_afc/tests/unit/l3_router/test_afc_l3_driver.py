import mock

from oslo_config import cfg
from neutron._i18n import _
from neutron.tests.unit import testlib_api
from neutron_lib.db import api as db_api
from networking_afc.db.models import aster_models_v2
from networking_afc.l3_router import afc_l3_driver


class AfcL3DriverTestCase(testlib_api.SqlTestCase):

    def setUp(self):
        super(AfcL3DriverTestCase, self).setUp()
        self.driver = afc_l3_driver.AFCL3Driver()
        if not cfg.CONF.ml2_aster:
            ml2_aster_test_opts = [
                cfg.StrOpt('border_switches',
                           help=_('test_border_manager_init'))
            ]
            cfg.CONF.register_opts(ml2_aster_test_opts, "ml2_aster")

    def _create_record(self, l2_vni):
        session = db_api.get_writer_session()
        with session.begin():
            binding = aster_models_v2.AsterL2VNIAllocation(
                l2_vni=l2_vni.get("l2_vni"),
                router_id=l2_vni.get("router_id")
            )
            session.add(binding)
            session.flush()

    def _get_record(self, router_id=None):
        session = db_api.get_reader_session()
        with session.begin():
            result = session.query(
                aster_models_v2.AsterL2VNIAllocation
                ).filter_by(router_id=router_id).all()
        return result

    def test__add_network_default_gateway(self):
        test_record = {
            "l2_vni": 222,
            "router_id": ""
        }
        self._create_record(test_record)
        fake_route = {
            "id": "fake_id",
            "gw_port_id": "fake_port_id",
            "l3_vni": "test_l3_vni",
            "tenant_id": "fake_tenant",
            "external_gateway_info": {
                "network_id": "fake_net"
            }
        }
        fake_gw_port = {
            "fixed_ip": "test_ip",
            "gip": "test_gip",
            "cidr": 'xxx/24',
            "ip_version": "4",
            "network_id": "test_id",
            "seg_id": None
        }
        fake_border_config = {
            "fake_switch_1": {
                "vlan_ranges": ['2000:2002'],
                "physical_network_ports_mapping": {"test_phy_net": ["X29"]}
            }
        }
        cfg.CONF.set_override("border_switches",
                              fake_border_config,
                              group="ml2_aster")
        fake_net_ctx = mock.Mock()
        fake_net_ctx.network_segments = [{'segmentation_id': "test_seg_id"}]
        fake_seg = mock.Mock()
        fake_seg.network_type = "aster_ext_net"
        fake_seg.physical_network = "test_phy_net"
        mock_api = mock.Mock()
        self.driver.afc_api.send_config_to_afc = mock_api
        mock_alloc_seg = mock.Mock()
        self.driver.border_vlan_manager.allocate_segment = mock_alloc_seg
        get_gw_path = ("networking_afc.common.utils."
                       "get_network_gateway_ipv4")
        Net_ctx_path = ("networking_afc.l3_router."
                        "afc_l3_driver.NetworkContext")
        get_net_seg_path = ("networking_afc.common.utils."
                            "get_network_segments")
        get_vlan_path = ("networking_afc.common.utils."
                         "get_vlan_id_by_route_id")
        with mock.patch(get_gw_path,
                        return_value=fake_gw_port) as mock_sub:
            with mock.patch(Net_ctx_path,
                            return_value=fake_net_ctx) as mock_network_ctx:
                with mock.patch(get_net_seg_path,
                                return_value=fake_seg) as mock_sub_2:
                    with mock.patch(get_vlan_path,
                                    return_value="test_vlan_id") as mock_sub_3:
                        self.driver._add_network_default_gateway(fake_route)
        l2_vni_record = self._get_record(router_id="fake_id")
        self.assertTrue(l2_vni_record)
        self.assertTrue(mock_sub.called)
        self.assertTrue(mock_network_ctx.called)
        self.assertTrue(mock_sub_2.called)
        self.assertTrue(mock_sub_3.called)
        self.assertTrue(mock_api.called)
        self.assertTrue(mock_alloc_seg.called)
