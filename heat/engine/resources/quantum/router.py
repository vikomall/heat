# vim: tabstop=4 shiftwidth=4 softtabstop=4

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

from heat.engine import clients
from heat.engine.resources.quantum import quantum

if clients.quantumclient is not None:
    from quantumclient.common.exceptions import QuantumClientException

from heat.openstack.common import log as logging

logger = logging.getLogger(__name__)


class Router(quantum.QuantumResource):
    properties_schema = {'name': {'Type': 'String'},
                         'value_specs': {'Type': 'Map',
                                         'Default': {}},
                         'admin_state_up': {'Type': 'Boolean',
                                            'Default': True}}
    attributes_schema = {
        "status": "the status of the router",
        "external_gateway_info": "gateway network for the router",
        "name": "friendly name of the router",
        "admin_state_up": "administrative state of the router",
        "tenant_id": "tenant owning the router",
        "id": "unique identifier for the router"
    }

    def handle_create(self):
        props = self.prepare_properties(
            self.properties,
            self.physical_resource_name())
        router = self.quantum().create_router({'router': props})['router']
        self.resource_id_set(router['id'])

    def _show_resource(self):
        return self.quantum().show_router(
            self.resource_id)['router']

    def check_create_complete(self, *args):
        attributes = self._show_resource()
        return self.is_built(attributes)

    def handle_delete(self):
        client = self.quantum()
        try:
            client.delete_router(self.resource_id)
        except QuantumClientException as ex:
            if ex.status_code != 404:
                raise ex


class RouterInterface(quantum.QuantumResource):
    properties_schema = {'router_id': {'Type': 'String',
                                       'Required': True},
                         'subnet_id': {'Type': 'String',
                                       'Required': True}}

    def handle_create(self):
        router_id = self.properties.get('router_id')
        subnet_id = self.properties.get('subnet_id')
        self.quantum().add_interface_router(
            router_id,
            {'subnet_id': subnet_id})
        self.resource_id_set('%s:%s' % (router_id, subnet_id))

    def handle_delete(self):
        client = self.quantum()
        (router_id, subnet_id) = self.resource_id.split(':')
        try:
            client.remove_interface_router(
                router_id,
                {'subnet_id': subnet_id})
        except QuantumClientException as ex:
            if ex.status_code != 404:
                raise ex


class RouterGateway(quantum.QuantumResource):
    properties_schema = {'router_id': {'Type': 'String',
                                       'Required': True},
                         'network_id': {'Type': 'String',
                                        'Required': True}}

    def handle_create(self):
        router_id = self.properties.get('router_id')
        network_id = self.properties.get('network_id')
        self.quantum().add_gateway_router(
            router_id,
            {'network_id': network_id})
        self.resource_id_set('%s:%s' % (router_id, network_id))

    def handle_delete(self):
        client = self.quantum()
        (router_id, network_id) = self.resource_id.split(':')
        try:
            client.remove_gateway_router(router_id)
        except QuantumClientException as ex:
            if ex.status_code != 404:
                raise ex


def resource_mapping():
    if clients.quantumclient is None:
        return {}

    return {
        'OS::Quantum::Router': Router,
        'OS::Quantum::RouterInterface': RouterInterface,
        'OS::Quantum::RouterGateway': RouterGateway,
    }
