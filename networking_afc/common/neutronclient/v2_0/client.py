# Copyright 2012 OpenStack Foundation.
# Copyright 2015 Hewlett-Packard Development Company, L.P.
# All Rights Reserved
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
import re
import os
import time
import json
import copy
import inspect
import logging
import itertools
import requests
import threading
import debtcollector.renames
import six.moves.urllib.parse as urlparse

from six import string_types

from keystoneauth1 import exceptions as ksa_exc
from neutronclient._i18n import _
from neutronclient import client
from neutronclient.common import utils
from neutronclient.common import serializer
from neutronclient.common import exceptions
from neutronclient.common import extension as client_extension

# from fcommon.crypto import encrypt

_logger = logging.getLogger(__name__)

HEX_ELEM = '[0-9A-Fa-f]'
UUID_PATTERN = '-'.join([HEX_ELEM + '{8}', HEX_ELEM + '{4}',
                         HEX_ELEM + '{4}', HEX_ELEM + '{4}',
                         HEX_ELEM + '{12}'])


def exception_handler_v20(status_code, error_content):
    """Exception handler for API v2.0 client.

    This routine generates the appropriate Neutron exception according to
    the contents of the response body.

    :param status_code: HTTP error status code
    :param error_content: deserialized body of error response
    """
    error_dict = None
    error_info = {}
    request_ids = error_content.request_ids
    if isinstance(error_content, dict):
        error_dict = error_content.get('NeutronError')
    # Find real error type
    client_exc = None
    if error_dict:
        # If Neutron key is found, it will definitely contain
        # a 'message' and 'type' keys?
        try:
            error_info = error_dict['error_info']
        except:
            error_info.update({"error_code":status_code})
        try:
            error_type = error_dict['type']
            error_message = error_dict['message']
            if error_dict['detail']:
                error_message += "\n" + error_dict['detail']
            # If corresponding exception is defined, use it.
            client_exc = getattr(exceptions, '%sClient' % error_type, None)
        except Exception:
            error_message = "%s" % error_dict
    else:
        error_message = None
        if isinstance(error_content, dict):
            error_message = error_content.get('message')
        if not error_message:
            # If we end up here the exception was not a neutron error
            error_message = "%s-%s" % (status_code, error_content)

    # If an exception corresponding to the error type is not found,
    # look up per status-code client exception.
    if not client_exc:
        client_exc = exceptions.HTTP_EXCEPTION_MAP.get(status_code)
    # If there is no exception per status-code,
    # Use NeutronClientException as fallback.
    if not client_exc:
        client_exc = exceptions.NeutronClientException
    raise client_exc(error_info=error_info,
                     message=error_message,
                     status_code=status_code,
                     request_ids=request_ids)


class _RequestIdMixin(object):
    """Wrapper class to expose x-openstack-request-id to the caller."""
    def _request_ids_setup(self):
        self._request_ids = []

    @property
    def request_ids(self):
        return self._request_ids

    def _append_request_ids(self, resp):
        """Add request_ids as an attribute to the object

        :param resp: Response object or list of Response objects
        """
        if isinstance(resp, list):
            # Add list of request_ids if response is of type list.
            for resp_obj in resp:
                self._append_request_id(resp_obj)
        elif resp is not None:
            # Add request_ids if response contains single object.
            self._append_request_id(resp)

    def _append_request_id(self, resp):
        if isinstance(resp, requests.Response):
            # Extract 'x-openstack-request-id' from headers if
            # response is a Response object.
            request_id = resp.headers.get('x-openstack-request-id')
        else:
            # If resp is of type string.
            request_id = resp
        if request_id:
            self._request_ids.append(request_id)


class _DictWithMeta(dict, _RequestIdMixin):
    def __init__(self, values, resp):
        super(_DictWithMeta, self).__init__(values)
        self._request_ids_setup()
        self._append_request_ids(resp)


class _TupleWithMeta(tuple, _RequestIdMixin):
    def __new__(cls, values, resp):
        return super(_TupleWithMeta, cls).__new__(cls, values)

    def __init__(self, values, resp):
        self._request_ids_setup()
        self._append_request_ids(resp)


class _StrWithMeta(str, _RequestIdMixin):
    def __new__(cls, value, resp):
        return super(_StrWithMeta, cls).__new__(cls, value)

    def __init__(self, values, resp):
        self._request_ids_setup()
        self._append_request_ids(resp)


class _GeneratorWithMeta(_RequestIdMixin):
    def __init__(self, paginate_func, collection, path, **params):
        self.paginate_func = paginate_func
        self.collection = collection
        self.path = path
        self.params = params
        self.generator = None
        self._request_ids_setup()

    def _paginate(self):
        for r in self.paginate_func(
                self.collection, self.path, **self.params):
            yield r, r.request_ids

    def __iter__(self):
        return self

    # Python 3 compatibility
    def __next__(self):
        return self.next()

    def next(self):
        if not self.generator:
            self.generator = self._paginate()

        try:
            obj, req_id = next(self.generator)
            self._append_request_ids(req_id)
        except StopIteration:
            raise StopIteration()

        return obj


class ClientBase(object):
    """Client for the OpenStack Neutron v2.0 API.

    :param string username: Username for authentication. (optional)
    :param string user_id: User ID for authentication. (optional)
    :param string password: Password for authentication. (optional)
    :param string token: Token for authentication. (optional)
    :param string tenant_name: DEPRECATED! Use project_name instead.
    :param string project_name: Project name. (optional)
    :param string tenant_id: DEPRECATED! Use project_id instead.
    :param string project_id: Project id. (optional)
    :param string auth_strategy: 'keystone' by default, 'noauth' for no
                                 authentication against keystone. (optional)
    :param string auth_url: Keystone service endpoint for authorization.
    :param string service_type: Network service type to pull from the
                                keystone catalog (e.g. 'network') (optional)
    :param string endpoint_type: Network service endpoint type to pull from the
                                 keystone catalog (e.g. 'publicURL',
                                 'internalURL', or 'adminURL') (optional)
    :param string region_name: Name of a region to select when choosing an
                               endpoint from the service catalog.
    :param string endpoint_url: A user-supplied endpoint URL for the neutron
                            service.  Lazy-authentication is possible for API
                            service calls if endpoint is set at
                            instantiation.(optional)
    :param integer timeout: Allows customization of the timeout for client
                            http requests. (optional)
    :param bool insecure: SSL certificate validation. (optional)
    :param bool log_credentials: Allow for logging of passwords or not.
                                 Defaults to False. (optional)
    :param string ca_cert: SSL CA bundle file to use. (optional)
    :param integer retries: How many times idempotent (GET, PUT, DELETE)
                            requests to Neutron server should be retried if
                            they fail (default: 0).
    :param bool raise_errors: If True then exceptions caused by connection
                              failure are propagated to the caller.
                              (default: True)
    :param session: Keystone client auth session to use. (optional)
    :param auth: Keystone auth plugin to use. (optional)

    Example::

        from neutronclient.v2_0 import client
        neutron = client.Client(username=USER,
                                password=PASS,
                                project_name=PROJECT_NAME,
                                auth_url=KEYSTONE_URL)

        nets = neutron.list_networks()
        ...
    """

    # API has no way to report plurals, so we have to hard code them
    # This variable should be overridden by a child class.
    EXTED_PLURALS = {}

    @debtcollector.renames.renamed_kwarg(
        'tenant_id', 'project_id', replace=True)
    def __init__(self, **kwargs):
        """Initialize a new client for the Neutron v2.0 API."""
        super(ClientBase, self).__init__()
        self.retries = kwargs.pop('retries', 0)
        self.raise_errors = kwargs.pop('raise_errors', True)
        self.httpclient = client.construct_http_client(**kwargs)
        self.version = '2.0'
        self.action_prefix = "/v%s" % (self.version)
        self.retry_interval = 1
        self.lock = threading.Lock()

    def _handle_fault_response(self, status_code, response_body, resp):
        # Create exception with HTTP status code and message
        _logger.debug("Error message: %s", response_body)
        # Add deserialized error message to exception arguments
        try:
            des_error_body = self.deserialize(response_body, status_code)
        except Exception:
            # If unable to deserialized body it is probably not a
            # Neutron error
            des_error_body = {'message': response_body}
        error_body = self._convert_into_with_meta(des_error_body, resp)
        # Raise the appropriate exception
        exception_handler_v20(status_code, error_body)

    def do_request(self, method, action, body=None, headers=None, params=None):
        # Add format and project_id
        action = self.action_prefix + action
        if isinstance(params, dict) and params:
            params = utils.safe_encode_dict(params)
            action += '?' + urlparse.urlencode(params, doseq=1)

        if body:
            body = self.serialize(body)

        resp, replybody = self.httpclient.do_request(action, method, body=body)

        status_code = resp.status_code
        if status_code in (requests.codes.ok,
                           requests.codes.created,
                           requests.codes.accepted,
                           requests.codes.no_content):
            data = self.deserialize(replybody, status_code)
            return self._convert_into_with_meta(data, resp)
        else:
            if not replybody:
                replybody = resp.reason
            self._handle_fault_response(status_code, replybody, resp)

    def get_auth_info(self):
        return self.httpclient.get_auth_info()

    def serialize(self, data):
        """Serializes a dictionary into JSON.

        A dictionary with a single key can be passed and it can contain any
        structure.
        """
        if data is None:
            return None
        elif isinstance(data, dict):
            return serializer.Serializer().serialize(data)
        else:
            raise Exception(_("Unable to serialize object of type = '%s'") %
                            type(data))

    def deserialize(self, data, status_code):
        """Deserializes a JSON string into a dictionary."""
        if not data:
            return data
        return serializer.Serializer().deserialize(
            data)['body']

    def retry_request(self, method, action, body=None,
                      headers=None, params=None):
        """Call do_request with the default retry configuration.

        Only idempotent requests should retry failed connection attempts.
        :raises: ConnectionFailed if the maximum # of retries is exceeded
        """
        max_attempts = self.retries + 1
        for i in range(max_attempts):
            try:
                return self.do_request(method, action, body=body,
                                       headers=headers, params=params)
            except (exceptions.ConnectionFailed, ksa_exc.ConnectionError):
                # Exception has already been logged by do_request()
                if i < self.retries:
                    _logger.debug('Retrying connection to Neutron service')
                    time.sleep(self.retry_interval)
                elif self.raise_errors:
                    raise

        if self.retries:
            msg = (_("Failed to connect to Neutron server after %d attempts")
                   % max_attempts)
        else:
            msg = _("Failed to connect Neutron server")

        raise exceptions.ConnectionFailed(reason=msg)

    def delete(self, action, body=None, headers=None, params=None):
        return self.retry_request("DELETE", action, body=body,
                                  headers=headers, params=params)

    def get(self, action, body=None, headers=None, params=None):
        return self.retry_request("GET", action, body=body,
                                  headers=headers, params=params)

    def post(self, action, body=None, headers=None, params=None):
        # Do not retry POST requests to avoid the orphan objects problem.
        return self.do_request("POST", action, body=body,
                               headers=headers, params=params)

    def put(self, action, body=None, headers=None, params=None):
        return self.retry_request("PUT", action, body=body,
                                  headers=headers, params=params)

    def list(self, collection, path, retrieve_all=True, **params):
        if retrieve_all:
            res = []
            request_ids = []
            for r in self._pagination(collection, path, **params):
                res.extend(r[collection])
                request_ids.extend(r.request_ids)
            return _DictWithMeta({collection: res}, request_ids)
        else:
            return _GeneratorWithMeta(self._pagination, collection,
                                      path, **params)

    def _pagination(self, collection, path, **params):
        if params.get('page_reverse', False):
            linkrel = 'previous'
        else:
            linkrel = 'next'
        next = True
        while next:
            res = self.get(path, params=params)
            yield res
            next = False
            try:
                for link in res['%s_links' % collection]:
                    if link['rel'] == linkrel:
                        query_str = urlparse.urlparse(link['href']).query
                        params = urlparse.parse_qs(query_str)
                        next = True
                        break
            except KeyError:
                break

    def _convert_into_with_meta(self, item, resp):
        if item:
            if isinstance(item, dict):
                return _DictWithMeta(item, resp)
            elif isinstance(item, string_types):
                return _StrWithMeta(item, resp)
        else:
            return _TupleWithMeta((), resp)

    def get_resource_plural(self, resource):
        for k in self.EXTED_PLURALS:
            if self.EXTED_PLURALS[k] == resource:
                return k
        return resource + 's'

    def find_resource_by_id(self, resource, resource_id, cmd_resource=None,
                            parent_id=None, fields=None):
        if not cmd_resource:
            cmd_resource = resource
        cmd_resource_plural = self.get_resource_plural(cmd_resource)
        resource_plural = self.get_resource_plural(resource)
        # TODO(amotoki): Use show_%s instead of list_%s
        obj_lister = getattr(self, "list_%s" % cmd_resource_plural)
        # perform search by id only if we are passing a valid UUID
        match = re.match(UUID_PATTERN, resource_id)
        collection = resource_plural
        if match:
            params = {'id': resource_id}
            if fields:
                params['fields'] = fields
            if parent_id:
                data = obj_lister(parent_id, **params)
            else:
                data = obj_lister(**params)
            if data and data[collection]:
                return data[collection][0]
        not_found_message = (_("Unable to find %(resource)s with id "
                               "'%(id)s'") %
                             {'resource': resource, 'id': resource_id})
        # 404 is raised by exceptions.NotFound to simulate serverside behavior
        raise exceptions.NotFound(message=not_found_message)

    def _find_resource_by_name(self, resource, name, project_id=None,
                               cmd_resource=None, parent_id=None, fields=None):
        if not cmd_resource:
            cmd_resource = resource
        cmd_resource_plural = self.get_resource_plural(cmd_resource)
        resource_plural = self.get_resource_plural(resource)
        obj_lister = getattr(self, "list_%s" % cmd_resource_plural)
        params = {'name': name}
        if fields:
            params['fields'] = fields
        if project_id:
            params['tenant_id'] = project_id
        if parent_id:
            data = obj_lister(parent_id, **params)
        else:
            data = obj_lister(**params)
        collection = resource_plural
        info = data[collection]
        if len(info) > 1:
            raise exceptions.NeutronClientNoUniqueMatch(resource=resource,
                                                        name=name)
        elif len(info) == 0:
            not_found_message = (_("Unable to find %(resource)s with name "
                                   "'%(name)s'") %
                                 {'resource': resource, 'name': name})
            # 404 is raised by exceptions.NotFound
            # to simulate serverside behavior
            raise exceptions.NotFound(message=not_found_message)
        else:
            return info[0]

    def find_resource(self, resource, name_or_id, project_id=None,
                      cmd_resource=None, parent_id=None, fields=None):
        try:
            return self.find_resource_by_id(resource, name_or_id,
                                            cmd_resource, parent_id, fields)
        except exceptions.NotFound:
            try:
                return self._find_resource_by_name(
                    resource, name_or_id, project_id,
                    cmd_resource, parent_id, fields)
            except exceptions.NotFound:
                not_found_message = (_("Unable to find %(resource)s with name "
                                       "or id '%(name_or_id)s'") %
                                     {'resource': resource,
                                      'name_or_id': name_or_id})
                raise exceptions.NotFound(
                    message=not_found_message)


class Client(ClientBase):

    networks_path = "/networks"
    network_path = "/networks/%s"
    ports_path = "/ports"
    port_path = "/ports/%s"
    devices_path = "/devices"
    device_path = "/devices/%s"
    device_startup_proto_members_path = "/devices/%s/startup_proto_members"
    device_shutdown_proto_members_path = "/devices/%s/shutdown_proto_members"
    device_flush_proto_members_path = "/devices/%s/flush_proto_members"
    device_refresh_proto_members_path = "/devices/%s/refresh_proto_members"
    device_init_proto_path = "/devices/%s/init_proto"
    neutron_create_network_path = "/devices/%s/neutron_create_network"
    neutron_delete_network_path = "/devices/%s/neutron_delete_network"
    neutron_create_subnet_path = "/devices/%s/neutron_create_subnet"
    neutron_delete_subnet_path = "/devices/%s/neutron_delete_subnet"
    neutron_create_router_path = "/devices/%s/neutron_create_router"
    neutron_delete_router_path = "/devices/%s/neutron_delete_router"
    neutron_create_router_interface_path = "/devices/%s/neutron_create_router_interface"
    neutron_delete_router_interface_path = "/devices/%s/neutron_delete_router_interface"
    links_path = "/links"
    link_path = "/links/%s"
    topos_path = "/topos"
    topo_path = "/topos/%s"
    flows_path = "/flows"
    flow_path = "/flows/%s"
    groups_path = "/groups"
    group_path = "/groups/%s"
    interfaces_path = "/interfaces"
    interface_path = "/interfaces/%s"
    service_networks_path = "/service_networks"
    service_network_path = "/service_networks/%s"
    flow_mappings_path = "/flow_mappings"
    flow_mapping_path = "/flow_mappings/%s"
    flow_nexthops_path = "/flow_nexthops"
    flow_nexthop_path = "/flow_nexthops/%s"
    flow_paths_path = "/flow_paths"
    flow_path_path = "/flow_paths/%s"
    backup_configs_path = "/backup_configs"
    backup_config_path = "/backup_configs/%s"
    vnfc_instances_path = "/vnfc_instances"
    vnfc_instance_path = "/vnfc_instances/%s"
    vnfc_flows_path = "/vnfc_flows"
    vnfc_flow_path = "/vnfc_flows/%s"
    resources_path = "/resources"
    resource_path = "/resources/%s"
    device_ports_path = "/device_ports"
    device_port_path = "/device_ports/%s"
    flow_types_path = "/flow_types"
    flow_type_path = "/flow_types/%s"
    flow_profiles_path = "/flow_profiles"
    flow_profile_path = "/flow_profiles/%s"
    device_proto_members_path = "/device_proto_members"
    device_proto_member_path = "/device_proto_members/%s"
    subnets_path = "/subnets"
    subnet_path = "/subnets/%s"
    subnetpools_path = "/subnetpools"
    subnetpool_path = "/subnetpools/%s"
    address_scopes_path = "/address-scopes"
    address_scope_path = "/address-scopes/%s"
    quotas_path = "/quotas"
    quota_path = "/quotas/%s"
    quota_default_path = "/quotas/%s/default"
    extensions_path = "/extensions"
    extension_path = "/extensions/%s"
    routers_path = "/routers"
    router_path = "/routers/%s"
    floatingips_path = "/floatingips"
    floatingip_path = "/floatingips/%s"
    security_groups_path = "/security-groups"
    security_group_path = "/security-groups/%s"
    security_group_rules_path = "/security-group-rules"
    security_group_rule_path = "/security-group-rules/%s"

    sfc_flow_classifiers_path = "/sfc/flow_classifiers"
    sfc_flow_classifier_path = "/sfc/flow_classifiers/%s"
    sfc_port_pairs_path = "/sfc/port_pairs"
    sfc_port_pair_path = "/sfc/port_pairs/%s"
    sfc_port_pair_groups_path = "/sfc/port_pair_groups"
    sfc_port_pair_group_path = "/sfc/port_pair_groups/%s"
    sfc_port_chains_path = "/sfc/port_chains"
    sfc_port_chain_path = "/sfc/port_chains/%s"

    endpoint_groups_path = "/vpn/endpoint-groups"
    endpoint_group_path = "/vpn/endpoint-groups/%s"
    vpnservices_path = "/vpn/vpnservices"
    vpnservice_path = "/vpn/vpnservices/%s"
    ipsecpolicies_path = "/vpn/ipsecpolicies"
    ipsecpolicy_path = "/vpn/ipsecpolicies/%s"
    ikepolicies_path = "/vpn/ikepolicies"
    ikepolicy_path = "/vpn/ikepolicies/%s"
    ipsec_site_connections_path = "/vpn/ipsec-site-connections"
    ipsec_site_connection_path = "/vpn/ipsec-site-connections/%s"

    lbaas_loadbalancers_path = "/lbaas/loadbalancers"
    lbaas_loadbalancer_path = "/lbaas/loadbalancers/%s"
    lbaas_loadbalancer_path_stats = "/lbaas/loadbalancers/%s/stats"
    lbaas_loadbalancer_path_status = "/lbaas/loadbalancers/%s/statuses"
    lbaas_listeners_path = "/lbaas/listeners"
    lbaas_listener_path = "/lbaas/listeners/%s"
    lbaas_l7policies_path = "/lbaas/l7policies"
    lbaas_l7policy_path = lbaas_l7policies_path + "/%s"
    lbaas_l7rules_path = lbaas_l7policy_path + "/rules"
    lbaas_l7rule_path = lbaas_l7rules_path + "/%s"
    lbaas_pools_path = "/lbaas/pools"
    lbaas_pool_path = "/lbaas/pools/%s"
    lbaas_healthmonitors_path = "/lbaas/healthmonitors"
    lbaas_healthmonitor_path = "/lbaas/healthmonitors/%s"
    lbaas_members_path = lbaas_pool_path + "/members"
    lbaas_member_path = lbaas_pool_path + "/members/%s"

    vips_path = "/lb/vips"
    vip_path = "/lb/vips/%s"
    pools_path = "/lb/pools"
    pool_path = "/lb/pools/%s"
    pool_path_stats = "/lb/pools/%s/stats"
    members_path = "/lb/members"
    member_path = "/lb/members/%s"
    health_monitors_path = "/lb/health_monitors"
    health_monitor_path = "/lb/health_monitors/%s"
    associate_pool_health_monitors_path = "/lb/pools/%s/health_monitors"
    disassociate_pool_health_monitors_path = (
        "/lb/pools/%(pool)s/health_monitors/%(health_monitor)s")
    qos_queues_path = "/qos-queues"
    qos_queue_path = "/qos-queues/%s"
    agents_path = "/agents"
    agent_path = "/agents/%s"
    network_gateways_path = "/network-gateways"
    network_gateway_path = "/network-gateways/%s"
    gateway_devices_path = "/gateway-devices"
    gateway_device_path = "/gateway-devices/%s"
    service_providers_path = "/service-providers"
    metering_labels_path = "/metering/metering-labels"
    metering_label_path = "/metering/metering-labels/%s"
    metering_label_rules_path = "/metering/metering-label-rules"
    metering_label_rule_path = "/metering/metering-label-rules/%s"

    DHCP_NETS = '/dhcp-networks'
    DHCP_AGENTS = '/dhcp-agents'
    L3_ROUTERS = '/l3-routers'
    L3_AGENTS = '/l3-agents'
    LOADBALANCER_POOLS = '/loadbalancer-pools'
    LOADBALANCER_AGENT = '/loadbalancer-agent'
    AGENT_LOADBALANCERS = '/agent-loadbalancers'
    LOADBALANCER_HOSTING_AGENT = '/loadbalancer-hosting-agent'
    firewall_rules_path = "/fw/firewall_rules"
    firewall_rule_path = "/fw/firewall_rules/%s"
    firewall_policies_path = "/fw/firewall_policies"
    firewall_policy_path = "/fw/firewall_policies/%s"
    firewall_policy_insert_path = "/fw/firewall_policies/%s/insert_rule"
    firewall_policy_remove_path = "/fw/firewall_policies/%s/remove_rule"
    firewalls_path = "/fw/firewalls"
    firewall_path = "/fw/firewalls/%s"
    fwaas_firewall_groups_path = "/fwaas/firewall_groups"
    fwaas_firewall_group_path = "/fwaas/firewall_groups/%s"
    fwaas_firewall_rules_path = "/fwaas/firewall_rules"
    fwaas_firewall_rule_path = "/fwaas/firewall_rules/%s"
    fwaas_firewall_policies_path = "/fwaas/firewall_policies"
    fwaas_firewall_policy_path = "/fwaas/firewall_policies/%s"
    fwaas_firewall_policy_insert_path = \
        "/fwaas/firewall_policies/%s/insert_rule"
    fwaas_firewall_policy_remove_path = \
        "/fwaas/firewall_policies/%s/remove_rule"
    rbac_policies_path = "/rbac-policies"
    rbac_policy_path = "/rbac-policies/%s"
    qos_policies_path = "/qos/policies"
    qos_policy_path = "/qos/policies/%s"
    qos_bandwidth_limit_rules_path = "/qos/policies/%s/bandwidth_limit_rules"
    qos_bandwidth_limit_rule_path = "/qos/policies/%s/bandwidth_limit_rules/%s"
    qos_dscp_marking_rules_path = "/qos/policies/%s/dscp_marking_rules"
    qos_dscp_marking_rule_path = "/qos/policies/%s/dscp_marking_rules/%s"
    qos_minimum_bandwidth_rules_path = \
        "/qos/policies/%s/minimum_bandwidth_rules"
    qos_minimum_bandwidth_rule_path = \
        "/qos/policies/%s/minimum_bandwidth_rules/%s"
    qos_rule_types_path = "/qos/rule-types"
    qos_rule_type_path = "/qos/rule-types/%s"
    flavors_path = "/flavors"
    flavor_path = "/flavors/%s"
    service_profiles_path = "/service_profiles"
    service_profile_path = "/service_profiles/%s"
    flavor_profile_bindings_path = flavor_path + service_profiles_path
    flavor_profile_binding_path = flavor_path + service_profile_path
    availability_zones_path = "/availability_zones"
    auto_allocated_topology_path = "/auto-allocated-topology/%s"
    BGP_DRINSTANCES = "/bgp-drinstances"
    BGP_DRINSTANCE = "/bgp-drinstance/%s"
    BGP_DRAGENTS = "/bgp-dragents"
    BGP_DRAGENT = "/bgp-dragents/%s"
    bgp_speakers_path = "/bgp-speakers"
    bgp_speaker_path = "/bgp-speakers/%s"
    bgp_peers_path = "/bgp-peers"
    bgp_peer_path = "/bgp-peers/%s"
    network_ip_availabilities_path = '/network-ip-availabilities'
    network_ip_availability_path = '/network-ip-availabilities/%s'
    tags_path = "/%s/%s/tags"
    tag_path = "/%s/%s/tags/%s"
    trunks_path = "/trunks"
    trunk_path = "/trunks/%s"
    subports_path = "/trunks/%s/get_subports"
    subports_add_path = "/trunks/%s/add_subports"
    subports_remove_path = "/trunks/%s/remove_subports"
    bgpvpns_path = "/bgpvpn/bgpvpns"
    bgpvpn_path = "/bgpvpn/bgpvpns/%s"
    bgpvpn_network_associations_path =\
        "/bgpvpn/bgpvpns/%s/network_associations"
    bgpvpn_network_association_path =\
        "/bgpvpn/bgpvpns/%s/network_associations/%s"
    bgpvpn_router_associations_path = "/bgpvpn/bgpvpns/%s/router_associations"
    bgpvpn_router_association_path =\
        "/bgpvpn/bgpvpns/%s/router_associations/%s"

    # API has no way to report plurals, so we have to hard code them
    EXTED_PLURALS = {'routers': 'router',
                     'floatingips': 'floatingip',
                     'service_types': 'service_type',
                     'service_definitions': 'service_definition',
                     'security_groups': 'security_group',
                     'security_group_rules': 'security_group_rule',
                     'ipsecpolicies': 'ipsecpolicy',
                     'ikepolicies': 'ikepolicy',
                     'ipsec_site_connections': 'ipsec_site_connection',
                     'vpnservices': 'vpnservice',
                     'endpoint_groups': 'endpoint_group',
                     'vips': 'vip',
                     'pools': 'pool',
                     'members': 'member',
                     'health_monitors': 'health_monitor',
                     'quotas': 'quota',
                     'service_providers': 'service_provider',
                     'firewall_rules': 'firewall_rule',
                     'firewall_policies': 'firewall_policy',
                     'firewalls': 'firewall',
                     'fwaas_firewall_rules': 'fwaas_firewall_rule',
                     'fwaas_firewall_policies': 'fwaas_firewall_policy',
                     'fwaas_firewall_groups': 'fwaas_firewall_group',
                     'metering_labels': 'metering_label',
                     'metering_label_rules': 'metering_label_rule',
                     'loadbalancers': 'loadbalancer',
                     'listeners': 'listener',
                     'l7rules': 'l7rule',
                     'l7policies': 'l7policy',
                     'lbaas_l7policies': 'lbaas_l7policy',
                     'lbaas_pools': 'lbaas_pool',
                     'lbaas_healthmonitors': 'lbaas_healthmonitor',
                     'lbaas_members': 'lbaas_member',
                     'healthmonitors': 'healthmonitor',
                     'rbac_policies': 'rbac_policy',
                     'address_scopes': 'address_scope',
                     'qos_policies': 'qos_policy',
                     'policies': 'policy',
                     'bandwidth_limit_rules': 'bandwidth_limit_rule',
                     'minimum_bandwidth_rules': 'minimum_bandwidth_rule',
                     'rules': 'rule',
                     'dscp_marking_rules': 'dscp_marking_rule',
                     'rule_types': 'rule_type',
                     'flavors': 'flavor',
                     'bgp_speakers': 'bgp_speaker',
                     'bgp_peers': 'bgp_peer',
                     'network_ip_availabilities': 'network_ip_availability',
                     'trunks': 'trunk',
                     'bgpvpns': 'bgpvpn',
                     'network_associations': 'network_association',
                     'router_associations': 'router_association',
                     'flow_classifiers': 'flow_classifier',
                     'port_pairs': 'port_pair',
                     'port_pair_groups': 'port_pair_group',
                     'port_chains': 'port_chain',
                     }
    
    def __init__(self, **kwargs):
        """Initialize a new client for the Neutron v2.0 API."""
        self.temp_path = "/tmp"
        self.update_temp_file_info(str(kwargs.pop('temp_file_name', '')))
        super(Client, self).__init__(**kwargs)
        self._register_extensions(self.version)

    def update_temp_file_info(self, temp_file_name):
        self.temp_file_name = temp_file_name
        self.output_file = "%s/output_%s.json" % (self.temp_path,self.temp_file_name)
        self.statistics_file = "%s/statistics_%s.json" % (self.temp_path,self.temp_file_name)
        self.percent_file = "%s/percent_%s.txt" % (self.temp_path,self.temp_file_name)
        for i in [self.output_file, self.statistics_file, self.percent_file]:
            if not os.path.exists(i):
                output_obj = open(i,"w")
                output_obj.close()

    def list_ext(self, collection, path, retrieve_all, **_params):
        """Client extension hook for list."""
        return self.list(collection, path, retrieve_all, **_params)

    def show_ext(self, path, id, **_params):
        """Client extension hook for show."""
        return self.get(path % id, params=_params)

    def create_ext(self, path, body=None):
        """Client extension hook for create."""
        return self.post(path, body=body)

    def update_ext(self, path, id, body=None):
        """Client extension hook for update."""
        return self.put(path % id, body=body)

    def delete_ext(self, path, id):
        """Client extension hook for delete."""
        return self.delete(path % id)

    def get_quotas_tenant(self, **_params):
        """Fetch project info for following quota operation."""
        return self.get(self.quota_path % 'tenant', params=_params)

    def list_quotas(self, **_params):
        """Fetch all projects' quotas."""
        return self.get(self.quotas_path, params=_params)

    @debtcollector.renames.renamed_kwarg(
        'tenant_id', 'project_id', replace=True)
    def show_quota(self, project_id, **_params):
        """Fetch information of a certain project's quotas."""
        return self.get(self.quota_path % (project_id), params=_params)

    @debtcollector.renames.renamed_kwarg(
        'tenant_id', 'project_id', replace=True)
    def show_quota_default(self, project_id, **_params):
        """Fetch information of a certain project's default quotas."""
        return self.get(self.quota_default_path % (project_id), params=_params)

    @debtcollector.renames.renamed_kwarg(
        'tenant_id', 'project_id', replace=True)
    def update_quota(self, project_id, body=None):
        """Update a project's quotas."""
        return self.put(self.quota_path % (project_id), body=body)

    @debtcollector.renames.renamed_kwarg(
        'tenant_id', 'project_id', replace=True)
    def delete_quota(self, project_id):
        """Delete the specified project's quota values."""
        return self.delete(self.quota_path % (project_id))

    def list_extensions(self, **_params):
        """Fetch a list of all extensions on server side."""
        return self.get(self.extensions_path, params=_params)

    def show_extension(self, ext_alias, **_params):
        """Fetches information of a certain extension."""
        return self.get(self.extension_path % ext_alias, params=_params)

    def list_ports(self, retrieve_all=True, **_params):
        """Fetches a list of all ports for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('ports', self.ports_path, retrieve_all,
                         **_params)

    def show_port(self, port, **_params):
        """Fetches information of a certain port."""
        return self.get(self.port_path % (port), params=_params)

    def create_port(self, body=None):
        """Creates a new port."""
        return self.post(self.ports_path, body=body)

    def update_port(self, port, body=None):
        """Updates a port."""
        return self.put(self.port_path % (port), body=body)

    def delete_port(self, port):
        """Deletes the specified port."""
        return self.delete(self.port_path % (port))

    def list_devices(self, retrieve_all=True, **_params):
        """Fetches a list of all devices for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('devices', self.devices_path, retrieve_all,
                         **_params)

    def show_device(self, device, **_params):
        """Fetches information of a certain device."""
        return self.get(self.device_path % (device), params=_params)

    def create_device(self, body=None):
        """Creates a new device."""
        return self.post(self.devices_path, body=body)

    def update_device(self, device, body=None):
        """Updates a device."""
        return self.put(self.device_path % (device), body=body)

    def delete_device(self, device):
        """Deletes the specified device."""
        return self.delete(self.device_path % (device))

    def startup_proto_members(self, device, body=None):
        """Removes specified rule from firewall policy."""
        return self.put(self.device_startup_proto_members_path % (device),
                        body=body)

    def shutdown_proto_members(self, device, body=None):
        """Removes specified rule from firewall policy."""
        return self.put(self.device_shutdown_proto_members_path % (device),
                        body=body)

    def flush_proto_members(self, device, body=None):
        """Removes specified rule from firewall policy."""
        return self.put(self.device_flush_proto_members_path % (device),
                        body=body)

    def refresh_proto_members(self, device, body=None):
        """Removes specified rule from firewall policy."""
        return self.put(self.device_refresh_proto_members_path % (device),
                        body=body)

    def init_device_proto(self, device, body=None):
        """Init device proto."""
        return self.put(self.device_init_proto_path % (device),
                        body=body)

    def neutron_create_network(self, device, body=None):
        """Init device proto."""
        return self.put(self.neutron_create_network_path % (device),
                        body=body)

    def neutron_delete_network(self, device, body=None):
        """Init device proto."""
        return self.put(self.neutron_delete_network_path % (device),
                        body=body)

    def neutron_create_subnet(self, device, body=None):
        """Init device proto."""
        return self.put(self.neutron_create_subnet_path % (device),
                        body=body)

    def neutron_delete_subnet(self, device, body=None):
        """Init device proto."""
        return self.put(self.neutron_delete_subnet_path % (device),
                        body=body)

    def neutron_create_router(self, device, body=None):
        """Init device proto."""
        return self.put(self.neutron_create_router_path % (device),
                        body=body)

    def neutron_delete_router(self, device, body=None):
        """Init device proto."""
        return self.put(self.neutron_delete_router_path % (device),
                        body=body)

    def neutron_create_router_interface(self, device, body=None):
        """Init device proto."""
        return self.put(self.neutron_create_router_interface_path % (device),
                        body=body)

    def neutron_delete_router_interface(self, device, body=None):
        """Init device proto."""
        return self.put(self.neutron_delete_router_interface_path % (device),
                        body=body)

    def list_topos(self, retrieve_all=True, **_params):
        """Fetches a list of all devices for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('topos', self.topos_path, retrieve_all,
                         **_params)

    def show_topo(self, topo, **_params):
        """Fetches information of a certain device."""
        return self.get(self.topo_path % (topo), params=_params)

    def create_topo(self, body=None):
        """Creates a new topo."""
        return self.post(self.topos_path, body=body)

    def update_topo(self, topo, body=None):
        """Updates a topo."""
        return self.put(self.topo_path % (topo), body=body)

    def delete_topo(self, topo):
        """Deletes the specified topo."""
        return self.delete(self.topo_path % (topo))

    def list_links(self, retrieve_all=True, **_params):
        """Fetches a list of all devices for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('links', self.links_path, retrieve_all,
                         **_params)

    def show_link(self, link, **_params):
        """Fetches information of a certain device."""
        return self.get(self.link_path % (link), params=_params)

    def create_link(self, body=None):
        """Creates a new link."""
        return self.post(self.links_path, body=body)

    def update_link(self, link, body=None):
        """Updates a link."""
        return self.put(self.link_path % (link), body=body)

    def delete_link(self, link):
        """Deletes the specified link."""
        return self.delete(self.link_path % (link))

    def list_flows(self, retrieve_all=True, **_params):
        """Fetches a list of all devices for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('flows', self.flows_path, retrieve_all,
                         **_params)

    def show_flow(self, flow, **_params):
        """Fetches information of a certain device."""
        return self.get(self.flow_path % (flow), params=_params)

    def create_flow(self, body=None):
        """Creates a new flow."""
        return self.post(self.flows_path, body=body)

    def update_flow(self, flow, body=None):
        """Updates a flow."""
        return self.put(self.flow_path % (flow), body=body)

    def delete_flow(self, flow):
        """Deletes the specified flow."""
        return self.delete(self.flow_path % (flow))

    def list_groups(self, retrieve_all=True, **_params):
        """Fetches a list of all devices for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('groups', self.groups_path, retrieve_all,
                         **_params)

    def show_group(self, group, **_params):
        """Fetches information of a certain device."""
        return self.get(self.group_path % (group), params=_params)

    def create_group(self, body=None):
        """Creates a new group."""
        return self.post(self.groups_path, body=body)

    def update_group(self, group, body=None):
        """Updates a group."""
        return self.put(self.group_path % (group), body=body)

    def delete_group(self, group):
        """Deletes the specified group."""
        return self.delete(self.group_path % (group))

    def list_interfaces(self, retrieve_all=True, **_params):
        """Fetches a list of all devices for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('interfaces', self.interfaces_path, retrieve_all,
                         **_params)

    def show_interface(self, interface, **_params):
        """Fetches information of a certain device."""
        return self.get(self.interface_path % (interface), params=_params)

    def create_interface(self, body=None):
        """Creates a new interface."""
        return self.post(self.interfaces_path, body=body)

    def update_interface(self, interface, body=None):
        """Updates a interface."""
        return self.put(self.interface_path % (interface), body=body)

    def delete_interface(self, interface):
        """Deletes the specified interface."""
        return self.delete(self.interface_path % (interface))

    def list_service_networks(self, retrieve_all=True, **_params):
        """Fetches a list of all devices for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('service_networks', self.service_networks_path, retrieve_all,
                         **_params)

    def show_service_network(self, service_network, **_params):
        """Fetches information of a certain device."""
        return self.get(self.service_network_path % (service_network), params=_params)

    def create_service_network(self, body=None):
        """Creates a new service_network."""
        return self.post(self.service_networks_path, body=body)

    def update_service_network(self, service_network, body=None):
        """Updates a service_network."""
        return self.put(self.service_network_path % (service_network), body=body)

    def delete_service_network(self, service_network):
        """Deletes the specified service_network."""
        return self.delete(self.service_network_path % (service_network))

    def list_flow_mappings(self, retrieve_all=True, **_params):
        """Fetches a list of all devices for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('flow_mappings', self.flow_mappings_path, retrieve_all,
                         **_params)

    def show_flow_mapping(self, flow_mapping, **_params):
        """Fetches information of a certain device."""
        return self.get(self.flow_mapping_path % (flow_mapping), params=_params)

    def create_flow_mapping(self, body=None):
        """Creates a new flow_mapping."""
        return self.post(self.flow_mappings_path, body=body)

    def update_flow_mapping(self, flow_mapping, body=None):
        """Updates a flow_mapping."""
        return self.put(self.flow_mapping_path % (flow_mapping), body=body)

    def delete_flow_mapping(self, flow_mapping):
        """Deletes the specified flow_mapping."""
        return self.delete(self.flow_mapping_path % (flow_mapping))

    def list_flow_nexthops(self, retrieve_all=True, **_params):
        """Fetches a list of all devices for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('flow_nexthops', self.flow_nexthops_path, retrieve_all,
                         **_params)

    def show_flow_nexthop(self, flow_nexthop, **_params):
        """Fetches information of a certain device."""
        return self.get(self.flow_nexthop_path % (flow_nexthop), params=_params)

    def create_flow_nexthop(self, body=None):
        """Creates a new flow_nexthop."""
        return self.post(self.flow_nexthops_path, body=body)

    def update_flow_nexthop(self, flow_nexthop, body=None):
        """Updates a flow_nexthop."""
        return self.put(self.flow_nexthop_path % (flow_nexthop), body=body)

    def delete_flow_nexthop(self, flow_nexthop):
        """Deletes the specified flow_nexthop."""
        return self.delete(self.flow_nexthop_path % (flow_nexthop))

    def list_flow_paths(self, retrieve_all=True, **_params):
        """Fetches a list of all flow_paths for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('flow_paths', self.flow_paths_path, retrieve_all,
                         **_params)

    def show_flow_path(self, flow_path, **_params):
        """Fetches information of a certain flow_path."""
        return self.get(self.flow_path_path % (flow_path), params=_params)

    def create_flow_path(self, body=None):
        """Creates a new flow_path."""
        return self.post(self.flow_paths_path, body=body)

    def update_flow_path(self, flow_path, body=None):
        """Updates a flow_path."""
        return self.put(self.flow_path_path % (flow_path), body=body)

    def delete_flow_path(self, flow_path):
        """Deletes the specified flow_path."""
        return self.delete(self.flow_path_path % (flow_path))

    def list_device_proto_members(self, retrieve_all=True, **_params):
        """Fetches a list of all device_proto_members for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('device_proto_members', self.device_proto_members_path, retrieve_all,
                         **_params)

    def show_device_proto_member(self, device_proto_member, **_params):
        """Fetches information of a certain device_proto_member."""
        return self.get(self.device_proto_member_path % (device_proto_member), params=_params)

    def create_device_proto_member(self, body=None):
        """Creates a new device_proto_member."""
        return self.post(self.device_proto_members_path, body=body)

    def update_device_proto_member(self, device_proto_member, body=None):
        """Updates a device_proto_member."""
        return self.put(self.device_proto_member_path % (device_proto_member), body=body)

    def delete_device_proto_member(self, device_proto_member):
        """Deletes the specified device_proto_member."""
        return self.delete(self.device_proto_member_path % (device_proto_member))

    # Backup config rest interface start
    def list_backup_configs(self, retrieve_all=True, **_params):
        """Fetches a list of all backup configs for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('backup_configs', self.backup_configs_path, retrieve_all, **_params)

    def show_backup_config(self, backup_config_id, **_params):
        """Fetches information of a certain backup config."""
        return self.get(self.backup_config_path % (backup_config_id), params=_params)

    def create_backup_config(self, body=None):
        """Creates a new backup config."""
        return self.post(self.backup_configs_path, body=body)

    def update_backup_config(self, backup_config_id, body=None):
        """Updates a backup config."""
        return self.put(self.backup_config_path % (backup_config_id), body=body)

    def restore_backup_config(self, backup_config_id, body=None):
        """Restore specified backup config."""
        return self.put((self.backup_config_path % backup_config_id) + "/restore_config",
                        body=body)

    def delete_backup_config(self, backup_config_id):
        """Deletes the specified backup config."""
        return self.delete(self.backup_config_path % (backup_config_id))
    # Backup config rest interface end

    # Vnfc instance rest interface start
    def list_vnfc_instances(self, retrieve_all=True, **_params):
        """Fetches a list of all vnfc instances for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('vnfc_instances', self.vnfc_instances_path, retrieve_all, **_params)

    def show_vnfc_instance(self, vnfc_instance_id, **_params):
        """Fetches information of a certain vnfc instance."""
        return self.get(self.vnfc_instance_path % (vnfc_instance_id), params=_params)

    def create_vnfc_instance(self, body=None):
        """Creates a  new vnfc instance."""
        return self.post(self.vnfc_instances_path, body=body)

    def update_vnfc_instance(self, vnfc_instance_id, body=None):
        """Updates a vnfc instance."""
        return self.put(self.vnfc_instance_path % (vnfc_instance_id), body=body)

    def delete_vnfc_instance(self, vnfc_instance_id):
        """Deletes the specified backup config."""
        return self.delete(self.vnfc_instance_path % (vnfc_instance_id))

    # Vnfc instance rest interface end

    # Vnfc flow rest interface start
    def list_vnfc_flows(self, retrieve_all=True, **_params):
        """Fetches a list of all vnfc flow for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('vnfc_flows', self.vnfc_flows_path, retrieve_all, **_params)

    def show_vnfc_flow(self, vnfc_flow_id, **_params):
        """Fetches information of a certain vnfc flow."""
        return self.get(self.vnfc_flow_path % (vnfc_flow_id), params=_params)

    def create_vnfc_flow(self, body=None):
        """Creates a new vnfc flow."""
        return self.post(self.vnfc_flows_path, body=body)

    def update_vnfc_flow(self, vnfc_flow_id, body=None):
        """Updates a vnfc flow."""
        return self.put(self.vnfc_flow_path % (vnfc_flow_id), body=body)

    def delete_vnfc_flow(self, vnfc_flow_id):
        """Deletes the vnfc flow."""
        return self.delete(self.vnfc_flow_path % (vnfc_flow_id))

    def list_device_ports(self, retrieve_all=True, **_params):
        """Fetches a list of all devices for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('device_ports', self.device_ports_path, retrieve_all,
                         **_params)

    def show_device_port(self, device_port, **_params):
        """Fetches information of a certain device."""
        return self.get(self.device_port_path % (device_port), params=_params)

    def create_device_port(self, body=None):
        """Creates a new device_port."""
        return self.post(self.device_ports_path, body=body)

    def update_device_port(self, device_port, body=None):
        """Updates a device_port."""
        return self.put(self.device_port_path % (device_port), body=body)

    def delete_device_port(self, device_port):
        """Deletes the specified device_port."""
        return self.delete(self.device_port_path % (device_port))

    def list_flow_types(self, retrieve_all=True, **_params):
        """Fetches a list of all devices for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('flow_types', self.flow_types_path, retrieve_all,
                         **_params)

    def show_flow_type(self, flow_type, **_params):
        """Fetches information of a certain device."""
        return self.get(self.flow_type_path % (flow_type), params=_params)

    def create_flow_type(self, body=None):
        """Creates a new flow_type."""
        return self.post(self.flow_types_path, body=body)

    def update_flow_type(self, flow_type, body=None):
        """Updates a flow_type."""
        return self.put(self.flow_type_path % (flow_type), body=body)

    def delete_flow_type(self, flow_type):
        """Deletes the specified flow_type."""
        return self.delete(self.flow_type_path % (flow_type))

    def list_flow_profiles(self, retrieve_all=True, **_params):
        """Fetches a list of all devices for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('flow_profiles', self.flow_profiles_path, retrieve_all,
                         **_params)

    def show_flow_profile(self, flow_profile, **_params):
        """Fetches information of a certain device."""
        return self.get(self.flow_profile_path % (flow_profile), params=_params)

    def create_flow_profile(self, body=None):
        """Creates a new flow_profile."""
        return self.post(self.flow_profiles_path, body=body)

    def update_flow_profile(self, flow_profile, body=None):
        """Updates a flow_profile."""
        return self.put(self.flow_profile_path % (flow_profile), body=body)

    def delete_flow_profile(self, flow_profile):
        """Deletes the specified flow_profile."""
        return self.delete(self.flow_profile_path % (flow_profile))

    # Vnfc flow rest interface end

    def list_resources(self, retrieve_all=True, **_params):
        """Fetches a list of all resource for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('resources', self.resources_path, retrieve_all, **_params)

    def show_resource(self, resource_id, **_params):
        """Fetches information of a certain resource."""
        return self.get(self.resource_path % (resource_id), params=_params)

    def create_resource(self, body=None):
        """Creates a new resource."""
        return self.post(self.resources_path, body=body)

    def update_resource(self, resource_id, body=None):
        """Updates a resource."""
        return self.put(self.resource_path % (resource_id), body=body)

    def delete_resource(self, resource_id):
        """Deletes the resource."""
        return self.delete(self.resource_path % (resource_id))

    def list_networks(self, retrieve_all=True, **_params):
        """Fetches a list of all networks for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('networks', self.networks_path, retrieve_all,
                         **_params)

    def show_network(self, network, **_params):
        """Fetches information of a certain network."""
        return self.get(self.network_path % (network), params=_params)

    def create_network(self, body=None):
        """Creates a new network."""
        return self.post(self.networks_path, body=body)

    def update_network(self, network, body=None):
        """Updates a network."""
        return self.put(self.network_path % (network), body=body)

    def delete_network(self, network):
        """Deletes the specified network."""
        return self.delete(self.network_path % (network))

    def list_subnets(self, retrieve_all=True, **_params):
        """Fetches a list of all subnets for a project."""
        return self.list('subnets', self.subnets_path, retrieve_all,
                         **_params)

    def show_subnet(self, subnet, **_params):
        """Fetches information of a certain subnet."""
        return self.get(self.subnet_path % (subnet), params=_params)

    def create_subnet(self, body=None):
        """Creates a new subnet."""
        return self.post(self.subnets_path, body=body)

    def update_subnet(self, subnet, body=None):
        """Updates a subnet."""
        return self.put(self.subnet_path % (subnet), body=body)

    def delete_subnet(self, subnet):
        """Deletes the specified subnet."""
        return self.delete(self.subnet_path % (subnet))

    def list_subnetpools(self, retrieve_all=True, **_params):
        """Fetches a list of all subnetpools for a project."""
        return self.list('subnetpools', self.subnetpools_path, retrieve_all,
                         **_params)

    def show_subnetpool(self, subnetpool, **_params):
        """Fetches information of a certain subnetpool."""
        return self.get(self.subnetpool_path % (subnetpool), params=_params)

    def create_subnetpool(self, body=None):
        """Creates a new subnetpool."""
        return self.post(self.subnetpools_path, body=body)

    def update_subnetpool(self, subnetpool, body=None):
        """Updates a subnetpool."""
        return self.put(self.subnetpool_path % (subnetpool), body=body)

    def delete_subnetpool(self, subnetpool):
        """Deletes the specified subnetpool."""
        return self.delete(self.subnetpool_path % (subnetpool))

    def list_routers(self, retrieve_all=True, **_params):
        """Fetches a list of all routers for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('routers', self.routers_path, retrieve_all,
                         **_params)

    def show_router(self, router, **_params):
        """Fetches information of a certain router."""
        return self.get(self.router_path % (router), params=_params)

    def create_router(self, body=None):
        """Creates a new router."""
        return self.post(self.routers_path, body=body)

    def update_router(self, router, body=None):
        """Updates a router."""
        return self.put(self.router_path % (router), body=body)

    def delete_router(self, router):
        """Deletes the specified router."""
        return self.delete(self.router_path % (router))

    def list_address_scopes(self, retrieve_all=True, **_params):
        """Fetches a list of all address scopes for a project."""
        return self.list('address_scopes', self.address_scopes_path,
                         retrieve_all, **_params)

    def show_address_scope(self, address_scope, **_params):
        """Fetches information of a certain address scope."""
        return self.get(self.address_scope_path % (address_scope),
                        params=_params)

    def create_address_scope(self, body=None):
        """Creates a new address scope."""
        return self.post(self.address_scopes_path, body=body)

    def update_address_scope(self, address_scope, body=None):
        """Updates a address scope."""
        return self.put(self.address_scope_path % (address_scope), body=body)

    def delete_address_scope(self, address_scope):
        """Deletes the specified address scope."""
        return self.delete(self.address_scope_path % (address_scope))

    def add_interface_router(self, router, body=None):
        """Adds an internal network interface to the specified router."""
        return self.put((self.router_path % router) + "/add_router_interface",
                        body=body)

    def remove_interface_router(self, router, body=None):
        """Removes an internal network interface from the specified router."""
        return self.put((self.router_path % router) +
                        "/remove_router_interface", body=body)

    def add_gateway_router(self, router, body=None):
        """Adds an external network gateway to the specified router."""
        return self.put((self.router_path % router),
                        body={'router': {'external_gateway_info': body}})

    def remove_gateway_router(self, router):
        """Removes an external network gateway from the specified router."""
        return self.put((self.router_path % router),
                        body={'router': {'external_gateway_info': {}}})

    def list_floatingips(self, retrieve_all=True, **_params):
        """Fetches a list of all floatingips for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('floatingips', self.floatingips_path, retrieve_all,
                         **_params)

    def show_floatingip(self, floatingip, **_params):
        """Fetches information of a certain floatingip."""
        return self.get(self.floatingip_path % (floatingip), params=_params)

    def create_floatingip(self, body=None):
        """Creates a new floatingip."""
        return self.post(self.floatingips_path, body=body)

    def update_floatingip(self, floatingip, body=None):
        """Updates a floatingip."""
        return self.put(self.floatingip_path % (floatingip), body=body)

    def delete_floatingip(self, floatingip):
        """Deletes the specified floatingip."""
        return self.delete(self.floatingip_path % (floatingip))

    def create_security_group(self, body=None):
        """Creates a new security group."""
        return self.post(self.security_groups_path, body=body)

    def update_security_group(self, security_group, body=None):
        """Updates a security group."""
        return self.put(self.security_group_path %
                        security_group, body=body)

    def list_security_groups(self, retrieve_all=True, **_params):
        """Fetches a list of all security groups for a project."""
        return self.list('security_groups', self.security_groups_path,
                         retrieve_all, **_params)

    def show_security_group(self, security_group, **_params):
        """Fetches information of a certain security group."""
        return self.get(self.security_group_path % (security_group),
                        params=_params)

    def delete_security_group(self, security_group):
        """Deletes the specified security group."""
        return self.delete(self.security_group_path % (security_group))

    def create_security_group_rule(self, body=None):
        """Creates a new security group rule."""
        return self.post(self.security_group_rules_path, body=body)

    def delete_security_group_rule(self, security_group_rule):
        """Deletes the specified security group rule."""
        return self.delete(self.security_group_rule_path %
                           (security_group_rule))

    def list_security_group_rules(self, retrieve_all=True, **_params):
        """Fetches a list of all security group rules for a project."""
        return self.list('security_group_rules',
                         self.security_group_rules_path,
                         retrieve_all, **_params)

    def show_security_group_rule(self, security_group_rule, **_params):
        """Fetches information of a certain security group rule."""
        return self.get(self.security_group_rule_path % (security_group_rule),
                        params=_params)

    def list_endpoint_groups(self, retrieve_all=True, **_params):
        """Fetches a list of all VPN endpoint groups for a project."""
        return self.list('endpoint_groups', self.endpoint_groups_path,
                         retrieve_all, **_params)

    def show_endpoint_group(self, endpointgroup, **_params):
        """Fetches information for a specific VPN endpoint group."""
        return self.get(self.endpoint_group_path % endpointgroup,
                        params=_params)

    def create_endpoint_group(self, body=None):
        """Creates a new VPN endpoint group."""
        return self.post(self.endpoint_groups_path, body=body)

    def update_endpoint_group(self, endpoint_group, body=None):
        """Updates a VPN endpoint group."""
        return self.put(self.endpoint_group_path % endpoint_group, body=body)

    def delete_endpoint_group(self, endpoint_group):
        """Deletes the specified VPN endpoint group."""
        return self.delete(self.endpoint_group_path % endpoint_group)

    def list_vpnservices(self, retrieve_all=True, **_params):
        """Fetches a list of all configured VPN services for a project."""
        return self.list('vpnservices', self.vpnservices_path, retrieve_all,
                         **_params)

    def show_vpnservice(self, vpnservice, **_params):
        """Fetches information of a specific VPN service."""
        return self.get(self.vpnservice_path % (vpnservice), params=_params)

    def create_vpnservice(self, body=None):
        """Creates a new VPN service."""
        return self.post(self.vpnservices_path, body=body)

    def update_vpnservice(self, vpnservice, body=None):
        """Updates a VPN service."""
        return self.put(self.vpnservice_path % (vpnservice), body=body)

    def delete_vpnservice(self, vpnservice):
        """Deletes the specified VPN service."""
        return self.delete(self.vpnservice_path % (vpnservice))

    def list_ipsec_site_connections(self, retrieve_all=True, **_params):
        """Fetches all configured IPsecSiteConnections for a project."""
        return self.list('ipsec_site_connections',
                         self.ipsec_site_connections_path,
                         retrieve_all,
                         **_params)

    def show_ipsec_site_connection(self, ipsecsite_conn, **_params):
        """Fetches information of a specific IPsecSiteConnection."""
        return self.get(
            self.ipsec_site_connection_path % (ipsecsite_conn), params=_params
        )

    def create_ipsec_site_connection(self, body=None):
        """Creates a new IPsecSiteConnection."""
        return self.post(self.ipsec_site_connections_path, body=body)

    def update_ipsec_site_connection(self, ipsecsite_conn, body=None):
        """Updates an IPsecSiteConnection."""
        return self.put(
            self.ipsec_site_connection_path % (ipsecsite_conn), body=body
        )

    def delete_ipsec_site_connection(self, ipsecsite_conn):
        """Deletes the specified IPsecSiteConnection."""
        return self.delete(self.ipsec_site_connection_path % (ipsecsite_conn))

    def list_ikepolicies(self, retrieve_all=True, **_params):
        """Fetches a list of all configured IKEPolicies for a project."""
        return self.list('ikepolicies', self.ikepolicies_path, retrieve_all,
                         **_params)

    def show_ikepolicy(self, ikepolicy, **_params):
        """Fetches information of a specific IKEPolicy."""
        return self.get(self.ikepolicy_path % (ikepolicy), params=_params)

    def create_ikepolicy(self, body=None):
        """Creates a new IKEPolicy."""
        return self.post(self.ikepolicies_path, body=body)

    def update_ikepolicy(self, ikepolicy, body=None):
        """Updates an IKEPolicy."""
        return self.put(self.ikepolicy_path % (ikepolicy), body=body)

    def delete_ikepolicy(self, ikepolicy):
        """Deletes the specified IKEPolicy."""
        return self.delete(self.ikepolicy_path % (ikepolicy))

    def list_ipsecpolicies(self, retrieve_all=True, **_params):
        """Fetches a list of all configured IPsecPolicies for a project."""
        return self.list('ipsecpolicies',
                         self.ipsecpolicies_path,
                         retrieve_all,
                         **_params)

    def show_ipsecpolicy(self, ipsecpolicy, **_params):
        """Fetches information of a specific IPsecPolicy."""
        return self.get(self.ipsecpolicy_path % (ipsecpolicy), params=_params)

    def create_ipsecpolicy(self, body=None):
        """Creates a new IPsecPolicy."""
        return self.post(self.ipsecpolicies_path, body=body)

    def update_ipsecpolicy(self, ipsecpolicy, body=None):
        """Updates an IPsecPolicy."""
        return self.put(self.ipsecpolicy_path % (ipsecpolicy), body=body)

    def delete_ipsecpolicy(self, ipsecpolicy):
        """Deletes the specified IPsecPolicy."""
        return self.delete(self.ipsecpolicy_path % (ipsecpolicy))

    def list_loadbalancers(self, retrieve_all=True, **_params):
        """Fetches a list of all loadbalancers for a project."""
        return self.list('loadbalancers', self.lbaas_loadbalancers_path,
                         retrieve_all, **_params)

    def show_loadbalancer(self, lbaas_loadbalancer, **_params):
        """Fetches information for a load balancer."""
        return self.get(self.lbaas_loadbalancer_path % (lbaas_loadbalancer),
                        params=_params)

    def create_loadbalancer(self, body=None):
        """Creates a new load balancer."""
        return self.post(self.lbaas_loadbalancers_path, body=body)

    def update_loadbalancer(self, lbaas_loadbalancer, body=None):
        """Updates a load balancer."""
        return self.put(self.lbaas_loadbalancer_path % (lbaas_loadbalancer),
                        body=body)

    def delete_loadbalancer(self, lbaas_loadbalancer):
        """Deletes the specified load balancer."""
        return self.delete(self.lbaas_loadbalancer_path %
                           (lbaas_loadbalancer))

    def retrieve_loadbalancer_stats(self, loadbalancer, **_params):
        """Retrieves stats for a certain load balancer."""
        return self.get(self.lbaas_loadbalancer_path_stats % (loadbalancer),
                        params=_params)

    def retrieve_loadbalancer_status(self, loadbalancer, **_params):
        """Retrieves status for a certain load balancer."""
        return self.get(self.lbaas_loadbalancer_path_status % (loadbalancer),
                        params=_params)

    def list_listeners(self, retrieve_all=True, **_params):
        """Fetches a list of all lbaas_listeners for a project."""
        return self.list('listeners', self.lbaas_listeners_path,
                         retrieve_all, **_params)

    def show_listener(self, lbaas_listener, **_params):
        """Fetches information for a lbaas_listener."""
        return self.get(self.lbaas_listener_path % (lbaas_listener),
                        params=_params)

    def create_listener(self, body=None):
        """Creates a new lbaas_listener."""
        return self.post(self.lbaas_listeners_path, body=body)

    def update_listener(self, lbaas_listener, body=None):
        """Updates a lbaas_listener."""
        return self.put(self.lbaas_listener_path % (lbaas_listener),
                        body=body)

    def delete_listener(self, lbaas_listener):
        """Deletes the specified lbaas_listener."""
        return self.delete(self.lbaas_listener_path % (lbaas_listener))

    def list_lbaas_l7policies(self, retrieve_all=True, **_params):
        """Fetches a list of all L7 policies for a listener."""
        return self.list('l7policies', self.lbaas_l7policies_path,
                         retrieve_all, **_params)

    def show_lbaas_l7policy(self, l7policy, **_params):
        """Fetches information of a certain listener's L7 policy."""
        return self.get(self.lbaas_l7policy_path % l7policy,
                        params=_params)

    def create_lbaas_l7policy(self, body=None):
        """Creates L7 policy for a certain listener."""
        return self.post(self.lbaas_l7policies_path, body=body)

    def update_lbaas_l7policy(self, l7policy, body=None):
        """Updates L7 policy."""
        return self.put(self.lbaas_l7policy_path % l7policy,
                        body=body)

    def delete_lbaas_l7policy(self, l7policy):
        """Deletes the specified L7 policy."""
        return self.delete(self.lbaas_l7policy_path % l7policy)

    def list_lbaas_l7rules(self, l7policy, retrieve_all=True, **_params):
        """Fetches a list of all rules for L7 policy."""
        return self.list('rules', self.lbaas_l7rules_path % l7policy,
                         retrieve_all, **_params)

    def show_lbaas_l7rule(self, l7rule, l7policy, **_params):
        """Fetches information of a certain L7 policy's rule."""
        return self.get(self.lbaas_l7rule_path % (l7policy, l7rule),
                        params=_params)

    def create_lbaas_l7rule(self, l7policy, body=None):
        """Creates rule for a certain L7 policy."""
        return self.post(self.lbaas_l7rules_path % l7policy, body=body)

    def update_lbaas_l7rule(self, l7rule, l7policy, body=None):
        """Updates L7 rule."""
        return self.put(self.lbaas_l7rule_path % (l7policy, l7rule),
                        body=body)

    def delete_lbaas_l7rule(self, l7rule, l7policy):
        """Deletes the specified L7 rule."""
        return self.delete(self.lbaas_l7rule_path % (l7policy, l7rule))

    def list_lbaas_pools(self, retrieve_all=True, **_params):
        """Fetches a list of all lbaas_pools for a project."""
        return self.list('pools', self.lbaas_pools_path,
                         retrieve_all, **_params)

    def show_lbaas_pool(self, lbaas_pool, **_params):
        """Fetches information for a lbaas_pool."""
        return self.get(self.lbaas_pool_path % (lbaas_pool),
                        params=_params)

    def create_lbaas_pool(self, body=None):
        """Creates a new lbaas_pool."""
        return self.post(self.lbaas_pools_path, body=body)

    def update_lbaas_pool(self, lbaas_pool, body=None):
        """Updates a lbaas_pool."""
        return self.put(self.lbaas_pool_path % (lbaas_pool),
                        body=body)

    def delete_lbaas_pool(self, lbaas_pool):
        """Deletes the specified lbaas_pool."""
        return self.delete(self.lbaas_pool_path % (lbaas_pool))

    def list_lbaas_healthmonitors(self, retrieve_all=True, **_params):
        """Fetches a list of all lbaas_healthmonitors for a project."""
        return self.list('healthmonitors', self.lbaas_healthmonitors_path,
                         retrieve_all, **_params)

    def show_lbaas_healthmonitor(self, lbaas_healthmonitor, **_params):
        """Fetches information for a lbaas_healthmonitor."""
        return self.get(self.lbaas_healthmonitor_path % (lbaas_healthmonitor),
                        params=_params)

    def create_lbaas_healthmonitor(self, body=None):
        """Creates a new lbaas_healthmonitor."""
        return self.post(self.lbaas_healthmonitors_path, body=body)

    def update_lbaas_healthmonitor(self, lbaas_healthmonitor, body=None):
        """Updates a lbaas_healthmonitor."""
        return self.put(self.lbaas_healthmonitor_path % (lbaas_healthmonitor),
                        body=body)

    def delete_lbaas_healthmonitor(self, lbaas_healthmonitor):
        """Deletes the specified lbaas_healthmonitor."""
        return self.delete(self.lbaas_healthmonitor_path %
                           (lbaas_healthmonitor))

    def list_lbaas_loadbalancers(self, retrieve_all=True, **_params):
        """Fetches a list of all lbaas_loadbalancers for a project."""
        return self.list('loadbalancers', self.lbaas_loadbalancers_path,
                         retrieve_all, **_params)

    def list_lbaas_members(self, lbaas_pool, retrieve_all=True, **_params):
        """Fetches a list of all lbaas_members for a project."""
        return self.list('members', self.lbaas_members_path % lbaas_pool,
                         retrieve_all, **_params)

    def show_lbaas_member(self, lbaas_member, lbaas_pool, **_params):
        """Fetches information of a certain lbaas_member."""
        return self.get(self.lbaas_member_path % (lbaas_pool, lbaas_member),
                        params=_params)

    def create_lbaas_member(self, lbaas_pool, body=None):
        """Creates a lbaas_member."""
        return self.post(self.lbaas_members_path % lbaas_pool, body=body)

    def update_lbaas_member(self, lbaas_member, lbaas_pool, body=None):
        """Updates a lbaas_member."""
        return self.put(self.lbaas_member_path % (lbaas_pool, lbaas_member),
                        body=body)

    def delete_lbaas_member(self, lbaas_member, lbaas_pool):
        """Deletes the specified lbaas_member."""
        return self.delete(self.lbaas_member_path % (lbaas_pool, lbaas_member))

    def list_vips(self, retrieve_all=True, **_params):
        """Fetches a list of all load balancer vips for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('vips', self.vips_path, retrieve_all,
                         **_params)

    def show_vip(self, vip, **_params):
        """Fetches information of a certain load balancer vip."""
        return self.get(self.vip_path % (vip), params=_params)

    def create_vip(self, body=None):
        """Creates a new load balancer vip."""
        return self.post(self.vips_path, body=body)

    def update_vip(self, vip, body=None):
        """Updates a load balancer vip."""
        return self.put(self.vip_path % (vip), body=body)

    def delete_vip(self, vip):
        """Deletes the specified load balancer vip."""
        return self.delete(self.vip_path % (vip))

    def list_pools(self, retrieve_all=True, **_params):
        """Fetches a list of all load balancer pools for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('pools', self.pools_path, retrieve_all,
                         **_params)

    def show_pool(self, pool, **_params):
        """Fetches information of a certain load balancer pool."""
        return self.get(self.pool_path % (pool), params=_params)

    def create_pool(self, body=None):
        """Creates a new load balancer pool."""
        return self.post(self.pools_path, body=body)

    def update_pool(self, pool, body=None):
        """Updates a load balancer pool."""
        return self.put(self.pool_path % (pool), body=body)

    def delete_pool(self, pool):
        """Deletes the specified load balancer pool."""
        return self.delete(self.pool_path % (pool))

    def retrieve_pool_stats(self, pool, **_params):
        """Retrieves stats for a certain load balancer pool."""
        return self.get(self.pool_path_stats % (pool), params=_params)

    def list_members(self, retrieve_all=True, **_params):
        """Fetches a list of all load balancer members for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('members', self.members_path, retrieve_all,
                         **_params)

    def show_member(self, member, **_params):
        """Fetches information of a certain load balancer member."""
        return self.get(self.member_path % (member), params=_params)

    def create_member(self, body=None):
        """Creates a new load balancer member."""
        return self.post(self.members_path, body=body)

    def update_member(self, member, body=None):
        """Updates a load balancer member."""
        return self.put(self.member_path % (member), body=body)

    def delete_member(self, member):
        """Deletes the specified load balancer member."""
        return self.delete(self.member_path % (member))

    def list_health_monitors(self, retrieve_all=True, **_params):
        """Fetches a list of all load balancer health monitors for a project.

        """
        # Pass filters in "params" argument to do_request
        return self.list('health_monitors', self.health_monitors_path,
                         retrieve_all, **_params)

    def show_health_monitor(self, health_monitor, **_params):
        """Fetches information of a certain load balancer health monitor."""
        return self.get(self.health_monitor_path % (health_monitor),
                        params=_params)

    def create_health_monitor(self, body=None):
        """Creates a new load balancer health monitor."""
        return self.post(self.health_monitors_path, body=body)

    def update_health_monitor(self, health_monitor, body=None):
        """Updates a load balancer health monitor."""
        return self.put(self.health_monitor_path % (health_monitor), body=body)

    def delete_health_monitor(self, health_monitor):
        """Deletes the specified load balancer health monitor."""
        return self.delete(self.health_monitor_path % (health_monitor))

    def associate_health_monitor(self, pool, body):
        """Associate  specified load balancer health monitor and pool."""
        return self.post(self.associate_pool_health_monitors_path % (pool),
                         body=body)

    def disassociate_health_monitor(self, pool, health_monitor):
        """Disassociate specified load balancer health monitor and pool."""
        path = (self.disassociate_pool_health_monitors_path %
                {'pool': pool, 'health_monitor': health_monitor})
        return self.delete(path)

    def create_qos_queue(self, body=None):
        """Creates a new queue."""
        return self.post(self.qos_queues_path, body=body)

    def list_qos_queues(self, **_params):
        """Fetches a list of all queues for a project."""
        return self.get(self.qos_queues_path, params=_params)

    def show_qos_queue(self, queue, **_params):
        """Fetches information of a certain queue."""
        return self.get(self.qos_queue_path % (queue),
                        params=_params)

    def delete_qos_queue(self, queue):
        """Deletes the specified queue."""
        return self.delete(self.qos_queue_path % (queue))

    def list_agents(self, **_params):
        """Fetches agents."""
        # Pass filters in "params" argument to do_request
        return self.get(self.agents_path, params=_params)

    def show_agent(self, agent, **_params):
        """Fetches information of a certain agent."""
        return self.get(self.agent_path % (agent), params=_params)

    def update_agent(self, agent, body=None):
        """Updates an agent."""
        return self.put(self.agent_path % (agent), body=body)

    def delete_agent(self, agent):
        """Deletes the specified agent."""
        return self.delete(self.agent_path % (agent))

    def list_network_gateways(self, **_params):
        """Retrieve network gateways."""
        return self.get(self.network_gateways_path, params=_params)

    def show_network_gateway(self, gateway_id, **_params):
        """Fetch a network gateway."""
        return self.get(self.network_gateway_path % gateway_id, params=_params)

    def create_network_gateway(self, body=None):
        """Create a new network gateway."""
        return self.post(self.network_gateways_path, body=body)

    def update_network_gateway(self, gateway_id, body=None):
        """Update a network gateway."""
        return self.put(self.network_gateway_path % gateway_id, body=body)

    def delete_network_gateway(self, gateway_id):
        """Delete the specified network gateway."""
        return self.delete(self.network_gateway_path % gateway_id)

    def connect_network_gateway(self, gateway_id, body=None):
        """Connect a network gateway to the specified network."""
        base_uri = self.network_gateway_path % gateway_id
        return self.put("%s/connect_network" % base_uri, body=body)

    def disconnect_network_gateway(self, gateway_id, body=None):
        """Disconnect a network from the specified gateway."""
        base_uri = self.network_gateway_path % gateway_id
        return self.put("%s/disconnect_network" % base_uri, body=body)

    def list_gateway_devices(self, **_params):
        """Retrieve gateway devices."""
        return self.get(self.gateway_devices_path, params=_params)

    def show_gateway_device(self, gateway_device_id, **_params):
        """Fetch a gateway device."""
        return self.get(self.gateway_device_path % gateway_device_id,
                        params=_params)

    def create_gateway_device(self, body=None):
        """Create a new gateway device."""
        return self.post(self.gateway_devices_path, body=body)

    def update_gateway_device(self, gateway_device_id, body=None):
        """Updates a new gateway device."""
        return self.put(self.gateway_device_path % gateway_device_id,
                        body=body)

    def delete_gateway_device(self, gateway_device_id):
        """Delete the specified gateway device."""
        return self.delete(self.gateway_device_path % gateway_device_id)

    def list_dhcp_agent_hosting_networks(self, network, **_params):
        """Fetches a list of dhcp agents hosting a network."""
        return self.get((self.network_path + self.DHCP_AGENTS) % network,
                        params=_params)

    def list_networks_on_dhcp_agent(self, dhcp_agent, **_params):
        """Fetches a list of dhcp agents hosting a network."""
        return self.get((self.agent_path + self.DHCP_NETS) % dhcp_agent,
                        params=_params)

    def add_network_to_dhcp_agent(self, dhcp_agent, body=None):
        """Adds a network to dhcp agent."""
        return self.post((self.agent_path + self.DHCP_NETS) % dhcp_agent,
                         body=body)

    def remove_network_from_dhcp_agent(self, dhcp_agent, network_id):
        """Remove a network from dhcp agent."""
        return self.delete((self.agent_path + self.DHCP_NETS + "/%s") % (
            dhcp_agent, network_id))

    def list_l3_agent_hosting_routers(self, router, **_params):
        """Fetches a list of L3 agents hosting a router."""
        return self.get((self.router_path + self.L3_AGENTS) % router,
                        params=_params)

    def list_routers_on_l3_agent(self, l3_agent, **_params):
        """Fetches a list of L3 agents hosting a router."""
        return self.get((self.agent_path + self.L3_ROUTERS) % l3_agent,
                        params=_params)

    def add_router_to_l3_agent(self, l3_agent, body):
        """Adds a router to L3 agent."""
        return self.post((self.agent_path + self.L3_ROUTERS) % l3_agent,
                         body=body)

    def list_dragents_hosting_bgp_speaker(self, bgp_speaker, **_params):
        """Fetches a list of Dynamic Routing agents hosting a BGP speaker."""
        return self.get((self.bgp_speaker_path + self.BGP_DRAGENTS)
                        % bgp_speaker, params=_params)

    def add_bgp_speaker_to_dragent(self, bgp_dragent, body):
        """Adds a BGP speaker to Dynamic Routing agent."""
        return self.post((self.agent_path + self.BGP_DRINSTANCES)
                         % bgp_dragent, body=body)

    def remove_bgp_speaker_from_dragent(self, bgp_dragent, bgpspeaker_id):
        """Removes a BGP speaker from Dynamic Routing agent."""
        return self.delete((self.agent_path + self.BGP_DRINSTANCES + "/%s")
                           % (bgp_dragent, bgpspeaker_id))

    def list_bgp_speaker_on_dragent(self, bgp_dragent, **_params):
        """Fetches a list of BGP speakers hosted by Dynamic Routing agent."""
        return self.get((self.agent_path + self.BGP_DRINSTANCES)
                        % bgp_dragent, params=_params)

    def list_firewall_rules(self, retrieve_all=True, **_params):
        """Fetches a list of all firewall rules for a project."""
        # Pass filters in "params" argument to do_request

        return self.list('firewall_rules', self.firewall_rules_path,
                         retrieve_all, **_params)

    def show_firewall_rule(self, firewall_rule, **_params):
        """Fetches information of a certain firewall rule."""
        return self.get(self.firewall_rule_path % (firewall_rule),
                        params=_params)

    def create_firewall_rule(self, body=None):
        """Creates a new firewall rule."""
        return self.post(self.firewall_rules_path, body=body)

    def update_firewall_rule(self, firewall_rule, body=None):
        """Updates a firewall rule."""
        return self.put(self.firewall_rule_path % (firewall_rule), body=body)

    def delete_firewall_rule(self, firewall_rule):
        """Deletes the specified firewall rule."""
        return self.delete(self.firewall_rule_path % (firewall_rule))

    def list_firewall_policies(self, retrieve_all=True, **_params):
        """Fetches a list of all firewall policies for a project."""
        # Pass filters in "params" argument to do_request

        return self.list('firewall_policies', self.firewall_policies_path,
                         retrieve_all, **_params)

    def show_firewall_policy(self, firewall_policy, **_params):
        """Fetches information of a certain firewall policy."""
        return self.get(self.firewall_policy_path % (firewall_policy),
                        params=_params)

    def create_firewall_policy(self, body=None):
        """Creates a new firewall policy."""
        return self.post(self.firewall_policies_path, body=body)

    def update_firewall_policy(self, firewall_policy, body=None):
        """Updates a firewall policy."""
        return self.put(self.firewall_policy_path % (firewall_policy),
                        body=body)

    def delete_firewall_policy(self, firewall_policy):
        """Deletes the specified firewall policy."""
        return self.delete(self.firewall_policy_path % (firewall_policy))

    def firewall_policy_insert_rule(self, firewall_policy, body=None):
        """Inserts specified rule into firewall policy."""
        return self.put(self.firewall_policy_insert_path % (firewall_policy),
                        body=body)

    def firewall_policy_remove_rule(self, firewall_policy, body=None):
        """Removes specified rule from firewall policy."""
        return self.put(self.firewall_policy_remove_path % (firewall_policy),
                        body=body)

    def list_firewalls(self, retrieve_all=True, **_params):
        """Fetches a list of all firewalls for a project."""
        # Pass filters in "params" argument to do_request

        return self.list('firewalls', self.firewalls_path, retrieve_all,
                         **_params)

    def show_firewall(self, firewall, **_params):
        """Fetches information of a certain firewall."""
        return self.get(self.firewall_path % (firewall), params=_params)

    def create_firewall(self, body=None):
        """Creates a new firewall."""
        return self.post(self.firewalls_path, body=body)

    def update_firewall(self, firewall, body=None):
        """Updates a firewall."""
        return self.put(self.firewall_path % (firewall), body=body)

    def delete_firewall(self, firewall):
        """Deletes the specified firewall."""
        return self.delete(self.firewall_path % (firewall))

    def list_fwaas_firewall_groups(self, retrieve_all=True, **_params):
        """Fetches a list of all firewall groups for a project"""
        return self.list('firewall_groups', self.fwaas_firewall_groups_path,
                         retrieve_all, **_params)

    def show_fwaas_firewall_group(self, fwg, **_params):
        """Fetches information of a certain firewall group"""
        return self.get(self.fwaas_firewall_group_path % (fwg), params=_params)

    def create_fwaas_firewall_group(self, body=None):
        """Creates a new firewall group"""
        return self.post(self.fwaas_firewall_groups_path, body=body)

    def update_fwaas_firewall_group(self, fwg, body=None):
        """Updates a firewall group"""
        return self.put(self.fwaas_firewall_group_path % (fwg), body=body)

    def delete_fwaas_firewall_group(self, fwg):
        """Deletes the specified firewall group"""
        return self.delete(self.fwaas_firewall_group_path % (fwg))

    def list_fwaas_firewall_rules(self, retrieve_all=True, **_params):
        """Fetches a list of all firewall rules for a project"""
        # Pass filters in "params" argument to do_request
        return self.list('firewall_rules', self.fwaas_firewall_rules_path,
                         retrieve_all, **_params)

    def show_fwaas_firewall_rule(self, firewall_rule, **_params):
        """Fetches information of a certain firewall rule"""
        return self.get(self.fwaas_firewall_rule_path % (firewall_rule),
                        params=_params)

    def create_fwaas_firewall_rule(self, body=None):
        """Creates a new firewall rule"""
        return self.post(self.fwaas_firewall_rules_path, body=body)

    def update_fwaas_firewall_rule(self, firewall_rule, body=None):
        """Updates a firewall rule"""
        return self.put(self.fwaas_firewall_rule_path % (firewall_rule),
                        body=body)

    def delete_fwaas_firewall_rule(self, firewall_rule):
        """Deletes the specified firewall rule"""
        return self.delete(self.fwaas_firewall_rule_path % (firewall_rule))

    def list_fwaas_firewall_policies(self, retrieve_all=True, **_params):
        """Fetches a list of all firewall policies for a project"""
        # Pass filters in "params" argument to do_request

        return self.list('firewall_policies',
                         self.fwaas_firewall_policies_path,
                         retrieve_all, **_params)

    def show_fwaas_firewall_policy(self, firewall_policy, **_params):
        """Fetches information of a certain firewall policy"""
        return self.get(self.fwaas_firewall_policy_path % (firewall_policy),
                        params=_params)

    def create_fwaas_firewall_policy(self, body=None):
        """Creates a new firewall policy"""
        return self.post(self.fwaas_firewall_policies_path, body=body)

    def update_fwaas_firewall_policy(self, firewall_policy, body=None):
        """Updates a firewall policy"""
        return self.put(self.fwaas_firewall_policy_path % (firewall_policy),
                        body=body)

    def delete_fwaas_firewall_policy(self, firewall_policy):
        """Deletes the specified firewall policy"""
        return self.delete(self.fwaas_firewall_policy_path % (firewall_policy))

    def insert_rule_fwaas_firewall_policy(self, firewall_policy, body=None):
        """Inserts specified rule into firewall policy"""
        return self.put((self.fwaas_firewall_policy_insert_path %
                        (firewall_policy)), body=body)

    def remove_rule_fwaas_firewall_policy(self, firewall_policy, body=None):
        """Removes specified rule from firewall policy"""
        return self.put((self.fwaas_firewall_policy_remove_path %
                        (firewall_policy)), body=body)

    def remove_router_from_l3_agent(self, l3_agent, router_id):
        """Remove a router from l3 agent."""
        return self.delete((self.agent_path + self.L3_ROUTERS + "/%s") % (
            l3_agent, router_id))

    def get_lbaas_agent_hosting_pool(self, pool, **_params):
        """Fetches a loadbalancer agent hosting a pool."""
        return self.get((self.pool_path + self.LOADBALANCER_AGENT) % pool,
                        params=_params)

    def list_pools_on_lbaas_agent(self, lbaas_agent, **_params):
        """Fetches a list of pools hosted by the loadbalancer agent."""
        return self.get((self.agent_path + self.LOADBALANCER_POOLS) %
                        lbaas_agent, params=_params)

    def get_lbaas_agent_hosting_loadbalancer(self, loadbalancer, **_params):
        """Fetches a loadbalancer agent hosting a loadbalancer."""
        return self.get((self.lbaas_loadbalancer_path +
                         self.LOADBALANCER_HOSTING_AGENT) % loadbalancer,
                        params=_params)

    def list_loadbalancers_on_lbaas_agent(self, lbaas_agent, **_params):
        """Fetches a list of loadbalancers hosted by the loadbalancer agent."""
        return self.get((self.agent_path + self.AGENT_LOADBALANCERS) %
                        lbaas_agent, params=_params)

    def list_service_providers(self, retrieve_all=True, **_params):
        """Fetches service providers."""
        # Pass filters in "params" argument to do_request
        return self.list('service_providers', self.service_providers_path,
                         retrieve_all, **_params)

    def create_metering_label(self, body=None):
        """Creates a metering label."""
        return self.post(self.metering_labels_path, body=body)

    def delete_metering_label(self, label):
        """Deletes the specified metering label."""
        return self.delete(self.metering_label_path % (label))

    def list_metering_labels(self, retrieve_all=True, **_params):
        """Fetches a list of all metering labels for a project."""
        return self.list('metering_labels', self.metering_labels_path,
                         retrieve_all, **_params)

    def show_metering_label(self, metering_label, **_params):
        """Fetches information of a certain metering label."""
        return self.get(self.metering_label_path %
                        (metering_label), params=_params)

    def create_metering_label_rule(self, body=None):
        """Creates a metering label rule."""
        return self.post(self.metering_label_rules_path, body=body)

    def delete_metering_label_rule(self, rule):
        """Deletes the specified metering label rule."""
        return self.delete(self.metering_label_rule_path % (rule))

    def list_metering_label_rules(self, retrieve_all=True, **_params):
        """Fetches a list of all metering label rules for a label."""
        return self.list('metering_label_rules',
                         self.metering_label_rules_path, retrieve_all,
                         **_params)

    def show_metering_label_rule(self, metering_label_rule, **_params):
        """Fetches information of a certain metering label rule."""
        return self.get(self.metering_label_rule_path %
                        (metering_label_rule), params=_params)

    def create_rbac_policy(self, body=None):
        """Create a new RBAC policy."""
        return self.post(self.rbac_policies_path, body=body)

    def update_rbac_policy(self, rbac_policy_id, body=None):
        """Update a RBAC policy."""
        return self.put(self.rbac_policy_path % rbac_policy_id, body=body)

    def list_rbac_policies(self, retrieve_all=True, **_params):
        """Fetch a list of all RBAC policies for a project."""
        return self.list('rbac_policies', self.rbac_policies_path,
                         retrieve_all, **_params)

    def show_rbac_policy(self, rbac_policy_id, **_params):
        """Fetch information of a certain RBAC policy."""
        return self.get(self.rbac_policy_path % rbac_policy_id,
                        params=_params)

    def delete_rbac_policy(self, rbac_policy_id):
        """Delete the specified RBAC policy."""
        return self.delete(self.rbac_policy_path % rbac_policy_id)

    def list_qos_policies(self, retrieve_all=True, **_params):
        """Fetches a list of all qos policies for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('policies', self.qos_policies_path,
                         retrieve_all, **_params)

    def show_qos_policy(self, qos_policy, **_params):
        """Fetches information of a certain qos policy."""
        return self.get(self.qos_policy_path % qos_policy,
                        params=_params)

    def create_qos_policy(self, body=None):
        """Creates a new qos policy."""
        return self.post(self.qos_policies_path, body=body)

    def update_qos_policy(self, qos_policy, body=None):
        """Updates a qos policy."""
        return self.put(self.qos_policy_path % qos_policy,
                        body=body)

    def delete_qos_policy(self, qos_policy):
        """Deletes the specified qos policy."""
        return self.delete(self.qos_policy_path % qos_policy)

    def list_qos_rule_types(self, retrieve_all=True, **_params):
        """List available qos rule types."""
        return self.list('rule_types', self.qos_rule_types_path,
                         retrieve_all, **_params)

    def list_bandwidth_limit_rules(self, policy_id,
                                   retrieve_all=True, **_params):
        """Fetches a list of all bandwidth limit rules for the given policy."""
        return self.list('bandwidth_limit_rules',
                         self.qos_bandwidth_limit_rules_path % policy_id,
                         retrieve_all, **_params)

    def show_bandwidth_limit_rule(self, rule, policy, **_params):
        """Fetches information of a certain bandwidth limit rule."""
        return self.get(self.qos_bandwidth_limit_rule_path %
                        (policy, rule), params=_params)

    def create_bandwidth_limit_rule(self, policy, body=None):
        """Creates a new bandwidth limit rule."""
        return self.post(self.qos_bandwidth_limit_rules_path % policy,
                         body=body)

    def update_bandwidth_limit_rule(self, rule, policy, body=None):
        """Updates a bandwidth limit rule."""
        return self.put(self.qos_bandwidth_limit_rule_path %
                        (policy, rule), body=body)

    def delete_bandwidth_limit_rule(self, rule, policy):
        """Deletes a bandwidth limit rule."""
        return self.delete(self.qos_bandwidth_limit_rule_path %
                           (policy, rule))

    def list_dscp_marking_rules(self, policy_id,
                                retrieve_all=True, **_params):
        """Fetches a list of all DSCP marking rules for the given policy."""
        return self.list('dscp_marking_rules',
                         self.qos_dscp_marking_rules_path % policy_id,
                         retrieve_all, **_params)

    def show_dscp_marking_rule(self, rule, policy, **_params):
        """Shows information of a certain DSCP marking rule."""
        return self.get(self.qos_dscp_marking_rule_path %
                        (policy, rule), params=_params)

    def create_dscp_marking_rule(self, policy, body=None):
        """Creates a new DSCP marking rule."""
        return self.post(self.qos_dscp_marking_rules_path % policy,
                         body=body)

    def update_dscp_marking_rule(self, rule, policy, body=None):
        """Updates a DSCP marking rule."""
        return self.put(self.qos_dscp_marking_rule_path %
                        (policy, rule), body=body)

    def delete_dscp_marking_rule(self, rule, policy):
        """Deletes a DSCP marking rule."""
        return self.delete(self.qos_dscp_marking_rule_path %
                           (policy, rule))

    def list_minimum_bandwidth_rules(self, policy_id, retrieve_all=True,
                                     **_params):
        """Fetches a list of all minimum bandwidth rules for the given policy.

        """
        return self.list('minimum_bandwidth_rules',
                         self.qos_minimum_bandwidth_rules_path %
                         policy_id, retrieve_all, **_params)

    def show_minimum_bandwidth_rule(self, rule, policy, body=None):
        """Fetches information of a certain minimum bandwidth rule."""
        return self.get(self.qos_minimum_bandwidth_rule_path %
                        (policy, rule), body=body)

    def create_minimum_bandwidth_rule(self, policy, body=None):
        """Creates a new minimum bandwidth rule."""
        return self.post(self.qos_minimum_bandwidth_rules_path % policy,
                         body=body)

    def update_minimum_bandwidth_rule(self, rule, policy, body=None):
        """Updates a minimum bandwidth rule."""
        return self.put(self.qos_minimum_bandwidth_rule_path %
                        (policy, rule), body=body)

    def delete_minimum_bandwidth_rule(self, rule, policy):
        """Deletes a minimum bandwidth rule."""
        return self.delete(self.qos_minimum_bandwidth_rule_path %
                           (policy, rule))

    def create_flavor(self, body=None):
        """Creates a new Neutron service flavor."""
        return self.post(self.flavors_path, body=body)

    def delete_flavor(self, flavor):
        """Deletes the specified Neutron service flavor."""
        return self.delete(self.flavor_path % (flavor))

    def list_flavors(self, retrieve_all=True, **_params):
        """Fetches a list of all Neutron service flavors for a project."""
        return self.list('flavors', self.flavors_path, retrieve_all,
                         **_params)

    def show_flavor(self, flavor, **_params):
        """Fetches information for a certain Neutron service flavor."""
        return self.get(self.flavor_path % (flavor), params=_params)

    def update_flavor(self, flavor, body):
        """Update a Neutron service flavor."""
        return self.put(self.flavor_path % (flavor), body=body)

    def associate_flavor(self, flavor, body):
        """Associate a Neutron service flavor with a profile."""
        return self.post(self.flavor_profile_bindings_path %
                         (flavor), body=body)

    def disassociate_flavor(self, flavor, flavor_profile):
        """Disassociate a Neutron service flavor with a profile."""
        return self.delete(self.flavor_profile_binding_path %
                           (flavor, flavor_profile))

    def create_service_profile(self, body=None):
        """Creates a new Neutron service flavor profile."""
        return self.post(self.service_profiles_path, body=body)

    def delete_service_profile(self, flavor_profile):
        """Deletes the specified Neutron service flavor profile."""
        return self.delete(self.service_profile_path % (flavor_profile))

    def list_service_profiles(self, retrieve_all=True, **_params):
        """Fetches a list of all Neutron service flavor profiles."""
        return self.list('service_profiles', self.service_profiles_path,
                         retrieve_all, **_params)

    def show_service_profile(self, flavor_profile, **_params):
        """Fetches information for a certain Neutron service flavor profile."""
        return self.get(self.service_profile_path % (flavor_profile),
                        params=_params)

    def update_service_profile(self, service_profile, body):
        """Update a Neutron service profile."""
        return self.put(self.service_profile_path % (service_profile),
                        body=body)

    def list_availability_zones(self, retrieve_all=True, **_params):
        """Fetches a list of all availability zones."""
        return self.list('availability_zones', self.availability_zones_path,
                         retrieve_all, **_params)

    @debtcollector.renames.renamed_kwarg(
        'tenant_id', 'project_id', replace=True)
    def get_auto_allocated_topology(self, project_id, **_params):
        """Fetch information about a project's auto-allocated topology."""
        return self.get(
            self.auto_allocated_topology_path % project_id,
            params=_params)

    @debtcollector.renames.renamed_kwarg(
        'tenant_id', 'project_id', replace=True)
    def delete_auto_allocated_topology(self, project_id, **_params):
        """Delete a project's auto-allocated topology."""
        return self.delete(
            self.auto_allocated_topology_path % project_id,
            params=_params)

    @debtcollector.renames.renamed_kwarg(
        'tenant_id', 'project_id', replace=True)
    def validate_auto_allocated_topology_requirements(self, project_id):
        """Validate requirements for getting an auto-allocated topology."""
        return self.get_auto_allocated_topology(project_id, fields=['dry-run'])

    def list_bgp_speakers(self, retrieve_all=True, **_params):
        """Fetches a list of all BGP speakers for a project."""
        return self.list('bgp_speakers', self.bgp_speakers_path, retrieve_all,
                         **_params)

    def show_bgp_speaker(self, bgp_speaker_id, **_params):
        """Fetches information of a certain BGP speaker."""
        return self.get(self.bgp_speaker_path % (bgp_speaker_id),
                        params=_params)

    def create_bgp_speaker(self, body=None):
        """Creates a new BGP speaker."""
        return self.post(self.bgp_speakers_path, body=body)

    def update_bgp_speaker(self, bgp_speaker_id, body=None):
        """Update a BGP speaker."""
        return self.put(self.bgp_speaker_path % bgp_speaker_id, body=body)

    def delete_bgp_speaker(self, speaker_id):
        """Deletes the specified BGP speaker."""
        return self.delete(self.bgp_speaker_path % (speaker_id))

    def add_peer_to_bgp_speaker(self, speaker_id, body=None):
        """Adds a peer to BGP speaker."""
        return self.put((self.bgp_speaker_path % speaker_id) +
                        "/add_bgp_peer", body=body)

    def remove_peer_from_bgp_speaker(self, speaker_id, body=None):
        """Removes a peer from BGP speaker."""
        return self.put((self.bgp_speaker_path % speaker_id) +
                        "/remove_bgp_peer", body=body)

    def add_network_to_bgp_speaker(self, speaker_id, body=None):
        """Adds a network to BGP speaker."""
        return self.put((self.bgp_speaker_path % speaker_id) +
                        "/add_gateway_network", body=body)

    def remove_network_from_bgp_speaker(self, speaker_id, body=None):
        """Removes a network from BGP speaker."""
        return self.put((self.bgp_speaker_path % speaker_id) +
                        "/remove_gateway_network", body=body)

    def list_route_advertised_from_bgp_speaker(self, speaker_id, **_params):
        """Fetches a list of all routes advertised by BGP speaker."""
        return self.get((self.bgp_speaker_path % speaker_id) +
                        "/get_advertised_routes", params=_params)

    def list_bgp_peers(self, **_params):
        """Fetches a list of all BGP peers."""
        return self.get(self.bgp_peers_path, params=_params)

    def show_bgp_peer(self, peer_id, **_params):
        """Fetches information of a certain BGP peer."""
        return self.get(self.bgp_peer_path % peer_id,
                        params=_params)

    def create_bgp_peer(self, body=None):
        """Create a new BGP peer."""
        return self.post(self.bgp_peers_path, body=body)

    def update_bgp_peer(self, bgp_peer_id, body=None):
        """Update a BGP peer."""
        return self.put(self.bgp_peer_path % bgp_peer_id, body=body)

    def delete_bgp_peer(self, peer_id):
        """Deletes the specified BGP peer."""
        return self.delete(self.bgp_peer_path % peer_id)

    def list_network_ip_availabilities(self, retrieve_all=True, **_params):
        """Fetches IP availability information for all networks"""
        return self.list('network_ip_availabilities',
                         self.network_ip_availabilities_path,
                         retrieve_all, **_params)

    def show_network_ip_availability(self, network, **_params):
        """Fetches IP availability information for a specified network"""
        return self.get(self.network_ip_availability_path % (network),
                        params=_params)

    def add_tag(self, resource_type, resource_id, tag, **_params):
        """Add a tag on the resource."""
        return self.put(self.tag_path % (resource_type, resource_id, tag))

    def replace_tag(self, resource_type, resource_id, body, **_params):
        """Replace tags on the resource."""
        return self.put(self.tags_path % (resource_type, resource_id), body)

    def remove_tag(self, resource_type, resource_id, tag, **_params):
        """Remove a tag on the resource."""
        return self.delete(self.tag_path % (resource_type, resource_id, tag))

    def remove_tag_all(self, resource_type, resource_id, **_params):
        """Remove all tags on the resource."""
        return self.delete(self.tags_path % (resource_type, resource_id))

    def create_trunk(self, body=None):
        """Create a trunk port."""
        return self.post(self.trunks_path, body=body)

    def update_trunk(self, trunk, body=None):
        """Update a trunk port."""
        return self.put(self.trunk_path % trunk, body=body)

    def delete_trunk(self, trunk):
        """Delete a trunk port."""
        return self.delete(self.trunk_path % (trunk))

    def list_trunks(self, retrieve_all=True, **_params):
        """Fetch a list of all trunk ports."""
        return self.list('trunks', self.trunks_path, retrieve_all,
                         **_params)

    def show_trunk(self, trunk, **_params):
        """Fetch information for a certain trunk port."""
        return self.get(self.trunk_path % (trunk), params=_params)

    def trunk_add_subports(self, trunk, body=None):
        """Add specified subports to the trunk."""
        return self.put(self.subports_add_path % (trunk), body=body)

    def trunk_remove_subports(self, trunk, body=None):
        """Removes specified subports from the trunk."""
        return self.put(self.subports_remove_path % (trunk), body=body)

    def trunk_get_subports(self, trunk, **_params):
        """Fetch a list of all subports attached to given trunk."""
        return self.get(self.subports_path % (trunk), params=_params)

    def list_bgpvpns(self, retrieve_all=True, **_params):
        """Fetches a list of all BGP VPNs for a project"""
        return self.list('bgpvpns', self.bgpvpns_path, retrieve_all, **_params)

    def show_bgpvpn(self, bgpvpn, **_params):
        """Fetches information of a certain BGP VPN"""
        return self.get(self.bgpvpn_path % bgpvpn, params=_params)

    def create_bgpvpn(self, body=None):
        """Creates a new BGP VPN"""
        return self.post(self.bgpvpns_path, body=body)

    def update_bgpvpn(self, bgpvpn, body=None):
        """Updates a BGP VPN"""
        return self.put(self.bgpvpn_path % bgpvpn, body=body)

    def delete_bgpvpn(self, bgpvpn):
        """Deletes the specified BGP VPN"""
        return self.delete(self.bgpvpn_path % bgpvpn)

    def list_bgpvpn_network_assocs(self, bgpvpn, retrieve_all=True, **_params):
        """Fetches a list of network associations for a given BGP VPN."""
        return self.list('network_associations',
                         self.bgpvpn_network_associations_path % bgpvpn,
                         retrieve_all, **_params)

    def show_bgpvpn_network_assoc(self, bgpvpn, net_assoc, **_params):
        """Fetches information of a certain BGP VPN's network association"""
        return self.get(
            self.bgpvpn_network_association_path % (bgpvpn, net_assoc),
            params=_params)

    def create_bgpvpn_network_assoc(self, bgpvpn, body=None):
        """Creates a new BGP VPN network association"""
        return self.post(self.bgpvpn_network_associations_path % bgpvpn,
                         body=body)

    def update_bgpvpn_network_assoc(self, bgpvpn, net_assoc, body=None):
        """Updates a BGP VPN network association"""
        return self.put(
            self.bgpvpn_network_association_path % (bgpvpn, net_assoc),
            body=body)

    def delete_bgpvpn_network_assoc(self, bgpvpn, net_assoc):
        """Deletes the specified BGP VPN network association"""
        return self.delete(
            self.bgpvpn_network_association_path % (bgpvpn, net_assoc))

    def list_bgpvpn_router_assocs(self, bgpvpn, retrieve_all=True, **_params):
        """Fetches a list of router associations for a given BGP VPN."""
        return self.list('router_associations',
                         self.bgpvpn_router_associations_path % bgpvpn,
                         retrieve_all, **_params)

    def show_bgpvpn_router_assoc(self, bgpvpn, router_assoc, **_params):
        """Fetches information of a certain BGP VPN's router association"""
        return self.get(
            self.bgpvpn_router_association_path % (bgpvpn, router_assoc),
            params=_params)

    def create_bgpvpn_router_assoc(self, bgpvpn, body=None):
        """Creates a new BGP VPN router association"""
        return self.post(self.bgpvpn_router_associations_path % bgpvpn,
                         body=body)

    def update_bgpvpn_router_assoc(self, bgpvpn, router_assoc, body=None):
        """Updates a BGP VPN router association"""
        return self.put(
            self.bgpvpn_router_association_path % (bgpvpn, router_assoc),
            body=body)

    def delete_bgpvpn_router_assoc(self, bgpvpn, router_assoc):
        """Deletes the specified BGP VPN router association"""
        return self.delete(
            self.bgpvpn_router_association_path % (bgpvpn, router_assoc))

    def create_port_pair(self, body=None):
        """Creates a new Port Pair."""
        return self.post(self.sfc_port_pairs_path, body=body)

    def update_port_pair(self, port_pair, body=None):
        """Update a Port Pair."""
        return self.put(self.sfc_port_pair_path % port_pair, body=body)

    def delete_port_pair(self, port_pair):
        """Deletes the specified Port Pair."""
        return self.delete(self.sfc_port_pair_path % (port_pair))

    def list_port_pair(self, retrieve_all=True, **_params):
        """Fetches a list of all Port Pairs."""
        return self.list('port_pairs', self.sfc_port_pairs_path, retrieve_all,
                         **_params)

    def show_port_pair(self, port_pair, **_params):
        """Fetches information of a certain Port Pair."""
        return self.get(self.sfc_port_pair_path % (port_pair), params=_params)

    def create_port_pair_group(self, body=None):
        """Creates a new Port Pair Group."""
        return self.post(self.sfc_port_pair_groups_path, body=body)

    def update_port_pair_group(self, port_pair_group, body=None):
        """Update a Port Pair Group."""
        return self.put(self.sfc_port_pair_group_path % port_pair_group,
                        body=body)

    def delete_port_pair_group(self, port_pair_group):
        """Deletes the specified Port Pair Group."""
        return self.delete(self.sfc_port_pair_group_path % (port_pair_group))

    def list_port_pair_group(self, retrieve_all=True, **_params):
        """Fetches a list of all Port Pair Groups."""
        return self.list('port_pair_groups', self.sfc_port_pair_groups_path,
                         retrieve_all, **_params)

    def show_port_pair_group(self, port_pair_group, **_params):
        """Fetches information of a certain Port Pair Group."""
        return self.get(self.sfc_port_pair_group_path % (port_pair_group),
                        params=_params)

    def create_port_chain(self, body=None):
        """Creates a new Port Chain."""
        return self.post(self.sfc_port_chains_path, body=body)

    def update_port_chain(self, port_chain, body=None):
        """Update a Port Chain."""
        return self.put(self.sfc_port_chain_path % port_chain, body=body)

    def delete_port_chain(self, port_chain):
        """Deletes the specified Port Chain."""
        return self.delete(self.sfc_port_chain_path % (port_chain))

    def list_port_chain(self, retrieve_all=True, **_params):
        """Fetches a list of all Port Chains."""
        return self.list('port_chains', self.sfc_port_chains_path,
                         retrieve_all, **_params)

    def show_port_chain(self, port_chain, **_params):
        """Fetches information of a certain Port Chain."""
        return self.get(self.sfc_port_chain_path % (port_chain),
                        params=_params)

    def create_flow_classifier(self, body=None):
        """Creates a new Flow Classifier."""
        return self.post(self.sfc_flow_classifiers_path, body=body)

    def update_flow_classifier(self, flow_classifier, body=None):
        """Update a Flow Classifier."""
        return self.put(self.sfc_flow_classifier_path % flow_classifier,
                        body=body)

    def delete_flow_classifier(self, flow_classifier):
        """Deletes the specified Flow Classifier."""
        return self.delete(self.sfc_flow_classifier_path % (flow_classifier))

    def list_flow_classifier(self, retrieve_all=True, **_params):
        """Fetches a list of all Flow Classifiers."""
        return self.list('flow_classifiers', self.sfc_flow_classifiers_path,
                         retrieve_all, **_params)

    def show_flow_classifier(self, flow_classifier, **_params):
        """Fetches information of a certain Flow Classifier."""
        return self.get(self.sfc_flow_classifier_path % (flow_classifier),
                        params=_params)

    def extend_show(self, resource_singular, path, parent_resource):
        def _fx(obj, **_params):
            return self.show_ext(path, obj, **_params)

        def _parent_fx(obj, parent_id, **_params):
            return self.show_ext(path % parent_id, obj, **_params)
        fn = _fx if not parent_resource else _parent_fx
        setattr(self, "show_%s" % resource_singular, fn)

    def extend_list(self, resource_plural, path, parent_resource):
        def _fx(retrieve_all=True, **_params):
            return self.list_ext(resource_plural, path,
                                 retrieve_all, **_params)

        def _parent_fx(parent_id, retrieve_all=True, **_params):
            return self.list_ext(resource_plural, path % parent_id,
                                 retrieve_all, **_params)
        fn = _fx if not parent_resource else _parent_fx
        setattr(self, "list_%s" % resource_plural, fn)

    def extend_create(self, resource_singular, path, parent_resource):
        def _fx(body=None):
            return self.create_ext(path, body)

        def _parent_fx(parent_id, body=None):
            return self.create_ext(path % parent_id, body)
        fn = _fx if not parent_resource else _parent_fx
        setattr(self, "create_%s" % resource_singular, fn)

    def extend_delete(self, resource_singular, path, parent_resource):
        def _fx(obj):
            return self.delete_ext(path, obj)

        def _parent_fx(obj, parent_id):
            return self.delete_ext(path % parent_id, obj)
        fn = _fx if not parent_resource else _parent_fx
        setattr(self, "delete_%s" % resource_singular, fn)

    def extend_update(self, resource_singular, path, parent_resource):
        def _fx(obj, body=None):
            return self.update_ext(path, obj, body)

        def _parent_fx(obj, parent_id, body=None):
            return self.update_ext(path % parent_id, obj, body)
        fn = _fx if not parent_resource else _parent_fx
        setattr(self, "update_%s" % resource_singular, fn)

    def _extend_client_with_module(self, module, version):
        classes = inspect.getmembers(module, inspect.isclass)
        for cls_name, cls in classes:
            if hasattr(cls, 'versions'):
                if version not in cls.versions:
                    continue
            parent_resource = getattr(cls, 'parent_resource', None)
            if issubclass(cls, client_extension.ClientExtensionList):
                self.extend_list(cls.resource_plural, cls.object_path,
                                 parent_resource)
            elif issubclass(cls, client_extension.ClientExtensionCreate):
                self.extend_create(cls.resource, cls.object_path,
                                   parent_resource)
            elif issubclass(cls, client_extension.ClientExtensionUpdate):
                self.extend_update(cls.resource, cls.resource_path,
                                   parent_resource)
            elif issubclass(cls, client_extension.ClientExtensionDelete):
                self.extend_delete(cls.resource, cls.resource_path,
                                   parent_resource)
            elif issubclass(cls, client_extension.ClientExtensionShow):
                self.extend_show(cls.resource, cls.resource_path,
                                 parent_resource)
            elif issubclass(cls, client_extension.NeutronClientExtension):
                setattr(self, "%s_path" % cls.resource_plural,
                        cls.object_path)
                setattr(self, "%s_path" % cls.resource, cls.resource_path)
                self.EXTED_PLURALS.update({cls.resource_plural: cls.resource})

    def _register_extensions(self, version):
        for name, module in itertools.chain(
                client_extension._discover_via_entry_points()):
            self._extend_client_with_module(module, version)

    def ipStr(self,ip):
        return ('%d.'*4)[:-1] % tuple(map(lambda x:x%256, (ip>>24, ip>>16, ip>>8, ip)))

    def ipInt(self, ip):
        ip = list(map(int, ip.split('.')))
        return (ip[0] << 24) + (ip[1] << 16) + (ip[2] << 8) + ip[3]

    def ipAdd(self, ip=None, A=0, B=0, C=0, D=0):
        if ip == '' or ip == None:
            return self.ipStr((A<<24) + (B<<16) + (C<<8) + D)
        return self.ipStr(self.ipInt(ip) + (A<<24) + (B<<16) + (C<<8) + D)

    def create_scan_default_device(self, ip_address, topo_id, auth_info, timeout, retries=1):
        """
        :param ip_address:
        :param topo_id:
        :param auth_info: {"username":"xx","password":"xx", "port": 443, "schema": "https"}, type dict
        :param timeout:
        :param retries:
        :return:
        """
        manage_conf_info = {
            "https": {
                "username": auth_info.get("username", "admin"),
                "password": auth_info.get("password", "passok"),
                "port":  auth_info.get("port", 443),
                "schema": auth_info.get("schema", "https")
            },
            "ssh": {
                "username": "root",
                "password": "passok",
                "port": 22
            }
        }
        manage_conf_info_str = encrypt(json.dumps(manage_conf_info))
        create_kwargs = {
            "device": {
                "name": "scan_device",
                "username": auth_info.get("username", "admin"),
                "password": auth_info.get("password", "passok"),
                "ip_address": ip_address,
                "topo_id": topo_id,
                "update_offset": True,
                "time_out": str(timeout),
                "manage_conf_info": manage_conf_info_str
            }
        }
        try:
            return self.create_device(body=create_kwargs)
        except exceptions.ConnectionFailed:
            if retries > 0:
                time.sleep(4)
                return self.create_scan_default_device(ip_address, topo_id, auth_info, timeout,
                                                       retries=retries-1)
            return {
                "device": {}
            }
        except exceptions.Conflict as ex:
            # Handle license limit devices add
            ex_info = ex.error_info
            err_msg = ex.message.rsplit("\n", 1)[0] if isinstance(ex.message, basestring) else ""
            ret = {
                "error_code": ex_info.get("error_code"),
                "message": err_msg,
                "device": ex_info.get("current_device")
            }
            return ret

    @staticmethod
    def notify_frontend(routing_key=None, msg=None, exchange="neutron", exchange_type="topic"):
        """
            Send a message to rabbitMQ
        :param routing_key: The routing_key of a exchange
        :param msg: A message that needs to be notified
        :param exchange: The exchange of rabbitMQ
        :param exchange_type: The type of exchange
        :return:
        """
        from oslo_config import cfg
        from neutron.common import config
        from pika import URLParameters
        from pika import BlockingConnection

        args = ['--config-file', '/etc/neutron/neutron.conf']
        config.init(args)

        connection = BlockingConnection(URLParameters(cfg.CONF.transport_url))
        channel = connection.channel()
        channel.exchange_declare(
            exchange=exchange,
            exchange_type=exchange_type
        )
        channel.basic_publish(
            exchange=exchange,
            routing_key=routing_key,
            body=json.dumps(msg) if isinstance(msg, dict) or isinstance(msg, list) else msg
        )
        connection.close()

    def scan_ip_and_create_device(self, ip_list, topo_id, auth_info, timeout=15, task_id="", pre_added=[]):
        """
        :param ip_list:
        :param topo_id:
        :param auth_info: <list>, {"username": "xx", "password": "xx", "port": 443, "schema": "https"}
        :param timeout:
        :param task_id:
        :return:
        """
        if isinstance(auth_info, list):
            # To deal with the problem that the admin account is locked
            auth_info.reverse()

        pre_added_ips = map(lambda x:x.get("ip_address"), pre_added)
        # Reduplication of IP Address
        scan_ips = list(set(ip for ip in ip_list if ip) - set(pre_added_ips))
        success_ping = {
            "total_num": len(scan_ips) + len(pre_added_ips),
            "recent_num": 0,
            # "in_db_devices": [],
            # "not_in_db_devices": [],
            # "exist_devices": [],
            # "license_limit_statistics": {},
            "device_statistics": {
                "license_limit_statistics": {}
            }
        }

        # pre execute for pre added server
        if len(pre_added_ips) > 0:
            for device in pre_added:
                curr = success_ping["device_statistics"].get(device.get("device_type"), 0)
                success_ping["device_statistics"].update({device.get("device_type"): curr + 1})

                msg = {
                    "device": {
                        "id": device.get("id", ""),
                        "name": device.get("name"),
                        "device_type": device.get("device_type"),
                        "ip_address": device.get("ip_address"),
                        "if_new_added": True,
                        "is_license_limited": False
                    },
                    "task_id": task_id
                }
                success_ping["recent_num"] += 1
                msg.update(success_ping)
                self.notify_frontend(routing_key="scan.device", msg=msg)

        # one by one

        devices = self.list_devices().get("devices")
        exist_device_mappings = {}
        for device in devices:
            if device.get("ip_address") not in pre_added_ips:
                exist_device_mappings.update({
                    device.get("ip_address"): {
                        "id": device.get("id"),
                        "name": device.get("name"),
                        "device_type": device.get("device_type"),
                        "ip_address": device.get("ip_address"),
                        "if_new_added": False
                    }
                })

        threads = []
        for ip_address in scan_ips:
            thr = threading.Thread(
                target=self.inform_scan_statistics,
                args=(ip_address, success_ping, topo_id, auth_info, exist_device_mappings, timeout, task_id)
            )
            threads.append(thr)
        for thr in threads:
            thr.start()

    def handle_license_limit(self, msg, timeout):
        name = msg["device"].get("name")
        device_id = msg["device"].get("id")
        ip_address = msg["device"].get("ip_address")
        cloud_node_devices = self.list_devices(device_type="CloudNode").get("devices")
        device_kwargs = {
            "device": {
                "name": "CloudNode_{}".format(len(cloud_node_devices)),
                "username": "",
                "password": "",
                "device_type": "CloudNode",
                "ip_address": "",
                "topo_id": "",
                "update_offset": True,
                "time_out": "",
                "manage_conf_info": ""
            }
        }
        try:
            ret = self.create_device(body=device_kwargs)
        except exceptions.Conflict as ex:
            # Handle license limit devices add
            self.delete_device(device_id)
            ex_info = ex.error_info
            err_msg = ex.message.rsplit("\n", 1)[0] if isinstance(ex.message, basestring) else ""
            return {
                "is_license_limited": True,
                "error_code": ex_info.get("error_code"),
                "message": err_msg
            }
        server_host_id = ret["device"].get("id")
        instance_kwargs = {
            "vnfc_instance": {
                "name": name,
                "password": "ubuntu",
                "username": "ubuntu",
                "device_type": "Tapplet",
                "server_host": server_host_id,
                "device_id": device_id,
                "ips": json.dumps([ip_address]),
                "port": json.dumps([]),
                "instance_id": ""
            }
        }
        _logger.info(instance_kwargs)
        self.create_vnfc_instance(body=instance_kwargs)
        update_kwargs = {
            "device": {
                "update_offset": True,
                "time_out": str(timeout)
            }
        }
        self.update_device(device_id, update_kwargs)
        return {}

    def inform_scan_statistics(self, ip, success_ping, topo_id, auth_infos, exist_device_mappings, timeout, task_id):
        """Create the device based on the IP and notify the front end of the statistics
        :param ip:
        :param success_ping:
        :param topo_id:
        :param auth_infos:
        :param exist_device_mappings:
        :param timeout:
        :param task_id:
        :return:
        """
        import socket

        create_result, device_type = {}, "unreachable"
        msg = {
            "device": {},
            "task_id": task_id
        }
        if ip in exist_device_mappings.keys():
            msg["device"].update(exist_device_mappings.get(ip))
            device_type = exist_device_mappings[ip].get("device_type")
        else:
            # More different authentication information is sent to create the device request
            for auth_info in auth_infos:
                # Port check
                port = auth_info.get("port", 443)
                try:
                    sk = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sk.settimeout(3)
                    sk.connect((ip, int(port)))
                    sk.close()
                except BaseException as e:
                    continue

                create_result = self.create_scan_default_device(ip, topo_id, auth_info, timeout)
                device_type = create_result.get("device").get("device_type", "unreachable")
                if device_type not in ["unreachable", "device_unknown"]:
                    msg["device"].update({
                        "id": create_result.get("device").get("id", ""),
                        "name": create_result.get("device").get("name"),
                        "device_type": create_result.get("device").get("device_type"),
                        "ip_address": create_result.get("device").get("ip_address"),
                        "if_new_added": True,
                        "is_license_limited": False
                    })
                    # License handle
                    if create_result.get("error_code"):
                        msg["device"].update({
                            "is_license_limited": True,
                            "error_code": create_result.get("error_code"),
                            "message": create_result.get("message")
                        })
                    break
        with self.lock:
            if msg["device"].get("is_license_limited"):
                success_ping["device_statistics"]["license_limit_statistics"].update({
                    device_type: success_ping["device_statistics"]["license_limit_statistics"].get(device_type, 0) + 1
                })
                # PF9032_limited
                # The device found during the scan but not recorded, because of license limit
                # success_ping["not_in_db_devices"].append(msg["device"])
            else:
                # No license limit statistics
                # device_statistics, device_type in ["unreachable", "device_unknown", "PF9032", ...]
                success_ping["device_statistics"].update({
                    device_type: success_ping["device_statistics"].get(device_type, 0) + 1
                })
            # if msg["device"].get("if_new_added") \
            #         and not msg["device"].get("is_license_limited"):
            #     # New records added during scanning
            #     success_ping["in_db_devices"].append(msg["device"])
            # elif msg.get("device") and not msg["device"].get("if_new_added") \
            #         and not msg["device"].get("is_license_limited"):
            #     # The device found during the scan has been recorded
            #     success_ping["exist_devices"].append(msg["device"])

            success_ping["recent_num"] += 1
            msg.update(success_ping)
            if "Tapplet" == device_type \
                    and msg["device"].get("if_new_added") \
                    and not msg["device"].get("is_license_limited"):
                ret = self.handle_license_limit(msg, timeout)
                msg["device"].update(ret)
            self.notify_frontend(routing_key="scan.device", msg=msg)
            if success_ping["recent_num"] == success_ping["total_num"]:
                # Notifies the front end that the current scan devices has been done
                msg.pop("device", "")
                self.notify_frontend(routing_key="scan.device.done", msg=msg)

        # Create the interfaces and links of new added device
        # if create_result.get("device").get("if_new_added"):
        #     device_id = create_result.get("device").get("id")
        #     update_kwargs = {
        #         "device": {
        #             "update_offset": True,
        #             "time_out": str(self.timeout)
        #         }
        #     }
        #     self.update_device(device_id, update_kwargs)

    def update_topology_bulk(self, topology_ids, is_preprocess=False, task_id=""):
        """Update the topology in bulk and notify the message to the front end
        :param topology_ids: <list> List of ids for the topology
        :param is_preprocess: <bool> If is true, the device will be preprocessed;
                                     otherwise, it will not be preprocessed
        :param task_id: <str>
        :return:
        """
        topologies = self.list_topos().get("topos")
        ids = [topology.get("id") for topology in topologies]
        exist_topology_ids = list(set(topology_ids) & set(ids))
        update_kwargs = {
            "topo": {
                "update_offset": True,
                "is_preprocess": is_preprocess
            }
        }
        msg = {
            "total_num": len(exist_topology_ids),
            "recent_num": 0,
            "task_id": task_id
        }
        for topology_id in exist_topology_ids:
            self.update_topo(topology_id, update_kwargs)
            msg["recent_num"] += 1
            # Notifies the front end that the topology has been updated
            self.notify_frontend(routing_key="scan.topo", msg=msg)
        # Notifies the front end that the current update topologies has been done
        self.notify_frontend(routing_key="scan.topo.done", msg={
            "task_id": task_id
        })

    def format_export_flow(self, flow_info):
        print flow_info
        for key,value in flow_info.items():
            if value == "" or value is None or key == "status":
                flow_info.pop(key)
        return flow_info

    def get_export_flow(self, file_name):
        try:
            flow_list = self.list_flows()
            service_network_list = self.list_service_networks()
            
            flow_list.update({"service_networks":map(self.format_export_flow, \
                service_network_list.get("service_networks"))})
                
            flow_list.update({"flows":map(self.format_export_flow, flow_list.get("flows"))})
            with open(file_name, "w") as json_file:
                json.dump(flow_list, json_file)
        except BaseException:
            return False
        else:
            return flow_list

    # def get_json_file(self, file_name):
    #     with open(file_name, "r") as json_file:
    #         flow_dict = json.load(json_file)
    #     flow_list = flow_dict.get("flows")
    #     if not flow_list:
    #         raise exceptions.JsonFileNotCorrect(file_name=file_name)
    #     else:
    #         return flow_list

    # def post_import_flow(self, file_name):
    #     flow_list = self.get_json_file(file_name)
    #     success_list = []
    #     failed_list = []
    #     if not flow_list:
    #         raise exceptions.JsonFileNotCorrect(file_name="test")
    #     for i in flow_list:
    #         try:
    #             create_result = self.create_flow(body = {"flow":i})
    #         except exceptions.InternalServerError:
    #             failed_list.append(i)
    #         except exceptions.NotFound:
    #             failed_list.append(i)
    #         else:                    
    #             success_list.append(create_result) 
    #     return success_list,failed_list

    def format_body(self, config, if_strip_none = True, strip_list = None, change_value_map = None):
        config_info = copy.deepcopy(config)
        for key,value in config_info.items():
            if if_strip_none:
                if value == "" or value is None :
                    config_info.pop(key)
            if strip_list:
                for i in strip_list:
                    if key == i:
                        config_info.pop(key)
            if change_value_map:
                if value in change_value_map.keys():
                    config_info.update({key:change_value_map.get(value)})
        return config_info

    def post_import_flow(self, file_name):

        with open(file_name, "r") as json_file:
            config_dict = json.load(json_file)
        
        service_network_map = {}
        for i in config_dict.get("service_networks"):
            result = self.create_service_network(\
                body = {"service_networks":[self.format_body(i, strip_list=["id"])]})
            service_network_map.update({i.get("id"):result.get("service_networks")[0].get("id")})
        
        flow_list = config_dict.get("flows")
        for i in range(len(flow_list)):
            flow_list[i] = self.format_body(flow_list[i], strip_list=["id"], change_value_map = service_network_map)

        create_flow_result = self.create_flow(body =  {"flows":flow_list})
        return create_flow_result
        # return self.create_service_network(\
        #     body = {"service_networks":config_dict.get("service_networks")}), \
        #     self.create_flow(body = {"flows":config_dict.get("flows")})

    def create_vnfc_instance_device(self, body=None):
        """Create a new vnfc_instance entry in DB,
                if the type of vnfc_instance is tapplet ,
                add the instance to devices table."""
        instance_body = body.get("vnfc_instance")
        if instance_body.get("device_type") in ["Tapplet"]:
            _body = {
                "device": {
                    "name": instance_body.get("name", ''),
                    "update_offset": True,
                    "device_type": instance_body.get("device_type"),
                    "link_state": "up",
                    "service_state": True,
                    "admin_state": True
                }
            }
            manage_conf_info = {
                "https": {
                    "username": "admin",
                    "password": "passok",
                    "port": 443
                },
                "ssh": {
                    "username": "ubuntu",
                    "password": "ubuntu",
                    "port": 22
            }}
            manage_conf_info_str = encrypt(json.dumps(manage_conf_info))
            _body["device"].update({"manage_conf_info": manage_conf_info_str})
            if instance_body.get("ips") and instance_body.get("ips") != "[]":
                ips = json.loads(instance_body.get("ips"))
                ip_address = ""
                for ip in ips:
                    result = os.system('ping -c 2 {} >>/dev/null'.format(ip))
                    if not result:
                        ip_address = ip
                        break
                _body["device"].update({"ip_address": ip_address})
            device = self.create_device(body=_body)
            self.notify_frontend(routing_key="scan.tapplet.create", msg={
                "device_id": device.get("device").get("id")
            })
            body["vnfc_instance"]["device_id"] = device.get("device").get("id")
        return self.create_vnfc_instance(body=body)

    def add_vnfc_instance_port(self, vnfc_instance_id, port=None):
        """add a port to vnfc_instance(Tapplet or VM)"""
        _body = self.show_vnfc_instance(vnfc_instance_id)
        ports = json.loads(_body.get("vnfc_instance").get("port"))
        if port and port not in ports:
            ports.append(port)
        _body["vnfc_instance"]["port"] = json.dumps(ports)
        _body["vnfc_instance"].pop("id")
        return self.update_vnfc_instance(vnfc_instance_id, body=_body)

    def update_license_infos(self, license_infos):
        licence_infos = self.list_resources(
            app_name="licence",
            key_name="licence_infos"
        ).get("resources")
        if licence_infos:
            resource_id = licence_infos[0].get("id")
            update_kwargs = {
                "resource": {
                    "value": license_infos
                }
            }
            self.update_resource(resource_id, update_kwargs)
        else:
            create_kwargs = {
                "resource": {
                    "app_name": "licence",
                    "key_name": "licence_infos",
                    "value": license_infos,
                    "desc": "",
                    "resource_id": ""
                }
            }
            self.create_resource(create_kwargs)

    def get_license_infos(self):
        licence_infos = self.list_resources(
            app_name = "licence",
            key_name = "licence_infos"
        ).get("resources")
        return licence_infos[0].get("value") if licence_infos else ""

    def delete_license_infos(self):
        licence_infos = self.list_resources(
            app_name = "licence",
            key_name = "licence_infos"
        ).get("resources")
        if licence_infos:
            resource_id = licence_infos[0].get("id")
            self.delete_resource(resource_id)
