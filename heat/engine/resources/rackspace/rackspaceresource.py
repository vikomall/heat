from heat.engine import resource
from heat.engine import clients
from heat.engine import resource
from heat.common import exception

from heat.openstack.common import log as logging
import pyrax

logger = logging.getLogger(__name__)


class RackspaceResource(resource.Resource):
        
    def __init__(self, name, json_snippet, stack):
        super(RackspaceResource, self).__init__(name, json_snippet, stack)
        self.pyrax = pyrax

    def authenticate(self):
        print "/////////////////// Going to authenticate \\\\\\\\\\\\\\\\\\\\\\"
        #self.rsusername = self.properties['RackspaceUserName']
        #self.rsapikey =  self.properties['RackspaceApiKey']
        cls = self.pyrax.utils.import_class('pyrax.identity.rax_identity.RaxIdentity')
        self.pyrax.identity = cls()
        self.pyrax.set_credentials(self.context.user, self.context.passowrd) #(self.rsusername, self.rsapikey)
        self.cdb = self.pyrax.cloud_databases
        print "//////////////Authentication completed successfully\\\\\\\\\\\\\\\\"
        
    def handle_update(self, json_snipped=None):
        return self.UPDATE_REPLACE