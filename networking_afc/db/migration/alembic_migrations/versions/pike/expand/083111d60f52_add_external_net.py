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

"""add_external_net

Revision ID: 083111d60f52
Revises: a4fd5f0f33a5
Create Date: 2020-06-17 16:45:14.763018

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '083111d60f52'
down_revision = 'a4fd5f0f33a5'


def upgrade():
    op.create_table(
        'ml2_leaf_l2_vni_allocations',
        sa.Column('l2_vni',
                  sa.Integer(),
                  autoincrement=False,
                  nullable=False),
        sa.Column('router_id',
                  sa.String(36),
                  nullable=False,
                  server_default=''),
        sa.PrimaryKeyConstraint('l2_vni')
    )
    op.create_table(
        'ml2_leaf_vlan_allocations',
        sa.Column('switch_ip',
                  sa.String(36),
                  nullable=False,
                  server_default=''),
        sa.Column('vlan_id',
                  sa.Integer(),
                  nullable=False,
                  default=0),
        sa.Column('router_id',
                  sa.String(36),
                  nullable=False,
                  server_default='')
    )
