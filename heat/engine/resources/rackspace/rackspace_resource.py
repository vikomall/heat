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

import pyrax
from heat.engine import resource
from heat.openstack.common import log as logging

logger = logging.getLogger(__name__)


class RackspaceResource(resource.Resource):
    '''
    Common base class for Rackspace Resource Providers
    '''
    properties_schema = {}
    
    def __init__(self, name, json_snippet, stack):
        super(RackspaceResource, self).__init__(name, json_snippet, stack)
        self.pyrax = pyrax
        self._cloud_db = None
        self._cloud_dns = None
        self._cloud_lb = None
        self._cloud_server = None
        self._cloud_nw = None
        self._cloud_blockstore = None

    def cloud_db(self):
        '''Rackspace cloud database client.'''      
        if self._cloud_db:
            return self._cloud_db
        
        self.__authenticate()
        self._cloud_db = self.pyrax.cloud_databases
        return self._cloud_db
    
    def cloud_lb(self):
        '''Rackspace cloud loadbalancer client.'''        
        if self._cloud_lb:
            return self._cloud_lb
        
        self.__authenticate()
        self._cloud_lb = self.pyrax.cloud_loadbalancers
        return self._cloud_lb

    def cloud_dns(self):
        '''Rackspace cloud dns client'''        
        if self._cloud_dns:
            return self._cloud_dns
         
        self._cloud_dns = self.pyrax.cloud_dns
        return self._cloud_dns
   
    def nova(self):
        '''Rackspace nova client.'''
        if self._cloud_server:
            return self._cloud_server
         
        self.__authenticate()
        self._cloud_server = self.pyrax.cloudservers
        return self._cloud_server

    def cinder(self):
        '''Rackspace cinder client.'''
        if self._cloud_blockstore:
            return self._cloud_blockstore
        
        self.__authenticate()
        self._cloud_blockstore = self.pyrax.cloud_blockstorage
        return self._cloud_blockstore

    def quantum(self):
        '''Rackspace quantum client.'''
        if self._cloud_nw:
            return self._cloud_nw
        
        self.__authenticate()
        self._cloud_nw = self.pyrax.cloud_networks
        return self._cloud_nw

    def __authenticate(self):
        cls = self.pyrax.utils.import_class('pyrax.identity.rax_identity.RaxIdentity')
        self.pyrax.identity = cls()
        logger.info("Authenticating with username:%s" % self.context.username)
        self.pyrax.set_credentials(self.context.username, 
                                   password=self.context.password)
        logger.info("User %s authenticated successfully."
                    % self.context.username)
        