import sys

from six import moves
from oslo_config import cfg
from oslo_log import log as logging

from neutron_lib.db import api as lib_db_api
from neutron_lib.plugins import utils as plugin_utils
from networking_afc.db.models import aster_models_v2
from networking_afc.common import utils
from oslo_db import exception as db_exc


LOG = logging.getLogger(__name__)


class BorderVlanManager(object):

    def __init__(self):
        self._parse_network_vlan_ranges()
        self._sync_vlan_allocations()

    def _parse_network_vlan_ranges(self):
        try:
            border_switches = cfg.CONF.ml2_aster.border_switches
            vlan_ranges = []
            for border_switch_ip,  border_switch in border_switches.items():
                vlan_ranges.append(
                    "{}:{}".format(border_switch_ip, border_switch.get("vlan_ranges")[0])
                )
            self.border_leaf_vlan_ranges = plugin_utils.parse_network_vlan_ranges(
                vlan_ranges)
        except Exception as ex:
            LOG.exception("Failed to parse border_leaf_vlan_ranges. ex: %s "
                          "Service terminated!", ex)
            sys.exit(1)

    @lib_db_api.retry_db_errors
    def _sync_vlan_allocations(self):
        session, writer_ctx_manager = utils.get_writer_session()
        with writer_ctx_manager:
            # get existing allocations for all physical networks
            allocations = dict()
            allocs = session.query(aster_models_v2.AsterLeafVlanAllocation).all()
            for alloc in allocs:
                if alloc.switch_ip not in allocations:
                    allocations[alloc.switch_ip] = list()
                allocations[alloc.switch_ip].append(alloc)

            # process vlan ranges for each configured border leaf
            for (switch_ip, vlan_ranges) in self.border_leaf_vlan_ranges.items():
                # determine current configured allocatable vlans for
                # this border leaf
                vlan_ids = set()
                for vlan_min, vlan_max in vlan_ranges:
                    vlan_ids |= set(moves.range(vlan_min, vlan_max + 1))

                # remove from table unallocated vlans not currently
                # allocatable
                if switch_ip in allocations:
                    for alloc in allocations[switch_ip]:
                        try:
                            # see if vlan is allocatable
                            vlan_ids.remove(alloc.vlan_id)
                        except KeyError:
                            # it's not allocatable, so check if its allocated
                            if not alloc.router_id:
                                # it's not, so remove it from table
                                LOG.debug("Removing vlan %(vlan_id)s on border switch_ip %(switch_ip)s from pool",
                                          {'vlan_id': alloc.vlan_id, 'switch_ip': switch_ip})
                                # This UPDATE WHERE statement blocks anyone
                                # from concurrently changing the allocation
                                # values to True while our transaction is
                                # open so we don't accidentally delete
                                # allocated segments. If someone has already
                                # allocated, update_objects will return 0 so we
                                # don't delete.
                                session.query(aster_models_v2.AsterLeafVlanAllocation).\
                                    filter_by(switch_ip=switch_ip, vlan_id=alloc.vlan_id, router_id="").\
                                    delete()
                                session.flush()
                    del allocations[switch_ip]

                # add missing allocatable vlans to table
                for vlan_id in sorted(vlan_ids):
                    binding = aster_models_v2.AsterLeafVlanAllocation(
                        switch_ip=switch_ip,
                        vlan_id=vlan_id,
                        router_id=""
                    )
                    session.add(binding)
                    session.flush()
            # remove from table unallocated vlans for any unconfigured
            # switch_ip
            for allocs in allocations.values():
                for alloc in allocs:
                    if not alloc.router_id:
                        LOG.debug("Removing vlan %(vlan_id)s on border leaf switch_ip "
                                  " %(switch_ip)s from pool",
                                  {'vlan_id': alloc.vlan_id,
                                   'switch_ip': alloc.switch_ip})
                        session.query(aster_models_v2.AsterLeafVlanAllocation).\
                            filter_by(switch_ip=alloc.switch_ip,
                                      vlan_id=alloc.vlan_id,
                                      router_id=alloc.router_id).delete()

    def allocate_segment(self, leaf_ip=None, router_id=None):
        session, ctx_manager = utils.get_writer_session()
        try:
            with ctx_manager:
                allocations = session.query(aster_models_v2.AsterLeafVlanAllocation).\
                    filter_by(switch_ip=leaf_ip, router_id="").all()
                if not allocations:
                    # No resource available
                    raise Exception
                alloc = allocations[-1]
                session.query(aster_models_v2.AsterLeafVlanAllocation).\
                    filter_by(switch_ip=leaf_ip, vlan_id=alloc.vlan_id).\
                    update({"router_id": router_id})
                session.flush()
        except db_exc.DBDuplicateEntry as ex:
            # Segment already allocated (insert failure)
            raise  ex

    def release_segment(self, leaf_ip=None, router_id=None):
        ranges = self.border_leaf_vlan_ranges.get(leaf_ip, [])
        vlan_ids = set()
        for vlan_id_min, vlan_id_max in ranges:
            vlan_ids |= set(moves.range(vlan_id_min, vlan_id_max + 1))

        session, ctx_manager = utils.get_writer_session()
        with ctx_manager:
            alloc = session.query(aster_models_v2.AsterLeafVlanAllocation). \
                filter_by(switch_ip=leaf_ip, router_id=router_id).first()
            if alloc and alloc.vlan_id in vlan_ids:
                alloc.router_id = ""
            else:
                session.query(aster_models_v2.AsterLeafVlanAllocation). \
                    filter_by(switch_ip=leaf_ip, router_id=router_id).delete()
            session.flush()
