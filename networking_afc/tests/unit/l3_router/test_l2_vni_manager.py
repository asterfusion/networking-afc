import unittest

from neutron.tests.unit import testlib_api
from neutron_lib.db import api as db_api
from networking_afc.l3_router import l2_vni_manager
from networking_afc.db.models import aster_models_v2


class AfcL2VniManagerTestCase(testlib_api.SqlTestCase):

    def setUp(self):
        super(AfcL2VniManagerTestCase, self).setUp()
        self.manager = l2_vni_manager.L2VniManager()

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

    def test_allocation_l2_vni_with_no_db(self):
        self.assertRaises(Exception, self.manager.allocation_l2_vni, "test_id")
        # db_record = self._get_record(router_id="")

    def test_allocation_l2_vni_with_normal_db(self):
        test_record = {
            "l2_vni": 2222,
            "router_id": ""
        }
        self._create_record(test_record)
        self.manager.allocation_l2_vni("test_id")
        db_record = self._get_record(router_id="test_id")
        self.assertTrue(db_record)

    @unittest.skip("TestCase is illeagal")
    def test_release_l2_vni(self):
        test_record = {
            "l2_vni": 2000,
            "router_id": "test_id"
        }
        test_record_2 = {
            "l2_vni": 2200,
            "router_id": "test_id"
        }
        self._create_record(test_record)
        self._create_record(test_record_2)
        db_record = self._get_record(router_id="test_id")
        self.manager.l2_vni_ranges = [(1999, 2001)]
        self.manager.release_l2_vni("test_id")
        db_record = self._get_record(router_id="test_id")
        self.assertEqual(1, len(db_record))
        self.assertEqual(db_record[0].get("l2_vni"), 2000)
        self.assertEqual(db_record[0].get("router_id"), "")
