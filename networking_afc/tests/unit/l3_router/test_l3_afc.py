
from neutron.tests import base
from networking_afc.l3_router import l3_afc


class TestAFCL3TestCase(base.BaseTestCase):

    def setUp(self):
        super(TestAFCL3TestCase, self).setUp()
        self.l3_plugin = l3_afc.AsterL3ServicePlugin()
