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
import time
import logging
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
        except Exception:
            error_info.update({"error_code": status_code})
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
    """Client for the AFC API."""

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
    devices_path = "/devices"
    device_path = "/devices/%s"
    neutron_create_network_path = "/devices/%s/neutron_create_network"
    neutron_delete_network_path = "/devices/%s/neutron_delete_network"
    neutron_create_subnet_path = "/devices/%s/neutron_create_subnet"
    neutron_delete_subnet_path = "/devices/%s/neutron_delete_subnet"
    neutron_create_router_path = "/devices/%s/neutron_create_router"
    neutron_delete_router_path = "/devices/%s/neutron_delete_router"
    neutron_create_router_interface_path = \
        "/devices/%s/neutron_create_router_interface"
    neutron_delete_router_interface_path = \
        "/devices/%s/neutron_delete_router_interface"

    def list_devices(self, retrieve_all=True, **_params):
        """Fetches a list of all devices for a project."""
        # Pass filters in "params" argument to do_request
        return self.list('devices', self.devices_path, retrieve_all,
                         **_params)

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
