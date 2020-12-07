# Copyright 2017 Red Hat, Inc.
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

"""add acccount tables

Revision ID: e229b8aad9f2
Revises: ac094507b7f4
Create Date: 2017-04-28 11:41:47.487584

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e229b8aad9f2'
down_revision = 'ac094507b7f4'


def upgrade():
    op.create_table(
        'ml2_aster_vxlan_allocations',
        sa.Column('vxlan_vni', sa.Integer(), autoincrement=False, nullable=False),
        sa.Column('allocated', sa.Boolean(), nullable=False,
                  server_default=sa.sql.false(), index=True),
        sa.PrimaryKeyConstraint('vxlan_vni'))

    op.create_table(
        'aster_ml2_port_bindings',
        sa.Column('binding_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('host_id', sa.String(36), nullable=False, server_default=''),
        sa.Column('switch_ip', sa.String(36), nullable=False, server_default=''),
        sa.Column('vxlan_vni', sa.String(36), nullable=False, server_default=''),
        sa.Column('vlan_id', sa.String(36), nullable=False, server_default=''),
        sa.Column('port_id', sa.String(36), nullable=False, server_default=''),
        sa.PrimaryKeyConstraint('binding_id'))

    op.create_table(
        'aster_switch_bindings',
        sa.Column('order_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('switch_ip', sa.String(36), nullable=False, server_default=''),
        sa.Column('vxlan_vni', sa.String(36), nullable=False, server_default=''),
        sa.Column('vlan_id', sa.String(36), nullable=False, server_default=''),
        sa.Column('resource_type', sa.String(36), nullable=False),
        sa.Column('resource_id', sa.String(36), nullable=False),
        sa.PrimaryKeyConstraint('order_id'))
