from setuptools import setup, find_packages

_entry_points = {
    'neutron.db.alembic_migrations': [
        'networking_afc = networking_afc.db.migration:alembic_migrations'
    ],
    'neutron.ml2.mechanism_drivers': [
        'asterswitch = networking_afc.ml2_drivers.mech_aster.mech_driver.'
        'mech_aster:AsterCXSwitchMechanismDriver'
    ],
    'neutron.ml2.type_drivers': [
        'aster_vxlan = networking_afc.ml2_drivers.mech_aster.mech_driver.'
        'type_aster_vxlan:AsterCXVxlanTypeDriver',
        'aster_ext_net = networking_afc.ml2_drivers.mech_aster.mech_driver.'
        'type_aster_ext_net:AsterExtNetTypeDriver'
    ],
    'neutron.service_plugins': [
        'afc_l3 = networking_afc.l3_router.l3_afc:AsterL3ServicePlugin'
    ]
}

_package_data = {
        '': ['*.mako'],
}

setup(
    name="networking_afc",
    url="None",
    zip_safe=False,
    version="0.9",
    package_data=_package_data,
    packages=find_packages(),
    entry_points=_entry_points
)
