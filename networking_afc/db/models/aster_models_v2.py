import sqlalchemy as sa
from neutron_lib.db import model_base


class AsterVxlanAllocation(model_base.BASEV2):

    __tablename__ = 'ml2_aster_vxlan_allocations'

    vxlan_vni = sa.Column(sa.Integer, nullable=False, primary_key=True,
                          autoincrement=False)
    allocated = sa.Column(sa.Boolean, nullable=False, default=False,
                          server_default=sa.sql.false())


class AsterL3VNIAllocation(model_base.BASEV2):

    __tablename__ = 'ml2_aster_l3_vni_allocations'

    l3_vni = sa.Column(sa.Integer, nullable=False, primary_key=True, autoincrement=False)
    router_id = sa.Column(sa.String(255), nullable=True, default="")


class AsterL2VNIAllocation(model_base.BASEV2):

    __tablename__ = 'ml2_leaf_l2_vni_allocations'

    l2_vni = sa.Column(sa.Integer, nullable=False, primary_key=True, autoincrement=False)
    router_id = sa.Column(sa.String(255), nullable=True, default="")


class AsterLeafVlanAllocation(model_base.BASEV2):

    __tablename__ = 'ml2_leaf_vlan_allocations'

    switch_ip = sa.Column(sa.String(64), nullable=False,
                                 primary_key=True)
    vlan_id = sa.Column(sa.Integer, nullable=False, primary_key=True,
                        autoincrement=False)
    router_id = sa.Column(sa.String(64), nullable=False)


class AsterPortBinding(model_base.BASEV2):
    """Represents a binding of VM's to nexus ports."""

    __tablename__ = "aster_ml2_port_bindings"

    binding_id = sa.Column(sa.Integer, primary_key=True, autoincrement=True)
    switch_ip = sa.Column(sa.String(255))
    vlan_id = sa.Column(sa.Integer, nullable=False)
    l2_vni = sa.Column(sa.Integer, nullable=False)
    is_config_l2 =  sa.Column(sa.Boolean, nullable=False, default=False,
                          server_default=sa.sql.false())
    subnet_id = sa.Column(sa.String(255))
    router_id= sa.Column(sa.String(255), default="")
    l3_vni = sa.Column(sa.Integer, nullable=False, default=0)

    def __repr__(self):
        """Just the binding, without the id key."""
        return ("<AsterPortBinding(switch_ip %s,l2_vni %s,"
                "vlan_id %s, subnet_id %s, router_id %s, l3_vni %s)>" %
                (self.switch_ip, self.l2_vni, self.vlan_id,
                 self.subnet_id, self.router_id, self.l3_vni))

    def __hash__(self):
        return hash(self.__repr__())

    def __eq__(self, other):
        """Compare only the binding, without the id key."""
        return (
            self.switch_ip == other.switch_ip and
            self.l2_vni == other.l2_vni and
            self.vlan_id == other.vlan_id
        )


class AsterCxHostMapping(model_base.BASEV2):
    """Aster CX Host to interface Mappings."""

    __tablename__ = 'aster_ml2_cx_host_interface_mappings'

    id = sa.Column(sa.Integer, primary_key=True, autoincrement=True)
    switch_ip = sa.Column(sa.String(255), nullable=False)
    switch_interfaces = sa.Column(sa.String(255), nullable=False)
    host_id = sa.Column(sa.String(255), nullable=False)
    physical_network = sa.Column(sa.String(255), nullable=False)
