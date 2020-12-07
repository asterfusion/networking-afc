import json

from oslo_log import log
from oslo_config import cfg

from networking_afc.common import config as conf
from networking_afc.common.neutronclient.v2_0 import client
from networking_afc.ml2_drivers.mech_aster.mech_driver import exceptions as exc


CONF = cfg.CONF
LOG = log.getLogger(__name__)


class AfcRestClient(object):

    def __init__(self):
        self.is_send_afc = conf.cfg.CONF.aster_authtoken.is_send_afc

    @staticmethod
    def get_neutron_client():
        LOG.info("Get AFC Get configuration information is >>> %s ", conf.cfg.CONF.aster_authtoken.auth_uri)
        neutron = client.Client(
            auth_strategy="noauth",
            endpoint_url=conf.cfg.CONF.aster_authtoken.auth_uri
        )
        return neutron
        # username = "admin"
        # password = "stack123"
        # project_name = "demo"
        # project_domain_id = "default"
        # user_domain_id = "default"
        # auth_url = "http://127.0.0.1/identity"
        # auth = identity.Password(auth_url=auth_url,
        #                          username=username,
        #                          password=password,
        #                          project_name=project_name,
        #                          project_domain_id=project_domain_id,
        #                          user_domain_id=user_domain_id)
        # sess = session.Session(auth=auth)
        # neutron = client.Client(session=sess)

    def get_switch_id_by_ip(self, switch_ip=None):
        neutron_client = self.get_neutron_client()
        devices = neutron_client.list_devices(ip_address=switch_ip).get("devices")
        if not devices:
            # LOG.error("Switch not found, params is >>>> %s", config_params)
            raise exc.NoFoundPhysicalSwitch(
                switch_ip=switch_ip
            )
        return neutron_client, devices[0].get("id")

    def send_config_to_afc(self, config_params):
        """
           Send create network request to AFC
        :param config_params: request params format:
                {
                   "switch_ip": "192.168.4.105",
                   "interfaces": [
                       "X37"
                   ],
                   "vlan_id": 105,
                   "vni": 10008
                }
        :return:
        """
        LOG.info("Neutron create_network config_params >>>>>>>>\n %s \n ",
                 json.dumps(config_params, indent=3))

        switch_ip = config_params.pop("switch_ip", "")
        if not self.is_send_afc:
            LOG.info("A request to create a network was not sent to the AFC !!!")
            return
        # Send create network request to AFC
        neutron, switch_id = self.get_switch_id_by_ip(switch_ip=switch_ip)
        ret = neutron.neutron_create_network(switch_id, body=config_params)
        LOG.info(">>>>>>>>>>>>>>>>>>>>>>> neutron_create_network ret >>> %s ", ret)

    def delete_config_from_afc(self, delete_params):
        """
           Send delete network request to AFC
        :param delete_params: request params format:
                {
                   "switch_ip": "192.168.4.105",
                   "interfaces": [
                       "X37"
                   ],
                   "vlan_id": 105,
                   "vni": 10008
                }
        :return:
        """
        LOG.info("Neutron delete_network delete_params >>>>>>>>\n %s \n ",
                 json.dumps(delete_params, indent=3))

        switch_ip = delete_params.pop("switch_ip", "")
        if not self.is_send_afc:
            LOG.info("A request to delete a network was not sent to the AFC !!!")
            return
        # Send delete network request to AFC
        neutron, switch_id = self.get_switch_id_by_ip(switch_ip=switch_ip)
        ret = neutron.neutron_delete_network(switch_id, body=delete_params)
        LOG.info(">>>>>>>>>>>>>>>>>>>>>>> neutron_delete_network ret >>> %s ", ret)


    def create_or_update_vrf_on_physical_switch(self, request_params=None):
        """
           Send create router request to AFC
           1. router_vni if not exist need create vrf
           2. Update vlan interface to the vrf
           3. Config gw/netmask to the vlan interface
        :param request_params: request params format:
                {
                   "switch_ip": "192.168.4.105",
                   "router_vni": 10000,
                   "l2_vni": 888,
                   "vlan_id": 105,
                   "gw_ip": "10.10.10.1/24"
                }
        :return:
        """
        LOG.info("Neutron create_router config_params >>>>>>>>\n %s \n ",
                 json.dumps(request_params, indent=3))

        switch_ip = request_params.pop("switch_ip", "")
        if not self.is_send_afc:
            LOG.info("A request to create a router was not sent to the AFC !!!")
            return
        # Send create router request to AFC
        neutron, switch_id = self.get_switch_id_by_ip(switch_ip=switch_ip)
        ret = neutron.neutron_create_router(switch_id, body=request_params)
        LOG.info(">>>>>>>>>>>>>>>>>>>>>>> neutron_create_router ret >>> %s ", ret)

    def delete_or_update_vrf_on_physical_switch(self, request_params=None):
        """
           Send delete router request to AFC
           1. Remove vlan interface from the vrf
           2. If vrf have more vlan interfaces not del
        :param request_params: request params format:
                {
                   "switch_ip": "192.168.4.105",
                   "router_vni": 10000,
                   "l2_vni": 777,
                   "vlan_id": 105,
                   "gw_ip": "10.10.10.1/24"
                }
        :return:
        """
        LOG.info("Neutron delete_router config_params >>>>>>>>\n %s \n ",
                 json.dumps(request_params, indent=3))

        switch_ip = request_params.pop("switch_ip", "")
        if not self.is_send_afc:
            LOG.info("A request to delete a router was not sent to the AFC !!!")
            return
        # Send delete router request to AFC
        neutron, switch_id = self.get_switch_id_by_ip(switch_ip=switch_ip)
        ret = neutron.neutron_delete_router(switch_id, body=request_params)
        LOG.info(">>>>>>>>>>>>>>>>>>>>>>> neutron_delete_router ret >>> %s ", ret)
