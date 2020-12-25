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

import copy

from neutron_lib.api.definitions import portbindings
from neutron_lib.plugins.ml2 import api


NETWORK_ID = "fake_network"
PORT_ID = "fake_port"


class FakeNetworkContext(api.NetworkContext):
    def __init__(self, segments):
        self._network_segments = segments
        self._top_bound_segment = None
        self._bottom_bound_segment = None
        self._current = None

    @property
    def current(self):
        # return {'network_id': NETWORK_ID}
        return self._current

    @property
    def original(self):
        return None

    @property
    def network_segments(self):
        return self._network_segments

    @property
    def top_bound_segment(self):
        return self._top_bound_segment

    @property
    def bottom_bound_segment(self):
        return self._bottom_bound_segment


class FakePortContext(api.PortContext):
    def __init__(self, segments, profile=None, original=None):
        self._network_context = FakeNetworkContext(segments)
        self._bound_profile = profile
        self._bound_segment_id = None
        self._bound_vif_type = None
        self._bound_vif_details = None
        self._original = original
        self._binding_levels = []

    @property
    def current(self):
        current_data = {'id': PORT_ID,
                        # portbindings.VNIC_TYPE: self._bound_vnic_type,
                        portbindings.PROFILE: self._bound_profile}
        ret_value = current_data
        if self._original:
            ret_value = copy.deepcopy(self.original)
            ret_value.update(current_data)
        return ret_value

    @property
    def original(self):
        return self._original

    @property
    def status(self):
        return 'DOWN'

    @property
    def original_status(self):
        return None

    @property
    def network(self):
        return self._network_context

    def _prepare_to_bind(self, segments_to_bind):
        self._segments_to_bind = segments_to_bind
        self._new_bound_segment = None
        self._next_segments_to_bind = None

    def _push_binding_level(self, binding_level):
        self._binding_levels.append(binding_level)

    def _pop_binding_level(self):
        return self._binding_levels.pop()

    @property
    def binding_levels(self):
        if self._binding_levels:
            return [{
                api.BOUND_DRIVER: level.driver,
                api.BOUND_SEGMENT: self._expand_segment(level.segment_id)
            } for level in self._binding_levels]

    @property
    def original_binding_levels(self):
        return None

    @property
    def top_bound_segment(self):
        if self._binding_levels:
            return self._expand_segment(self._binding_levels[0].segment_id)

    @property
    def original_top_bound_segment(self):
        return None

    @property
    def bottom_bound_segment(self):
        if self._binding_levels:
            return self._expand_segment(self._binding_levels[-1].segment_id)

    @property
    def original_bottom_bound_segment(self):
        return None

    def _expand_segment(self, segment_id):
        for segment in self._network_context.network_segments:
            if segment[api.ID] == self._bound_segment_id:
                return segment

    @property
    def host(self):
        return ''

    @property
    def original_host(self):
        return None

    @property
    def vif_details(self):
        return None

    @property
    def original_vif_details(self):
        return None

    @property
    def segments_to_bind(self):
        return self._network_context.network_segments

    # def host_agents(self, agent_type):
    #     if agent_type == self._agent_type:
    #         return self._agents
    #     else:
    #         return []

    def set_binding(self, segment_id, vif_type, vif_details):
        self._bound_segment_id = segment_id
        self._bound_vif_type = vif_type
        self._bound_vif_details = vif_details

    def continue_binding(self, segment_id, next_segments_to_bind):
        pass

    def allocate_dynamic_segment(self, segment):
        pass

    def release_dynamic_segment(self, segment_id):
        pass
