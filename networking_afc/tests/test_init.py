
from neutron_lib.plugins.ml2 import api

NETWORK_ID = "fake_net"


class FakeNetworkContext(api.NetworkContext):
    def __init__(self, segments):
        self._network_segments = segments
        self._top_bound_segment = None

    @property
    def current(self):
        return {'network_id': NETWORK_ID}

    @property
    def original(self):
        return None

    @property
    def top_bound_segment(self):
        return self._top_bound_segment

    @property
    def network_segments(self):
        return self._network_segments


if __name__ == "__main__":
    segments = {
        "aa": "aa"
    }
    a = FakeNetworkContext(segments)
