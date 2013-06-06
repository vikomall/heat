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
        import pdb
        pdb.set_trace()
        self.username = stack.context.username
        self.password = stack.context.password
        print "============RackspaceResource-INIT=================="
        print "json snippet:", json_snippet
        print "stack:", stack
        print "resource_id:", self.resource_id
        print "stack-context", stack.context
        print "===============RackspaceResource-INIT-done==========================="

    def authenticate(self):
        #pyrax.set_setting("identity_type", "keystone")
        #pyrax.set_credentials(
        print "/////////////////// Going to authenticate \\\\\\\\\\\\\\\\\\\\\\"
        self.pyrax = pyrax
        cls = pyrax.utils.import_class('pyrax.identity.rax_identity.RaxIdentity')
        pyrax.identity = cls()
        pyrax.set_credentials(self.username, password=self.password)
        self.cdb = pyrax.cloud_databases
        print "//////////////Authentication completed successfully\\\\\\\\\\\\\\\\"
        return True
        
    def handle_update(self, json_snipped=None):
        print 
        print "******handle-update****"
        print "context:", self.stack.context
        print "***********************"
        print 

        return self.UPDATE_REPLACE