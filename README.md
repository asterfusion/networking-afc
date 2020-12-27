# Networking-afc Plugin for OpenStack Neutron

## Overview
Based on the OpenStack Networking ML2 plugin which provides an extensible architecture that supports multiple independent drivers to be used to configure different network devices from different vendors, Networking-afc Neutron Plugin offers infrastructure services for Asterfusion’s  switches and configure the underlying physical network with cooperation of Asteris Fabric SDN Controller (AFC for short). Networking-afc Neutron Plugin can efficiently implement distributed Overlay network and offload Layer 2 and layer 3 vxlan network functions such as packaging or unpackaging the vxlan onto physical switches. It helps to allow VM traffic for North-South & East-West while to improve the forwarding performance and reliability of cloud networks and reduce the CPU overhead consumed by computing nodes on the network.

Note. As a unified visual platform of SDN controller, AFC monitors and manages business maintenance in hardware devices through network topology. In addition, it can rapidly deploy or uninstall the networking-afc plugin facing OpenStack and deliver service configurations to Asterfusion programmable switches, which meet business requirements of overlay network and free up the capability of the Asterfusion basic network. AFC uses standard network protocols to manage network resources and docks with Asterfusion programmable switches through the standardized southbound interface to realizes the collaboration between computing and network resources. It can not only undertake the work of business presentation/collaboration independently, but also supports to connect with the cloud platform like openstack, vcenter, etc. through the open capability of The Northbound Restful interface.

<!--
    ### Architectural
    Networking-afc Neutron Plugin 

    <img src="https://github.com/songminyue/hello-world/blob/main/NETWORKING-AFC.png" width="50%" >
-->
### Supported components:

|components||
|:-----------------:|:---------------------------:|
|Openstack version   |Rocky                        |
|Asterfusion switches|CX306P, CX564P|
|Linux Distribution  |Centos7.6|
|Type driver         |Aster_vxlan, Aster_ext_net|
|Mechanism Driver    |AsterCXSwitchMechanismDriver|

## Prerequsites

* Openstack Rocky is required
* Ensure that lldp is installed and started on all compute nodes
* Ensure that pip is enabled on controller

## Configuration

It is recommended to use AFC for its advantages that AFC can rapidly deploy or uninstall the networking-afc plugin facing OpenStack，directly deliver service configurations to Asterfusion programmable switches and intuitively present the network resource status of cloud network.
More details of Asterfusion programmable switches and obtainment of AFC can refer to https://asterfusion.com/index.php/zh/product-zn/afc

Meanwhile, networking-afc plugin also supports the manually installation.

0.  To be convenient for uninstallation of plugin, first we need to backup the configuration files:
```
Controller node:
/etc/neutron/neutron.conf
/etc/neutron/plugins/ml2/ml2_conf.ini
/usr/share/openstack-dashboard/openstack_dashboard/local/local_settings.py
Compute node:
/etc/neutron/plugins/ml2/openvswitch_agent.ini
```
Then we can configurate the controller and compute nodes for networking-afc plugin deployment step by step as below.

1.  Add ML2 plugin Configuration in /etc/neutron/plugins/ml2/ml2_conf.ini on controller node

1). Add type_driver and mechanism_drivers of asterfusion
```    
[ml2]
    mechanism_drivers=asterswitch,openvswitch
    type_drivers=vxlan,flat,vlan,local,aster_vxlan, aster_ext_net
    tenant_network_types=aster_vxlan
```
2). Give the configurations for each network type. Physnet-x/Physnet-y is the provider number of asterfusion’s switch and vlan range for networks can be same within two switches but have to be in the range 2-4094. For aster_vxlan type, vni_range and l3_vni_range are setted for Layer 2 and Layer3 network respectively, and for aster_ext_net type, external network name should be given. 
```
[ml2_type_vlan]
    network_vlan_ranges=physnet-x:30:50,physnet-y:30:50
    
[ml2_type_aster_vxlan]
    vni_ranges=10000:10010
    l3_vni_ranges=10:20
    mcast_ranges=224.0.0.1:224.0.0.3
    # Border leaf l2 vni ranages
    l2_vni_ranges=100:110
    
[ml2_type_aster_ext_net]
    aster_ext_net_networks=fw1,fw2
```
3). For distributed Overlay network, it needs to configure according to the role of the switch. Physical_network_ports_mapping shows the border leaf’s interface(X27-X29) connected with cooresponding External network which is given by aster_ext_net type. Physnet is given to distinguish between different leaf switches and host_ports_mapping is the maping of node’s hostname and interfaces of cx connected with node
```
[ml2_border_leaf:192.168.x.x]
    # Border leaf connect FW vlan ranges and interface_names
    vlan_ranges=30:50
    physical_network_ports_mapping=fw1:[X28],fw2:[X27, X29]
 
[ml2_mech_aster_cx:192.168.x.x]
    physnet=physnet-x
    host_ports_mapping=controller:[X25],computer1:[X29],computer2:[X37]
    
[ml2_mech_aster_cx:192.168.x.x]
    physnet=physnet-y
    host_ports_mapping=controller:[X25],computer1:[X29],computer2:[X37]
```
4). Configurate the parameters of AFC to support the Northbound REST APIs interfacing with networking-afc driver
```
[aster_authtoken]
    username=aster
    password=123456
    auth_uri=http://192.168.4.169:9696
    is_send_afc=True
```
2.  Add plugin configuration in /etc/neutron/neutron.conf on controller
```
service_plugins=afc_l3
```

3.  In order to make supported_provider_types on the dashboard visible, it is necessary to add“aster_vxlan”, “aster_ext_net” in /usr/share/openstack-dashboard/openstack_dashboard/local/local_settings.py on the controller node where horizon is deployed
```
OPENSTACK_NEUTRON_NETWORK = {
  …… ……
  'supported_provider_types': ['local', 'flat', 'vlan', 'gre', 'vxlan', 'geneve', "aster_vxlan", "aster_ext_net"],
        'extra_provider_types': {
            "aster_ext_net": {
              'display_name': _('AsterExtNet'),
              'require_physical_network': True,
              'require_segmentation_id': False
            },
            "aster_vxlan": {
              'display_name': _('AsterVXLAN'),
               'require_physical_network': False,
               'require_segmentation_id': True,
            },
             …… ……
         },

  'segmentation_id_range': {
            'vlan': (1, 4094),
            'gre': (1, (2 ** 32) - 1),
            'vxlan': (1, (2 ** 24) - 1),
            'geneve': (1, (2 ** 24) - 1),
            'aster_vxlan': (1, (2 ** 24) - 1),
        }
}
        
```
4.  To reach the external virtual machine, it’s necessary to add a ovs bridge on compute node and bond the switch’s ports (e.g. eth1) connected with the server node to the bridge. The bridge name is associated with provider number of switch.
```
ovs-vsctl add-br br-leaf-102
ovs-vsctl add-port br-leaf-102 eth1
```

5.  Then the mapping relationship of ovs bridge and switch can be established. All compute nodes installed neutron-openvswitch-agent need to be configured in /etc/neutron/plugins/ml2/openvswitch_agent.ini respectively.
```
bridge_mappings=physnet_4_102:br-leaf-102
systemctl restart neutron-openvswitch-agent
```
## Install 
1.  Install networking-afc plugin (get from) and update database
```
python setup.py install
neutron-db-manage --subproject networking_afc upgrade head
```

2.  Restart services on controller
```
systemctl restart httpd 
systemctl restart neutron-server
```

##  Uninstall
1.  Uninstall neiworking-afc plugin.
```
pip uninstall networking_afc
```
2.  Overwrite the configuration file with the backup files in step.0 and restart the services respectively.
```
# controller node
systemctl restart httpd 
systemctl restart neutron-server
# compute node
systemctl restart neutron-openvswitch-agent
```


