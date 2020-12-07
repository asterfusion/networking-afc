import six

from oslo_config import cfg
from oslo_log import log as logging
from oslo_db import exception as db_exc
from networking_afc.db.models import aster_models_v2
from networking_afc.common import utils


LOG = logging.getLogger(__name__)

aster_l3_vni_opts = [
    cfg.ListOpt('l3_vni_ranges',
                default=[],
                help=_("Comma-separated list of <vni_min>:<vni_max> tuples "
                       "enumerating ranges of VXLAN Network IDs that are "
                       "available for tenant network allocation. "
                       "Example format: vni_ranges = 100:1000,2000:6000"))
]

cfg.CONF.register_opts(aster_l3_vni_opts, "ml2_type_aster_vxlan")


class L3VniManager(object):

    def __init__(self):
        self.l3_vni_ranges = []
        self._verify_vni_ranges()
        self.sync_allocations()

    def _verify_vni_ranges(self):
        try:
            self._parse_aster_l3_vni_ranges(
                cfg.CONF.ml2_type_aster_vxlan.l3_vni_ranges,
                self.l3_vni_ranges
            )
            # LOG.info("Aster CX Switch L3 VNI ranges: %s", self.conf_vxlan_ranges)
        except Exception:
            LOG.exception("Failed to parse l3_vni_ranges. Service terminated!")
            raise SystemExit()

    @staticmethod
    def _parse_aster_l3_vni_ranges(l3_ranges, current_range):
        for entry in l3_ranges:
            entry = entry.strip()
            try:
                l3_vni_min, l3_vni_max = entry.split(':')
                l3_vni_min = l3_vni_min.strip()
                l3_vni_max = l3_vni_max.strip()
                tunnel_range = int(l3_vni_min), int(l3_vni_max)
            except ValueError as ex:
                raise ex
                # raise exc.NetworkTunnelRangeError(tunnel_range=entry, error=ex)
            # self._parse_nexus_vni_range(tunnel_range)
            current_range.append(tunnel_range)

        LOG.info("Aster CX Switch L3 VNI ranges: %(range)s",
                 {'range': current_range})

    def sync_allocations(self):
        """
        Synchronize vxlan_allocations table with configured tunnel ranges.
        """

        # determine current configured allocatable vnis
        l3_vnis = set()
        for l3_vni_min, l3_vni_max in self.l3_vni_ranges:
            l3_vnis |= set(six.moves.range(l3_vni_min, l3_vni_max + 1))

        session, writer_ctx_manager = utils.get_writer_session()
        with writer_ctx_manager:
            # remove from table unallocated tunnels not currently allocatable
            # fetch results as list via all() because we'll be iterating
            # through them twice
            allocs = (session.query(aster_models_v2.AsterL3VNIAllocation).
                      with_lockmode("update").all())
            # collect all vnis present in db
            existing_vnis = set(alloc.l3_vni for alloc in allocs)
            # collect those vnis that needs to be deleted from db
            vnis_to_remove = [alloc.l3_vni for alloc in allocs
                              if (alloc.l3_vni not in l3_vnis and
                                  not alloc.router_id)]
            # Immediately delete vnis in chunks. This leaves no work for
            # flush at the end of transaction
            bulk_size = 100
            chunked_vnis = (vnis_to_remove[i: i + bulk_size] for i in
                            range(0, len(vnis_to_remove), bulk_size))
            for vni_list in chunked_vnis:
                session.query(aster_models_v2.AsterL3VNIAllocation).filter(
                    aster_models_v2.AsterL3VNIAllocation.
                    l3_vni.in_(vni_list)).delete(
                        synchronize_session=False)
            # collect vnis that need to be added
            vnis = list(l3_vnis - existing_vnis)
            chunked_vnis = (vnis[i: i + bulk_size] for i in
                            range(0, len(vnis), bulk_size))
            for vni_list in chunked_vnis:
                bulk = [{'l3_vni': vni, 'router_id': ""}
                        for vni in vni_list]
                session.execute(aster_models_v2.AsterL3VNIAllocation.
                                __table__.insert(), bulk)

    def allocation_l3_vni(self, router_id):
        # Allocations one l3 vni to VRouter
        session, ctx_manager = utils.get_writer_session()
        try:
            with ctx_manager:
                all_l3_vni = session.query(aster_models_v2.AsterL3VNIAllocation).\
                    filter_by(router_id="").all()
                if not all_l3_vni:
                    # No resource available
                    raise Exception
                one_l3_vni = all_l3_vni[-1]
                session.query(aster_models_v2.AsterL3VNIAllocation).\
                    filter_by(l3_vni=one_l3_vni.l3_vni).\
                    update({"router_id": router_id})
                session.flush()
        except db_exc.DBDuplicateEntry as ex:
            # Segment already allocated (insert failure)
            raise  ex

    def release_l3_vni(self, router_id):
        # Release l3 vni from VRouter
        l3_vnis = set()
        for l3_vni_min, l3_vni_max in self.l3_vni_ranges:
            l3_vnis |= set(six.moves.range(l3_vni_min, l3_vni_max + 1))

        session, ctx_manager = utils.get_writer_session()
        with ctx_manager:
            alloc = session.query(aster_models_v2.AsterL3VNIAllocation). \
                filter_by(router_id=router_id).first()
            if alloc and alloc.l3_vni in l3_vnis:
                alloc.router_id = ""
            else:
                session.query(aster_models_v2.AsterL3VNIAllocation). \
                    filter_by(router_id=router_id).delete()
            session.flush()

