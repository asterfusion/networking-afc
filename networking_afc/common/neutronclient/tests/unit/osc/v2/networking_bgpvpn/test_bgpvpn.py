# Copyright (c) 2016 Juniper Networks Inc.
# All Rights Reserved.
#
#   Licensed under the Apache License, Version 2.0 (the "License"); you may
#   not use this file except in compliance with the License. You may obtain
#   a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#   WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#   License for the specific language governing permissions and limitations
#   under the License.
#

import copy
import operator

import mock
from osc_lib import exceptions
from osc_lib import utils as osc_utils

from neutronclient.osc import utils as nc_osc_utils
from neutronclient.osc.v2.networking_bgpvpn import bgpvpn
from neutronclient.osc.v2.networking_bgpvpn import constants
from neutronclient.tests.unit.osc.v2.networking_bgpvpn import fakes


columns_short = tuple(col for col, _, listing_mode in bgpvpn._attr_map
                      if listing_mode in (nc_osc_utils.LIST_BOTH,
                                          nc_osc_utils.LIST_SHORT_ONLY))
columns_long = tuple(col for col, _, listing_mode in bgpvpn._attr_map
                     if listing_mode in (nc_osc_utils.LIST_BOTH,
                                         nc_osc_utils.LIST_LONG_ONLY))
headers_short = tuple(head for _, head, listing_mode in bgpvpn._attr_map
                      if listing_mode in (nc_osc_utils.LIST_BOTH,
                                          nc_osc_utils.LIST_SHORT_ONLY))
headers_long = tuple(head for _, head, listing_mode in bgpvpn._attr_map
                     if listing_mode in (nc_osc_utils.LIST_BOTH,
                                         nc_osc_utils.LIST_LONG_ONLY))
sorted_attr_map = sorted(bgpvpn._attr_map, key=operator.itemgetter(1))
sorted_columns = tuple(col for col, _, _ in sorted_attr_map)
sorted_headers = tuple(head for _, head, _ in sorted_attr_map)


def _get_data(attrs, columns=sorted_columns):
    return osc_utils.get_dict_properties(attrs, columns,
                                         formatters=bgpvpn._formatters)


class TestCreateBgpvpn(fakes.TestNeutronClientBgpvpn):
    def setUp(self):
        super(TestCreateBgpvpn, self).setUp()
        self.cmd = bgpvpn.CreateBgpvpn(self.app, self.namespace)

    def test_create_bgpvpn_with_no_args(self):
        fake_bgpvpn = fakes.FakeBgpvpn.create_one_bgpvpn()
        self.neutronclient.create_bgpvpn = mock.Mock(
            return_value={constants.BGPVPN: fake_bgpvpn})
        arglist = []
        verifylist = [
            ('project', None),
            ('name', None),
            ('type', 'l3'),
            ('route_targets', None),
            ('import_targets', None),
            ('export_targets', None),
            ('route_distinguishers', None),
        ]

        parsed_args = self.check_parser(self.cmd, arglist, verifylist)

        cols, data = self.cmd.take_action(parsed_args)

        self.neutronclient.create_bgpvpn.assert_called_once_with(
            {constants.BGPVPN: {'type': 'l3'}})
        self.assertEqual(sorted_headers, cols)
        self.assertItemEqual(_get_data(fake_bgpvpn), data)

    def test_create_bgpvpn_with_all_args(self):
        attrs = {
            'tenant_id': 'new_fake_project_id',
            'name': 'fake_name',
            'type': 'l2',
            'route_targets': ['fake_rt1', 'fake_rt2', 'fake_rt3'],
            'import_targets': ['fake_irt1', 'fake_irt2', 'fake_irt3'],
            'export_targets': ['fake_ert1', 'fake_ert2', 'fake_ert3'],
            'route_distinguishers': ['fake_rd1', 'fake_rd2', 'fake_rd3'],
        }
        fake_bgpvpn = fakes.FakeBgpvpn.create_one_bgpvpn(attrs)
        self.neutronclient.create_bgpvpn = mock.Mock(
            return_value={constants.BGPVPN: fake_bgpvpn})
        arglist = [
            '--project', fake_bgpvpn['tenant_id'],
            '--name', fake_bgpvpn['name'],
            '--type', fake_bgpvpn['type'],
        ]
        for rt in fake_bgpvpn['route_targets']:
            arglist.extend(['--route-target', rt])
        for rt in fake_bgpvpn['import_targets']:
            arglist.extend(['--import-target', rt])
        for rt in fake_bgpvpn['export_targets']:
            arglist.extend(['--export-target', rt])
        for rd in fake_bgpvpn['route_distinguishers']:
            arglist.extend(['--route-distinguisher', rd])
        verifylist = [
            ('project', fake_bgpvpn['tenant_id']),
            ('name', fake_bgpvpn['name']),
            ('type', fake_bgpvpn['type']),
            ('route_targets', fake_bgpvpn['route_targets']),
            ('import_targets', fake_bgpvpn['import_targets']),
            ('export_targets', fake_bgpvpn['export_targets']),
            ('route_distinguishers', fake_bgpvpn['route_distinguishers']),
        ]

        parsed_args = self.check_parser(self.cmd, arglist, verifylist)

        cols, data = self.cmd.take_action(parsed_args)

        fake_bgpvpn_call = copy.deepcopy(fake_bgpvpn)
        fake_bgpvpn_call.pop('id')
        fake_bgpvpn_call.pop('networks')
        fake_bgpvpn_call.pop('routers')
        self.neutronclient.create_bgpvpn.assert_called_once_with(
            {constants.BGPVPN: fake_bgpvpn_call})
        self.assertEqual(sorted_headers, cols)
        self.assertItemEqual(_get_data(fake_bgpvpn), data)


class TestSetBgpvpn(fakes.TestNeutronClientBgpvpn):
    def setUp(self):
        super(TestSetBgpvpn, self).setUp()
        self.cmd = bgpvpn.SetBgpvpn(self.app, self.namespace)

    def test_set_bgpvpn(self):
        attrs = {
            'route_targets': ['set_rt1', 'set_rt2', 'set_rt3'],
            'import_targets': ['set_irt1', 'set_irt2', 'set_irt3'],
            'export_targets': ['set_ert1', 'set_ert2', 'set_ert3'],
            'route_distinguishers': ['set_rd1', 'set_rd2', 'set_rd3'],
        }
        fake_bgpvpn = fakes.FakeBgpvpn.create_one_bgpvpn(attrs)
        self.neutronclient.show_bgpvpn = mock.Mock(
            return_value={constants.BGPVPN: fake_bgpvpn})
        self.neutronclient.update_bgpvpn = mock.Mock()
        arglist = [
            fake_bgpvpn['id'],
            '--name', 'set_name',
            '--route-target', 'set_rt1',
            '--import-target', 'set_irt1',
            '--export-target', 'set_ert1',
            '--route-distinguisher', 'set_rd1',
        ]
        verifylist = [
            ('bgpvpn', fake_bgpvpn['id']),
            ('name', 'set_name'),
            ('route_targets', ['set_rt1']),
            ('purge_route_target', False),
            ('import_targets', ['set_irt1']),
            ('purge_import_target', False),
            ('export_targets', ['set_ert1']),
            ('purge_export_target', False),
            ('route_distinguishers', ['set_rd1']),
            ('purge_route_distinguisher', False),
        ]

        parsed_args = self.check_parser(self.cmd, arglist, verifylist)

        result = self.cmd.take_action(parsed_args)

        attrs = {
            'name': 'set_name',
            'route_targets': list(set(fake_bgpvpn['route_targets']) |
                                  set(['set_rt1'])),
            'import_targets': list(set(fake_bgpvpn['import_targets']) |
                                   set(['set_irt1'])),
            'export_targets': list(set(fake_bgpvpn['export_targets']) |
                                   set(['set_ert1'])),
            'route_distinguishers': list(
                set(fake_bgpvpn['route_distinguishers']) | set(['set_rd1'])),
        }
        self.neutronclient.update_bgpvpn.assert_called_once_with(
            fake_bgpvpn['id'], {constants.BGPVPN: attrs})
        self.assertIsNone(result)

    def test_set_bgpvpn_with_purge_list(self):
        fake_bgpvpn = fakes.FakeBgpvpn.create_one_bgpvpn()
        self.neutronclient.show_bgpvpn = mock.Mock(
            return_value={constants.BGPVPN: fake_bgpvpn})
        self.neutronclient.update_bgpvpn = mock.Mock()
        arglist = [
            fake_bgpvpn['id'],
            '--route-target', 'set_rt1',
            '--no-route-target',
            '--import-target', 'set_irt1',
            '--no-import-target',
            '--export-target', 'set_ert1',
            '--no-export-target',
            '--route-distinguisher', 'set_rd1',
            '--no-route-distinguisher',
        ]
        verifylist = [
            ('bgpvpn', fake_bgpvpn['id']),
            ('route_targets', ['set_rt1']),
            ('purge_route_target', True),
            ('import_targets', ['set_irt1']),
            ('purge_import_target', True),
            ('export_targets', ['set_ert1']),
            ('purge_export_target', True),
            ('route_distinguishers', ['set_rd1']),
            ('purge_route_distinguisher', True),
        ]

        parsed_args = self.check_parser(self.cmd, arglist, verifylist)

        result = self.cmd.take_action(parsed_args)

        attrs = {
            'route_targets': [],
            'import_targets': [],
            'export_targets': [],
            'route_distinguishers': [],
        }
        self.neutronclient.update_bgpvpn.assert_called_once_with(
            fake_bgpvpn['id'], {constants.BGPVPN: attrs})
        self.assertIsNone(result)


class TestUnsetBgpvpn(fakes.TestNeutronClientBgpvpn):
    def setUp(self):
        super(TestUnsetBgpvpn, self).setUp()
        self.cmd = bgpvpn.UnsetBgpvpn(self.app, self.namespace)

    def test_unset_bgpvpn(self):
        attrs = {
            'route_targets': ['unset_rt1', 'unset_rt2', 'unset_rt3'],
            'import_targets': ['unset_irt1', 'unset_irt2', 'unset_irt3'],
            'export_targets': ['unset_ert1', 'unset_ert2', 'unset_ert3'],
            'route_distinguishers': ['unset_rd1', 'unset_rd2', 'unset_rd3'],
        }
        fake_bgpvpn = fakes.FakeBgpvpn.create_one_bgpvpn(attrs)
        self.neutronclient.show_bgpvpn = mock.Mock(
            return_value={constants.BGPVPN: fake_bgpvpn})
        self.neutronclient.update_bgpvpn = mock.Mock()
        arglist = [
            fake_bgpvpn['id'],
            '--route-target', 'unset_rt1',
            '--import-target', 'unset_irt1',
            '--export-target', 'unset_ert1',
            '--route-distinguisher', 'unset_rd1',
        ]
        verifylist = [
            ('bgpvpn', fake_bgpvpn['id']),
            ('route_targets', ['unset_rt1']),
            ('purge_route_target', False),
            ('import_targets', ['unset_irt1']),
            ('purge_import_target', False),
            ('export_targets', ['unset_ert1']),
            ('purge_export_target', False),
            ('route_distinguishers', ['unset_rd1']),
            ('purge_route_distinguisher', False),
        ]

        parsed_args = self.check_parser(self.cmd, arglist, verifylist)

        result = self.cmd.take_action(parsed_args)

        attrs = {
            'route_targets': list(set(fake_bgpvpn['route_targets']) -
                                  set(['unset_rt1'])),
            'import_targets': list(set(fake_bgpvpn['import_targets']) -
                                   set(['unset_irt1'])),
            'export_targets': list(set(fake_bgpvpn['export_targets']) -
                                   set(['unset_ert1'])),
            'route_distinguishers': list(
                set(fake_bgpvpn['route_distinguishers']) - set(['unset_rd1'])),
        }
        self.neutronclient.update_bgpvpn.assert_called_once_with(
            fake_bgpvpn['id'], {constants.BGPVPN: attrs})
        self.assertIsNone(result)

    def test_unset_bgpvpn_with_purge_list(self):
        fake_bgpvpn = fakes.FakeBgpvpn.create_one_bgpvpn()
        self.neutronclient.show_bgpvpn = mock.Mock(
            return_value={constants.BGPVPN: fake_bgpvpn})
        self.neutronclient.update_bgpvpn = mock.Mock()
        arglist = [
            fake_bgpvpn['id'],
            '--route-target', 'unset_rt1',
            '--all-route-target',
            '--import-target', 'unset_irt1',
            '--all-import-target',
            '--export-target', 'unset_ert1',
            '--all-export-target',
            '--route-distinguisher', 'unset_rd1',
            '--all-route-distinguisher',
        ]
        verifylist = [
            ('bgpvpn', fake_bgpvpn['id']),
            ('route_targets', ['unset_rt1']),
            ('purge_route_target', True),
            ('import_targets', ['unset_irt1']),
            ('purge_import_target', True),
            ('export_targets', ['unset_ert1']),
            ('purge_export_target', True),
            ('route_distinguishers', ['unset_rd1']),
            ('purge_route_distinguisher', True),
        ]

        parsed_args = self.check_parser(self.cmd, arglist, verifylist)

        result = self.cmd.take_action(parsed_args)

        attrs = {
            'route_targets': [],
            'import_targets': [],
            'export_targets': [],
            'route_distinguishers': [],
        }
        self.neutronclient.update_bgpvpn.assert_called_once_with(
            fake_bgpvpn['id'], {constants.BGPVPN: attrs})
        self.assertIsNone(result)


class TestDeleteBgpvpn(fakes.TestNeutronClientBgpvpn):
    def setUp(self):
        super(TestDeleteBgpvpn, self).setUp()
        self.neutronclient.find_resource = mock.Mock(
            side_effect=lambda _, name_or_id: {'id': name_or_id})
        self.cmd = bgpvpn.DeleteBgpvpn(self.app, self.namespace)

    def test_delete_one_bgpvpn(self):
        fake_bgpvpn = fakes.FakeBgpvpn.create_one_bgpvpn()
        self.neutronclient.delete_bgpvpn = mock.Mock()
        arglist = [
            fake_bgpvpn['id'],
        ]
        verifylist = [
            ('bgpvpns', [fake_bgpvpn['id']]),
        ]

        parsed_args = self.check_parser(self.cmd, arglist, verifylist)

        result = self.cmd.take_action(parsed_args)

        self.neutronclient.delete_bgpvpn.assert_called_once_with(
            fake_bgpvpn['id'])
        self.assertIsNone(result)

    def test_delete_multi_bpgvpn(self):
        fake_bgpvpns = fakes.FakeBgpvpn.create_bgpvpns(count=3)
        fake_bgpvpn_ids = [fake_bgpvpn['id'] for fake_bgpvpn in
                           fake_bgpvpns[constants.BGPVPNS]]
        self.neutronclient.delete_bgpvpn = mock.Mock()
        arglist = fake_bgpvpn_ids
        verifylist = [
            ('bgpvpns', fake_bgpvpn_ids),
        ]

        parsed_args = self.check_parser(self.cmd, arglist, verifylist)

        result = self.cmd.take_action(parsed_args)

        self.neutronclient.delete_bgpvpn.assert_has_calls(
            [mock.call(id) for id in fake_bgpvpn_ids])
        self.assertIsNone(result)

    def test_delete_multi_bpgvpn_with_unknown(self):
        count = 3
        fake_bgpvpns = fakes.FakeBgpvpn.create_bgpvpns(count=count)
        fake_bgpvpn_ids = [fake_bgpvpn['id'] for fake_bgpvpn in
                           fake_bgpvpns[constants.BGPVPNS]]

        def raise_unknonw_resource(resource_path, name_or_id):
            if str(count - 2) in name_or_id:
                raise Exception()
        self.neutronclient.delete_bgpvpn = mock.Mock(
            side_effect=raise_unknonw_resource)
        arglist = fake_bgpvpn_ids
        verifylist = [
            ('bgpvpns', fake_bgpvpn_ids),
        ]

        parsed_args = self.check_parser(self.cmd, arglist, verifylist)

        self.assertRaises(exceptions.CommandError, self.cmd.take_action,
                          parsed_args)

        self.neutronclient.delete_bgpvpn.assert_has_calls(
            [mock.call(id) for id in fake_bgpvpn_ids])


class TestListBgpvpn(fakes.TestNeutronClientBgpvpn):
    def setUp(self):
        super(TestListBgpvpn, self).setUp()
        self.cmd = bgpvpn.ListBgpvpn(self.app, self.namespace)

    def test_list_all_bgpvpn(self):
        count = 3
        fake_bgpvpns = fakes.FakeBgpvpn.create_bgpvpns(count=count)
        self.neutronclient.list_bgpvpns = mock.Mock(return_value=fake_bgpvpns)
        arglist = []
        verifylist = []

        parsed_args = self.check_parser(self.cmd, arglist, verifylist)

        headers, data = self.cmd.take_action(parsed_args)

        self.neutronclient.list_bgpvpns.assert_called_once()
        self.assertEqual(headers, list(headers_short))
        self.assertListItemEqual(
            list(data),
            [_get_data(fake_bgpvpn, columns_short) for fake_bgpvpn
             in fake_bgpvpns[constants.BGPVPNS]])

    def test_list_all_bgpvpn_long_mode(self):
        count = 3
        fake_bgpvpns = fakes.FakeBgpvpn.create_bgpvpns(count=count)
        self.neutronclient.list_bgpvpns = mock.Mock(return_value=fake_bgpvpns)
        arglist = [
            '--long',
        ]
        verifylist = [
            ('long', True),
        ]

        parsed_args = self.check_parser(self.cmd, arglist, verifylist)

        headers, data = self.cmd.take_action(parsed_args)

        self.neutronclient.list_bgpvpns.assert_called_once()
        self.assertEqual(headers, list(headers_long))
        self.assertListItemEqual(
            list(data),
            [_get_data(fake_bgpvpn, columns_long) for fake_bgpvpn
             in fake_bgpvpns[constants.BGPVPNS]])

    def test_list_project_bgpvpn(self):
        count = 3
        project_id = 'list_fake_project_id'
        attrs = {'tenant_id': project_id}
        fake_bgpvpns = fakes.FakeBgpvpn.create_bgpvpns(count=count,
                                                       attrs=attrs)
        self.neutronclient.list_bgpvpns = mock.Mock(return_value=fake_bgpvpns)
        arglist = [
            '--project', project_id,
        ]
        verifylist = [
            ('project', project_id),
        ]

        parsed_args = self.check_parser(self.cmd, arglist, verifylist)

        headers, data = self.cmd.take_action(parsed_args)

        self.neutronclient.list_bgpvpns.assert_called_once_with(
            tenant_id=project_id)
        self.assertEqual(headers, list(headers_short))
        self.assertListItemEqual(
            list(data),
            [_get_data(fake_bgpvpn, columns_short) for fake_bgpvpn
             in fake_bgpvpns[constants.BGPVPNS]])

    def test_list_bgpvpn_with_filters(self):
        count = 3
        name = 'fake_id0'
        layer_type = 'l2'
        attrs = {'type': layer_type}
        fake_bgpvpns = fakes.FakeBgpvpn.create_bgpvpns(count=count,
                                                       attrs=attrs)
        returned_bgpvpn = fake_bgpvpns[constants.BGPVPNS][0]
        self.neutronclient.list_bgpvpns = mock.Mock(
            return_value={constants.BGPVPNS: [returned_bgpvpn]})
        arglist = [
            '--property', 'name=%s' % name,
            '--property', 'type=%s' % layer_type,
        ]
        verifylist = [
            ('property', {'name': name, 'type': layer_type}),
        ]

        parsed_args = self.check_parser(self.cmd, arglist, verifylist)

        headers, data = self.cmd.take_action(parsed_args)

        self.neutronclient.list_bgpvpns.assert_called_once_with(
            name=name,
            type=layer_type)
        self.assertEqual(headers, list(headers_short))
        self.assertListItemEqual(list(data),
                                 [_get_data(returned_bgpvpn, columns_short)])


class TestShowBgpvpn(fakes.TestNeutronClientBgpvpn):
    def setUp(self):
        super(TestShowBgpvpn, self).setUp()
        self.cmd = bgpvpn.ShowBgpvpn(self.app, self.namespace)

    def test_show_bgpvpn(self):
        fake_bgpvpn = fakes.FakeBgpvpn.create_one_bgpvpn()
        self.neutronclient.show_bgpvpn = mock.Mock(
            return_value={constants.BGPVPN: fake_bgpvpn})
        arglist = [
            fake_bgpvpn['id'],
        ]
        verifylist = [
            ('bgpvpn', fake_bgpvpn['id']),
        ]

        parsed_args = self.check_parser(self.cmd, arglist, verifylist)

        headers, data = self.cmd.take_action(parsed_args)

        self.neutronclient.show_bgpvpn.assert_called_once_with(
            fake_bgpvpn['id'])
        self.assertEqual(sorted_headers, headers)
        self.assertItemEqual(_get_data(fake_bgpvpn), data)
