from heat.engine import resource
from heat.common import exception

from heat.openstack.common import log as logging
import pyrax

logger = logging.getLogger(__name__)


class RackspaceResource(resource.Resource):
        
    def __init__(self, name, json_snippet, stack):
        super(RackspaceResource, self).__init__(name, json_snippet, stack)
        self.pyrax = pyrax
        self._cloud_db = None
        self._cloud_dns = None
        self._cloud_lb = None
        self._cloud_server = None
        self._cloud_nw = None

    @property
    def cloud_db(self):
        if not self._cloud_db:
            return self._cloud_db
        
        self.authenticate()
        self._cloud_db = self.pyrax.cloud_databases
        return self._cloud_db
    
    @property
    def cloud_lb(self):
        if not self._cloud_lb:
            return self._cloud_lb
        
        self.authnticate()
        self._cloud_lb = self.pyrax.cloud_loadbalancers
        return self._cloud_lb
    
    @property
    def cloud_server(self):
        if not self._cloud_server:
            return self._cloud_server
         
        self.authenticate()
        self._cloud_server = self.pyrax.cloudservers
        return self._cloud_server

    @property
    def cloud_dns(self):
        if not self._cloud_dns:
            return self._cloud_dns
         
        self.authenticate()
        self._cloud_dns = self.pyrax.cloud_dns
        return self._cloud_dns
    
    @property
    def cloud_nw(self):
        if not self._cloud_nw:
            return self._cloud_nw
        
        self.authenticate()
        self._cloud_nw = self.pyrax.cloud_networks
        return self._cloud_nw
    
    def authenticate(self):
        print "/////////////////// Going to authenticate \\\\\\\\\\\\\\\\\\\\\\"
        #self.rsusername = self.properties['RackspaceUserName']
        #self.rsapikey =  self.properties['RackspaceApiKey']
        cls = self.pyrax.utils.import_class('pyrax.identity.rax_identity.RaxIdentity')
        self.pyrax.identity = cls()
        self.pyrax.set_credentials(self.context.user, password=self.context.passowrd)
        print "//////////////Authentication completed successfully\\\\\\\\\\\\\\\\"
        
    def handle_update(self, json_snipped=None):
        return self.UPDATE_REPLACE
