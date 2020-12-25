# Copyright (c) 2015 IBM Corp.
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
import copy
import mock

from oslo_config import cfg
from neutron_lib.plugins.ml2 import api
from neutron_lib.db import api as db_api
from neutron.tests.unit import testlib_api
from neutron_lib.api.definitions import portbindings
from networking_afc.db.models import aster_models_v2

from networking_afc.ml2_drivers.mech_aster.mech_driver import mech_aster
from networking_afc.ml2_drivers.mech_aster.mech_driver import (
    exceptions as exc)
from networking_afc.tests.unit.ml2_drivers.mech_aster.mech_driver import (
    _test_mech_base as me_base)

SEGMENTS = [{
            api.ID: 'aster_vxlan_segment_id',
            api.NETWORK_TYPE: 'aster_vxlan',
            api.PHYSICAL_NETWORK: 'fake_physical_network',
            api.NETWORK_ID: 'fake_network_id'}]


class AsterCXSwitchMechanismBaseTestCase(testlib_api.SqlTestCase):
    ASTER_SEGMENT = {
        api.ID: 'aster_vxlan_segment_id',
        api.NETWORK_TYPE: 'aster_vxlan',
        api.PHYSICAL_NETWORK: 'fake_physical_network',
        api.NETWORK_ID: 'fake_network_id'}

    def setUp(self):
        super(AsterCXSwitchMechanismBaseTestCase, self).setUp()
        self.driver = mech_aster.AsterCXSwitchMechanismDriver()
        self.context = me_base.FakeNetworkContext(SEGMENTS)


class AsterCXSwitchCheckTestCase(AsterCXSwitchMechanismBaseTestCase):

    def setUp(self):
        super(AsterCXSwitchCheckTestCase, self).setUp()

    def test__get_segments_with_None_top(self):
        top_segment = None
        bottom_segment = {
            api.ID: 'aster_vxlan_segment_id',
            api.NETWORK_TYPE: 'aster_vxlan',
            api.PHYSICAL_NETWORK: 'fake_physical_network',
            api.NETWORK_ID: 'fake_network_id'
        }
        top, bottom = self.driver._get_segments(top_segment, bottom_segment)
        self.assertIsNone(top)
        self.assertIsNone(bottom)

    def test__get_segments_with_aster_vxlan_top(self):
        top_segment = self.ASTER_SEGMENT
        bottom_segment = {
            api.ID: 'aster_vxlan_segment_id',
            api.NETWORK_TYPE: 'test',
            api.PHYSICAL_NETWORK: 'fake_physical_network',
            api.NETWORK_ID: 'fake_network_id'
        }
        top, bottom = self.driver._get_segments(top_segment, bottom_segment)
        self.assertEqual(top, bottom_segment)
        self.assertEqual(bottom, top_segment)

    def test__get_segments_with_not_aster_vxlan_top(self):
        top_segment = {
            api.ID: 'aster_vxlan_segment_id',
            api.NETWORK_TYPE: 'test',
            api.PHYSICAL_NETWORK: 'fake_physical_network',
            api.NETWORK_ID: 'fake_network_id'
        }
        bottom_segment = None
        top, bottom = self.driver._get_segments(top_segment, bottom_segment)
        self.assertEqual(top, top_segment)
        self.assertIsNone(bottom)


class AsterCXSwitchBaseTestCase(AsterCXSwitchMechanismBaseTestCase):

    def setUp(self):
        super(AsterCXSwitchBaseTestCase, self).setUp()

    def _set_up_fixture(self):
        self.context._current = {
            "network_id": "fake_net",
            portbindings.HOST_ID: "fake_host_1"
        }
        fake_switch_info = {
            "fake_switch_1": {
                "host_ports_mapping": {
                    "fake_host_1": ["Y25"]
                },
                "physnet": "fake_phy_net_1"
            },
            "fake_switch_2": {
                "host_ports_mapping": {
                    "fake_host_2": ["Y20"]
                },
                "physnet": "fake_phy_net_1"
            },
            "fake_switch_3": {
                "host_ports_mapping": {
                    "fake_host_1": ["Y35"]
                    },
                "physnet": "fake_phy_net_2"
            },
            "fake_switch_4": {
                "host_ports_mapping": {
                    "fake_host_1": ["Y35"]
                    },
                "physnet": "fake_phy_net_1"
            }
        }
        cfg.CONF.set_override(
            "cx_switches",
            fake_switch_info,
            group="ml2_aster")


class AsterCXSwitchDbTestCase(AsterCXSwitchBaseTestCase):

    mock_sub_path = ('networking_afc.common.utils.'
                     'get_subnet_detail_by_network_id')

    def setUp(self):
        super(AsterCXSwitchDbTestCase, self).setUp()

    # def _get_current(self):
    #     current = {'status': 'ACTIVE',
    #                'subnets': [],
    #                'name': 'net1',
    #                'provider:physical_network': None,
    #                'admin_state_up': True,
    #                'tenant_id': 'test-tenant',
    #                'provider:network_type': 'local',
    #                'router:external': False,
    #                'shared': False,
    #                'id': 'd897e21a-dfd6-4331-a5dd-7524fa421c3e',
    #                'provider:segmentation_id': None}
    #     self.context.current = current

    def _create_record(self, port_binding):
        session = db_api.get_writer_session()
        with session.begin():
            binding = aster_models_v2.AsterPortBinding(
                switch_ip=port_binding.get("switch_ip"),
                vlan_id=port_binding.get("vlan_id"),
                l2_vni=port_binding.get("l2_vni"),
                subnet_id=port_binding.get("subnet_id")
            )
            session.add(binding)
            session.flush()

    def _get_record(self, subnet_id=None):
        session = db_api.get_reader_session()
        with session.begin():
            result = session.query(aster_models_v2.AsterPortBinding). \
                filter_by(subnet_id=subnet_id).all()
        return result

    def test__configure_physical_switch_db_with(self):
        self._set_up_fixture()
        vlan_seg = {
            api.SEGMENTATION_ID: 'vlan_segment_id',
            api.NETWORK_TYPE: 'test_vlan',
            api.PHYSICAL_NETWORK: 'fake_phy_net_1',
            api.NETWORK_ID: 'fake_network_id'}
        vxlan_seg = {
            api.SEGMENTATION_ID: 'vxlan_segment_id',
            api.NETWORK_TYPE: 'test_vxlan',
            api.PHYSICAL_NETWORK: 'fake_phy_net_1',
            api.NETWORK_ID: 'fake_network_id'}
        test_record = {
            "switch_ip": "fake_switch_1",
            "vlan_id": "xx",
            "l2_vni": "xx",
            "subnet_id": "test_id"
        }
        self._create_record(test_record)
        current_db = self._get_record(subnet_id="test_id")
        self.assertEqual(1, len(current_db))
        with mock.patch(self.mock_sub_path,
                        return_value={"subnet_id": "test_id"}) as mock_sub:
            self.driver._configure_physical_switch_db(
                self.context.current,
                vlan_segment=vlan_seg,
                vxlan_segment=vxlan_seg)
        new_db = self._get_record(subnet_id="test_id")
        self.assertEqual(2, len(new_db))
        self.assertTrue(mock_sub.called)


class AsterCXSwitchConfigTestCase(AsterCXSwitchDbTestCase):

    mock_sub_2_path = ("networking_afc.common.utils."
                       "get_router_interface_by_subnet_id")
    get_ports_path = ("networking_afc.common.utils."
                      "get_ports_by_subnet")

    vlan_seg = {
        api.SEGMENTATION_ID: 'vlan_segment_id',
        api.NETWORK_TYPE: 'test_vlan',
        api.PHYSICAL_NETWORK: 'fake_phy_net_1',
        api.NETWORK_ID: 'fake_network_id'}
    vxlan_seg = {
        api.SEGMENTATION_ID: 'vxlan_segment_id',
        api.NETWORK_TYPE: 'test_vxlan',
        api.PHYSICAL_NETWORK: 'fake_phy_net_1',
        api.NETWORK_ID: 'fake_network_id'}

    def setUp(self):
        super(AsterCXSwitchConfigTestCase, self).setUp()
        self._set_up_fixture()

    def _test__configure_physical_switch(self, port, vxlan_segment,
                                         vlan_segment):
        a = mock.Mock()
        # a.send_config_to_afc = mock.Mock()
        self.driver.afc_api.send_config_to_afc = a
        result = self.driver._configure_physical_switch(
            port=port,
            vxlan_segment=vxlan_segment,
            vlan_segment=vlan_segment)
        return result

    def test__configure_physical_switch_with_invaild_segment(self):
        with mock.patch(self.mock_sub_path, return_value=None) as mock_sub:
            result = self._test__configure_physical_switch(
                self.context.current,
                self.vxlan_seg,
                None)
        self.assertIsNone(result)
        self.assertFalse(mock_sub.called)

    def test__configure_physical_switch_with_no_subnet_detail(self):
        with mock.patch(self.mock_sub_path, return_value=None) as mock_sub:
            result = self._test__configure_physical_switch(
                self.context.current,
                self.vxlan_seg,
                self.vlan_seg)
        self.assertIsNone(result)
        self.assertTrue(mock_sub.called)

    def test__configure_physical_switch_with_no_db_record(self):
        with mock.patch(self.mock_sub_path,
                        return_value={"subnet_id": "test_id",
                                      "gw_and_mask": "fake_ip"}) as mock_sub:
            result = self._test__configure_physical_switch(
                self.context.current,
                self.vxlan_seg,
                self.vlan_seg)
        self.assertIsNone(result)
        self.assertTrue(mock_sub.called)

    def test__configure_physical_switch_with_no_conn_router_interface(self):
        test_record = {
            "switch_ip": "fake_switch_1",
            "vlan_id": "xx",
            "l2_vni": "xx",
            "subnet_id": "test_id"
        }
        self._create_record(test_record)
        with mock.patch(self.mock_sub_path,
                        return_value={
                            "subnet_id": "test_id",
                            "gw_and_mask": "fake_ip"}) as mock_sub:
            with mock.patch(self.mock_sub_2_path,
                            return_value=None) as mock_sub_2:
                self._test__configure_physical_switch(
                    self.context.current,
                    self.vxlan_seg,
                    self.vlan_seg)
        db_record = self._get_record(subnet_id="test_id")
        self.assertTrue(db_record[0].get("is_config_l2"))
        self.assertTrue(mock_sub.called)
        self.assertTrue(mock_sub_2.called)

    def test__configure_physical_switch_with_full_process(self):
        mock_l3_path = ("networking_afc.ml2_drivers.mech_aster."
                        "mech_driver.mech_aster.add_interface_to_router")
        test_record = {
            "switch_ip": "fake_switch_1",
            "vlan_id": "xx",
            "l2_vni": "xx",
            "subnet_id": "test_id"
        }
        self._create_record(test_record)
        with mock.patch(self.mock_sub_path,
                        return_value={"subnet_id": "test_id",
                                      "gw_and_mask": "fake_ip"}) as mock_sub:
            with mock.patch(self.mock_sub_2_path,
                            return_value={"id": "fake_router_id",
                                          "l3_vni": "fake_l3"}) as mock_sub_2:
                with mock.patch(mock_l3_path,
                                return_value="mock_method") as mock_l3:
                    self._test__configure_physical_switch(
                        self.context.current,
                        self.vxlan_seg,
                        self.vlan_seg)
        db_record = self._get_record(subnet_id="test_id")
        self.assertTrue(db_record[0].get("is_config_l2"))
        self.assertEqual(db_record[0].get("router_id"), "fake_router_id")
        self.assertEqual(db_record[0].get("l3_vni"), "fake_l3")
        self.assertTrue(mock_sub.called)
        self.assertTrue(mock_sub_2.called)
        self.assertTrue(mock_l3.called)

    def _test__delete_physical_switch_config(self, port,
                                             vxlan_segment, vlan_segment):
        a = mock.Mock()
        # a.send_config_to_afc = mock.Mock()
        self.driver.afc_api.delete_config_from_afc = a
        result = self.driver._delete_physical_switch_config(
            port=port,
            vxlan_segment=vxlan_segment,
            vlan_segment=vlan_segment)
        return result

    def test__delete_physical_switch_config_with_invaild_segment(self):
        with mock.patch(self.mock_sub_path, return_value=None) as mock_sub:
            result = self._test__delete_physical_switch_config(
                self.context.current,
                self.vxlan_seg,
                None)
        self.assertIsNone(result)
        self.assertFalse(mock_sub.called)

    def test__delete_physical_switch_config_with_no_subnet_detail(self):
        with mock.patch(self.mock_sub_path, return_value=None) as mock_sub:
            result = self._test__delete_physical_switch_config(
                self.context.current,
                self.vxlan_seg,
                self.vlan_seg)
        self.assertIsNone(result)
        self.assertTrue(mock_sub.called)

    def test__delete_physical_switch_config_with_subnet_ports_on_host(self):
        test_record = {
            "switch_ip": "fake_switch_1",
            "vlan_id": "xx",
            "l2_vni": "xx",
            "subnet_id": "test_id"
        }
        self._create_record(test_record)
        db_record = self._get_record(subnet_id="test_id")
        self.assertEqual(1, len(db_record))
        with mock.patch(self.mock_sub_path,
                        return_value={"subnet_id": "test_id",
                                      "gw_and_mask": "fake_ip"}) as mock_sub:
            with mock.patch(self.get_ports_path,
                            return_value={"a": "test_sub"}) as mock_sub_2:
                self._test__delete_physical_switch_config(
                    self.context.current,
                    self.vxlan_seg,
                    self.vlan_seg)
        current_record = self._get_record(subnet_id="test_id")
        self.assertEqual(1, len(current_record))
        self.assertTrue(mock_sub.called)
        self.assertTrue(mock_sub_2.called)

    def test__delete_physical_switch_config_with_no_router_interface(self):
        test_record = {
            "switch_ip": "fake_switch_1",
            "vlan_id": "xx",
            "l2_vni": "xx",
            "subnet_id": "test_id"
        }
        self._create_record(test_record)
        db_record = self._get_record(subnet_id="test_id")
        self.assertEqual(1, len(db_record))
        with mock.patch(self.mock_sub_path,
                        return_value={"subnet_id": "test_id",
                                      "gw_and_mask": "fake_ip"}) as mock_sub:
            with mock.patch(self.get_ports_path,
                            return_value={}) as mock_sub_2:
                with mock.patch(self.mock_sub_2_path,
                                return_value={}) as mock_sub_3:
                    self._test__delete_physical_switch_config(
                        self.context.current,
                        self.vxlan_seg,
                        self.vlan_seg)
        current_record = self._get_record(subnet_id="test_id")
        self.assertEqual(0, len(current_record))
        self.assertTrue(mock_sub.called)
        self.assertTrue(mock_sub_2.called)
        self.assertTrue(mock_sub_3.called)

    def test__delete_physical_switch_config_with_normal_process(self):
        test_record = {
            "switch_ip": "fake_switch_1",
            "vlan_id": "xx",
            "l2_vni": "xx",
            "subnet_id": "test_id"
        }
        delete_interface_path = ("networking_afc.ml2_drivers.mech_aster."
                                 "mech_driver.mech_aster."
                                 "delete_interface_from_router")
        self._create_record(test_record)
        db_record = self._get_record(subnet_id="test_id")
        self.assertEqual(1, len(db_record))
        with mock.patch(self.mock_sub_path,
                        return_value={"subnet_id": "test_id",
                                      "gw_and_mask": "fake_ip"}) as mock_sub:
            with mock.patch(self.mock_sub_2_path,
                            return_value={"aa": "test_router"}) as mock_sub_2:
                with mock.patch(self.get_ports_path,
                                return_value={}) as mock_sub_3:
                    with mock.patch(delete_interface_path,
                                    return_value="mock_method") as mock_l3:
                        self._test__delete_physical_switch_config(
                            self.context.current,
                            self.vxlan_seg,
                            self.vlan_seg)
        current_record = self._get_record(subnet_id="test_id")
        self.assertEqual(0, len(current_record))
        self.assertTrue(mock_sub.called)
        self.assertTrue(mock_sub_2.called)
        self.assertTrue(mock_sub_3.called)
        self.assertTrue(mock_l3.called)


class AsterCXSwitchCommitTestCase(AsterCXSwitchBaseTestCase):
    vlan_seg = {
        api.SEGMENTATION_ID: 'vlan_segment_id',
        api.NETWORK_TYPE: 'test_vlan',
        api.PHYSICAL_NETWORK: 'fake_phy_net_1',
        api.NETWORK_ID: 'fake_network_id'}
    vxlan_seg = {
        api.SEGMENTATION_ID: 'vxlan_segment_id',
        api.NETWORK_TYPE: 'test_vxlan',
        api.PHYSICAL_NETWORK: 'fake_phy_net_1',
        api.NETWORK_ID: 'fake_network_id'}

    def setUp(self):
        super(AsterCXSwitchCommitTestCase, self).setUp()
        self.net_ctx = self._generate_ctx()
        self.port_ctx = self._generate_port_ctx()

    def _generate_ctx(self):
        context = mock.Mock()
        context.current = {"nekwork_id": "test_network"}
        return context

    def _generate_port_ctx(self):
        context = mock.Mock()
        context.current = {
            portbindings.HOST_ID: "new_id",
            "status": "DOWN",
            "device_owner": "test_owner"
        }
        context.origin = {
            portbindings.HOST_ID: "old_id",
            "status": "up"
        }
        context.top_bound_segment = self.vlan_seg
        context.bottom_bound_segment = self.vxlan_seg
        context.original_top_bound_segment = None
        context.original_bottom_bound_segment = self.vlan_seg
        return context

    def test_create_network_precommit(self):
        pass

    def test_delete_network_postcommit(self):
        pass

    def test_create_subnet_precommit(self):
        mock_path = ('networking_afc.common.utils.'
                     'get_subnets_by_network_id')
        with mock.patch(mock_path,
                        return_value=[{"name": "test_subnet"}]):
            self.assertRaises(exc.AsterDisallowCreateSubnet,
                              self.driver.create_subnet_precommit,
                              self.net_ctx)

    def test_create_port_postcommit(self):
        pass

    def test_update_port_precommit(self):
        mock_db = mock.Mock()
        self.driver._configure_physical_switch_db = mock_db
        self.driver.update_port_precommit(self.port_ctx)
        self.assertFalse(mock_db.called)
        normal_ctx = copy.deepcopy(self.port_ctx)
        normal_ctx.current.update({"device_owner": "computetest",
                                   "status": "up"})
        self.driver.update_port_precommit(normal_ctx)
        self.assertTrue(mock_db.called)

    def test_update_port_postcommit(self):
        mock_create = mock.Mock()
        mock_delete = mock.Mock()
        self.driver._configure_physical_switch = mock_create
        self.driver._delete_physical_switch_config = mock_delete
        self.driver.update_port_postcommit(self.port_ctx)
        self.assertTrue(mock_delete.called)
        normal_ctx = copy.deepcopy(self.port_ctx)
        normal_ctx.current.update({"device_owner": "computetest",
                                   "status": "up"})
        self.driver.update_port_postcommit(normal_ctx)
        self.assertTrue(mock_create.called)

    def test_delete_port_precommit(self):
        pass

    def test_delete_port_postcommit(self):
        a = mock.Mock()
        self.driver._delete_physical_switch_config = a
        self.driver.delete_port_postcommit(self.port_ctx)
        self.assertFalse(a.called)
        normal_ctx = copy.deepcopy(self.port_ctx)
        normal_ctx.current.update({"device_owner": "computetest"})
        self.driver.delete_port_postcommit(normal_ctx)
        self.assertTrue(a.called)

    def test_bind_port(self):
        pass


class AsterCXSwitchCommitAPITestCase(AsterCXSwitchMechanismBaseTestCase):

    def setUp(self):
        super(AsterCXSwitchCommitAPITestCase, self).setUp()
