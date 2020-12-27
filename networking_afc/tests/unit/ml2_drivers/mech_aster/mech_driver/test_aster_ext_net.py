
from oslo_config import cfg
from neutron.tests.unit import testlib_api
from neutron_lib import exceptions as exc
from networking_afc.ml2_drivers.mech_aster.mech_driver import (
    type_aster_ext_net)


class AsterExtNetTypeBaseTest(testlib_api.SqlTestCase):
    DRIVER_MODULE = type_aster_ext_net
    DRIVER_CLASS = type_aster_ext_net.AsterExtNetTypeDriver
    TYPE = type_aster_ext_net.TYPE_ASTER_EXT_NET

    def setUp(self):
        super(AsterExtNetTypeBaseTest, self).setUp()

    def test_validate_provider_segment_with_none_phy_net(self):
        driver = self.DRIVER_CLASS()
        segement = {
            'segmentation_id': None,
            'physical_network': None,
            'network_type': u'aster_ext_net'
        }
        self.assertRaises(
            exc.InvalidInput,
            driver.validate_provider_segment,
            segement)

    def test_validate_provider_segment_with_zero_ext_net(self):
        cfg.CONF.set_override("aster_ext_net_networks", "",
                              group="ml2_type_aster_ext_net")
        driver = self.DRIVER_CLASS()
        segement = {
            'segmentation_id': None,
            'physical_network': 'fw1',
            'network_type': u'aster_ext_net'
        }
        self.assertRaises(
            exc.InvalidInput,
            driver.validate_provider_segment,
            segement)

    def test_validate_provider_segment_with_invaild_ext_net(self):
        cfg.CONF.set_override("aster_ext_net_networks", "aaa",
                              group="ml2_type_aster_ext_net")
        driver = self.DRIVER_CLASS()
        segement = {
            'segmentation_id': None,
            'physical_network': 'fw1',
            'network_type': u'aster_ext_net'
        }
        self.assertRaises(
            exc.InvalidInput,
            driver.validate_provider_segment,
            segement)

    def test_validate_provider_segment_with_seg_id(self):
        cfg.CONF.set_override("aster_ext_net_networks", "fw1",
                              group="ml2_type_aster_ext_net")
        driver = self.DRIVER_CLASS()
        segement = {
            'segmentation_id': "test",
            'physical_network': 'fw1',
            'network_type': u'aster_ext_net'
        }
        self.assertRaises(
            exc.InvalidInput,
            driver.validate_provider_segment,
            segement)

    def test_validate_provider_segment_with_vaild_seg(self):
        cfg.CONF.set_override("aster_ext_net_networks", "fw1",
                              group="ml2_type_aster_ext_net")
        driver = self.DRIVER_CLASS()
        segement = {
            'segmentation_id': None,
            'physical_network': 'fw1',
            'network_type': u'aster_ext_net'
        }
        result = driver.validate_provider_segment(segement)
        self.assertIsNone(result)
