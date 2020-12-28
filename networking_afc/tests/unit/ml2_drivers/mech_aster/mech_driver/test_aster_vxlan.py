# Copyright (c) 2013 OpenStack Foundation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import mock

from oslo_config import cfg
from neutron_lib import context
from neutron_lib.plugins.ml2 import api
from neutron.tests.unit import testlib_api
from neutron_lib import exceptions as exc
from neutron_lib.db import api as lib_db_api
from networking_afc.ml2_drivers.mech_aster.mech_driver import type_aster_vxlan


TUN_MIN = 100
TUN_MAX = 109
UPDATED_TUNNEL_RANGES = [(TUN_MIN + 5, TUN_MAX + 5)]


class AsterVxlanBaseTest(testlib_api.SqlTestCase):
    DRIVER_MODULE = type_aster_vxlan
    DRIVER_CLASS = type_aster_vxlan.AsterCXVxlanTypeDriver
    TYPE = type_aster_vxlan.TYPE_ASTER_VXLAN
    TUN_MIN0 = 100
    TUN_MAX0 = 101
    TUN_MIN1 = 200
    TUN_MAX1 = 201

    def setUp(self):
        super(AsterVxlanBaseTest, self).setUp()
        cfg.CONF.set_override("vni_ranges", "", group="ml2_type_aster_vxlan")
        self.driver = self.DRIVER_CLASS()
        self.context = context.get_admin_context()


class AsterVxlancheckTest(AsterVxlanBaseTest):
    TUNNEL_RANGES = [(TUN_MIN, TUN_MAX)]

    def setUp(self):
        super(AsterVxlancheckTest, self).setUp()
        self.driver.tunnel_ranges = self.TUNNEL_RANGES

    def _test__parse_aster_vni_range(self, _range):
        self.assertRaises(
            exc.NetworkTunnelRangeError,
            self.driver._parse_aster_vni_range,
            _range)

    def test__parse_aster_vni_range_out_of_range(self):
        tunnel_range = [2, 2000]
        self._test__parse_aster_vni_range(tunnel_range)

    def test__parse_aster_vni_range_order_error(self):
        tunnel_range = [100, 50]
        self._test__parse_aster_vni_range(tunnel_range)

    def _test__parse_afc_vni_ranges(self):
        tunnel_ranges = cfg.CONF.ml2_type_aster_vxlan.vni_ranges
        current_range = self.driver.tunnel_ranges
        self.driver._parse_afc_vni_ranges(tunnel_ranges, current_range)

    def test__parse_afc_vni_ranges_with_invaild_conf_type(self):
        cfg.CONF.set_override("vni_ranges", "1:", group="ml2_type_aster_vxlan")
        self.assertRaises(
            exc.NetworkTunnelRangeError,
            self._test__parse_afc_vni_ranges)

    def test__parse_afc_vni_ranges_with_invaild_conf_value_1(self):
        cfg.CONF.set_override("vni_ranges", "10:1000",
                              group="ml2_type_aster_vxlan")
        self.assertRaises(
            exc.NetworkTunnelRangeError,
            self._test__parse_afc_vni_ranges)

    def test__parse_afc_vni_ranges_with_invaild_conf_value_2(self):
        cfg.CONF.set_override("vni_ranges", "15000:5000",
                              group="ml2_type_aster_vxlan")
        self.assertRaises(
            exc.NetworkTunnelRangeError,
            self._test__parse_afc_vni_ranges)

    def test__verify_vni_ranges_with_normal_status(self):
        old_range = len(self.driver.tunnel_ranges)
        cfg.CONF.set_override("vni_ranges", "5000:10000",
                              group="ml2_type_aster_vxlan")
        self._test__parse_afc_vni_ranges()
        self.assertNotEqual(old_range, len(self.driver.tunnel_ranges))


class AsterVxlanTypeDBTest(AsterVxlanBaseTest):

    def setUp(self):
        super(AsterVxlanTypeDBTest, self).setUp()
        self.session = lib_db_api.get_writer_session()

    def test_allocate_tenant_segment(self):
        a = mock.Mock(vxlan_vni="test")
        # a.vxlan_vni = mock.Mock(return_value="test")
        mock_a = mock.Mock(return_value=None)
        self.driver.allocate_partially_specified_segment = mock_a
        result = self.driver.allocate_tenant_segment(self.context)
        self.assertIsNone(result)
        self.assertTrue(mock_a.called)
        self.driver.allocate_partially_specified_segment = mock.Mock(
            return_value=a)
        result = self.driver.allocate_tenant_segment(self.context)
        # self.assertTrue(a.called)
        self.assertEqual(result[api.SEGMENTATION_ID], "test")

    def _test_reserve_provider_segment(self, context, segment):
        return self.driver.reserve_provider_segment(context, segment)

    def test_reserve_provider_segment_with_spec_seg(self):
        specs = {api.NETWORK_TYPE: self.TYPE,
                 api.PHYSICAL_NETWORK: 'None',
                 api.SEGMENTATION_ID: None}
        a = mock.Mock(return_value=None)
        self.driver.allocate_partially_specified_segment = a
        # self.assertTrue(a.called)
        self.assertRaises(exc.NoNetworkAvailable,
                          self._test_reserve_provider_segment,
                          self.context, specs)
        mock_a = mock.Mock(vxlan_vni="test")
        mock_b = mock.Mock(return_value=mock_a)
        self.driver.allocate_partially_specified_segment = mock_b
        result = self._test_reserve_provider_segment(self.context, specs)
        self.assertTrue(mock_b.called)
        self.assertEqual(result[api.SEGMENTATION_ID], "test")

    def test_reserve_provider_segment_with_normal_seg(self):
        normals = {
            api.NETWORK_TYPE: self.TYPE,
            api.PHYSICAL_NETWORK: 'None',
            api.SEGMENTATION_ID: "test_id"}
        a = mock.Mock(return_value=None)
        self.driver.allocate_fully_specified_segment = a
        # self.assertTrue(a.called)
        self.assertRaises(
            exc.TunnelIdInUse,
            self._test_reserve_provider_segment,
            self.context, normals)
        mock_a = mock.Mock(vxlan_vni="test")
        mock_b = mock.Mock(return_value=mock_a)
        self.driver.allocate_fully_specified_segment = mock_b
        result = self._test_reserve_provider_segment(self.context, normals)
        self.assertTrue(mock_b.called)
        self.assertEqual(result[api.SEGMENTATION_ID], "test")

    def _test_sync_allocations_and_allocated(self, tunnel_id):
        segment = {api.NETWORK_TYPE: self.TYPE,
                   api.PHYSICAL_NETWORK: None,
                   api.SEGMENTATION_ID: tunnel_id}
        self.driver.reserve_provider_segment(self.context, segment)

        self.driver.tunnel_ranges = UPDATED_TUNNEL_RANGES
        self.driver.sync_allocations()

        self.assertTrue(
            self.driver.get_allocation(self.context, tunnel_id).allocated)

    def test_sync_allocations_and_allocated_in_initial_range(self):
        self._test_sync_allocations_and_allocated(TUN_MIN + 2)

    def test_sync_allocations_and_allocated_in_final_range(self):
        self._test_sync_allocations_and_allocated(TUN_MAX + 2)
