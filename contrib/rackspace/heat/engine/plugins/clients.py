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

from oslo.config import cfg

from heat.openstack.common import log as logging

logger = logging.getLogger(__name__)

from heat.common import exception
from heat.engine import clients
from heat.openstack.common.gettextutils import _

try:
    import pyrax
except ImportError:
    logger.info('pyrax not available')

try:
    from swiftclient import client as swiftclient
except ImportError:
    swiftclient = None
    logger.info('swiftclient not available')
try:
    from ceilometerclient.v2 import client as ceilometerclient
except ImportError:
    ceilometerclient = None
    logger.info('ceilometerclient not available')

cloud_opts = [
    cfg.StrOpt('region_name',
               default=None,
               help=_('Region for connecting to services'))
]
cfg.CONF.register_opts(cloud_opts)


class Clients(clients.OpenStackClients):
    '''
    Convenience class to create and cache client instances.
    '''
    def __init__(self, context):
        super(Clients, self).__init__(context)
        self.pyrax = None

    def _get_client(self, name):
        if not self.pyrax:
            self.__authenticate()
        return self.pyrax.get(name)

    def auto_scale(self):
        """Rackspace Auto Scale client."""
        return self._get_client("autoscale")

    def cloud_db(self):
        '''Rackspace cloud database client.'''
        return self._get_client("database")

    def cloud_lb(self):
        '''Rackspace cloud loadbalancer client.'''
        return self._get_client("load_balancer")

    def cloud_dns(self):
        '''Rackspace cloud dns client.'''
        return self._get_client("dns")

    def nova(self, service_type="compute"):
        '''Rackspace cloudservers client. Specifying the service type is to
        maintain compatability with clients.OpenStackClients. It is not
        actually a valid option to change within pyrax.
        '''
        if service_type is not "compute":
            raise ValueError("service_type should be compute.")
        return self._get_client(service_type)

    def neutron(self):
        '''Rackspace neutron client.'''
        return self._get_client("network")

    def __authenticate(self):
        # current implemenation shown below authenticates using
        # username and password. Need make it work with auth-token
        pyrax.set_setting("identity_type", "keystone")
        pyrax.set_setting("auth_endpoint", self.context.auth_url)
        logger.info("Authenticating with username:%s" %
                    self.context.username)
        self.pyrax = pyrax.auth_with_token(self.context.auth_token,
                                           tenant_id=self.context.tenant_id,
                                           tenant_name=self.context.tenant,
                                           region=(cfg.CONF.region_name
                                                   or None))
        if not self.pyrax:
            raise exception.AuthorizationFailure("No services available.")
        logger.info("User %s authenticated successfully."
                    % self.context.username)
