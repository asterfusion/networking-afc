# Copyright 2020 Red Hat, Inc.
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
#

"""add subnet_id

Revision ID: a4fd5f0f33a5
Revises: e229b8aad9f2
Create Date: 2020-05-31 15:42:50.804729

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a4fd5f0f33a5'
down_revision = 'e229b8aad9f2'

def upgrade():
    # Add l3_vni allocation table
    op.create_table(
        'ml2_aster_l3_vni_allocations',
        sa.Column('l3_vni', sa.Integer(), autoincrement=False, nullable=False),
        sa.Column('router_id', sa.String(36), nullable=False, server_default=''),
        sa.PrimaryKeyConstraint('l3_vni')
    )
    # Add cx switch and host interface_mappings table
    op.create_table(
        'aster_ml2_cx_host_interface_mappings',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('switch_ip', sa.String(255), nullable=False, server_default=''),
        sa.Column('switch_interfaces', sa.String(255), nullable=False, server_default=''),
        sa.Column('host_id', sa.String(255), nullable=False, server_default=''),
        sa.Column('physical_network', sa.String(255), nullable=False, server_default=''),
        sa.PrimaryKeyConstraint('id')
    )
    # Add l2_vni filed
    op.add_column(
        'aster_ml2_port_bindings',
        sa.Column('l2_vni', sa.Integer(), nullable=False, default=0)
    )
    # Add is_config_l2 filed
    op.add_column(
        'aster_ml2_port_bindings',
        sa.Column('is_config_l2', sa.Boolean(), nullable=False,
                  server_default=sa.sql.false())
    )
    # Add router_id filed
    op.add_column(
        'aster_ml2_port_bindings',
        sa.Column('subnet_id', sa.String(36), nullable=False, default='')
    )
    # Add router_id filed
    op.add_column(
        'aster_ml2_port_bindings',
        sa.Column('router_id', sa.String(36), nullable=False, default='')
    )
    # Add l3_vni filed
    op.add_column(
        'aster_ml2_port_bindings',
        sa.Column('l3_vni', sa.Integer(), nullable=False, default=0)
    )

    op.drop_column('aster_ml2_port_bindings', 'host_id')
    op.drop_column('aster_ml2_port_bindings', 'vxlan_vni')
    op.drop_column('aster_ml2_port_bindings', 'port_id')
