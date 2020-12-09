

1.  Configure it on the controller
 
     
    vim /etc/neutron/plugins/ml2/ml2_conf.ini
    
    [ml2]
    
    mechanism_drivers=asterswitch,openvswitch
    
    #
    # From neutron.ml2
    #
    
    # List of network type driver entrypoints to be loaded from the
    # neutron.ml2.type_drivers namespace. (list value)
    #type_drivers = local,flat,vlan,gre,vxlan,geneve
    type_drivers=vxlan,flat,vlan,local,aster_vxlan
    tenant_network_types=aster_vxlan
    
    [ml2_type_vlan]
    
    #
    # From neutron.ml2
    #
    
    # List of <physical_network>:<vlan_min>:<vlan_max> or <physical_network>
    # specifying physical_network names usable for VLAN provider and tenant
    # networks, as well as ranges of VLAN tags on each available for allocation to
    # tenant networks. (list value)
    #network_vlan_ranges =
    network_vlan_ranges=physnet_4_102:30:50,physnet_4_105:100:200
    
    [ml2_type_aster_vxlan]
    vni_ranges=10000:10010
    l3_vni_ranges=10:20
    mcast_ranges=224.0.0.1:224.0.0.3
    
    [ml2_mech_aster_cx:192.168.4.102]
    physnet=physnet_4_102
    host_ports_mapping=controller:[X25],computer1:[X29],computer2:[X37]
    
    [ml2_mech_aster_cx:192.168.4.105]
    physnet=physnet_4_105
    host_ports_mapping=controller:[X25],computer1:[X29],computer2:[X37]
    
    [aster_authtoken]
    username=aster
    password=123456
    auth_uri=http://192.168.4.169:9696
    
    vim /etc/neutron/neutron.conf
    
    service_plugins=afc_l3

2.  Configure on the node where horizon is deployed

     
    vim /usr/share/openstack-dashboard/openstack_dashboard/local/local_settings.py
    
    OPENSTACK_NEUTRON_NETWORK = {
        'enable_distributed_router': False,
        'enable_firewall': False,
        'enable_ha_router': False,
        'enable_lb': False,
        'enable_quotas': True,
        'enable_security_group': True,
        'enable_vpn': False,
        'profile_support': None,
        'supported_provider_types': ['local', 'flat', 'vlan', 'gre', 'vxlan', 'geneve', "aster_vxlan"],
        'extra_provider_types': {
            "aster_vxlan": {
              'display_name': _('AsterVXLAN'),
               'require_physical_network': False,
               'require_segmentation_id': True,
             },
            'local': {
                'display_name': _('Local'),
                'require_physical_network': False,
                'require_segmentation_id': False,
            },
            'flat': {
                'display_name': _('Flat'),
                'require_physical_network': True,
                'require_segmentation_id': False,
            },
            'vlan': {
                'display_name': _('VLAN'),
                'require_physical_network': True,
                'require_segmentation_id': True,
            },
            'gre': {
                'display_name': _('GRE'),
                'require_physical_network': False,
                'require_segmentation_id': True,
            },
            'vxlan': {
                'display_name': _('VXLAN'),
                'require_physical_network': False,
                'require_segmentation_id': True,
            },
            'geneve': {
                'display_name': _('Geneve'),
                'require_physical_network': False,
                'require_segmentation_id': True,
            },
            'midonet': {
                'display_name': _('MidoNet'),
                'require_physical_network': False,
                'require_segmentation_id': False,
            },
            'uplink': {
                'display_name': _('MidoNet Uplink'),
                'require_physical_network': False,
                'require_segmentation_id': False,
            }
        },
        'segmentation_id_range': {
            'vlan': (1, 4094),
            'gre': (1, (2 ** 32) - 1),
            'vxlan': (1, (2 ** 24) - 1),
            'geneve': (1, (2 ** 24) - 1),
            'aster_vxlan': (1, (2 ** 24) - 1),
        }
    }

3.  All nodes with neutron-openvswitch-agent installed need to be configured
  
    
    vim /etc/neutron/plugins/ml2/openvswitch_agent.ini
    
    comment out the tunnel_types option
          
    bridge_mappings=physnet_4_102:br_4_102
   
4.  Upgrade DB commands 


    neutron-db-manage --subproject networking_afc upgrade head

5.  Clean DB commands


    drop table ml2_aster_vxlan_allocations;
    drop table ml2_aster_l3_vni_allocations;
    drop table aster_ml2_port_bindings;
    drop table aster_switch_bindings;
    delete from aster_alembic_version;
