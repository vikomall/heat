from heat.engine import resource
from heat.engine import clients
from heat.engine import resource
from heat.common import exception
from heat.engine.resources import rackspaceresource
import exceptions as exc

from heat.openstack.common import log as logging
#import pyrax

logger = logging.getLogger(__name__)


class CloudDBInstance(rackspaceresource.RackspaceResource):
    #database_schema = {
        #"character_set": {
            #"Type":"String",
            #"Default":"utf8",
            #"Required":False
        #},
        #"collate": {
            #"Type":"String",
            #"Default":"utf8_general_ci",
            #"Required":False
        #},
        #"name": {
            #"Type":"String",
            #"Required":False
        #}
    #}
    
    #user_schema = {
        #"name": {
            #"Type":"String",
            #"Required":False
        #},
        #"password": {
            #"Type":"String",
            #"Required":False
        #},
        #"host": {
            #"Type": "String",
            #"Default": "%"
        #},
        #"databases": {
            #'Type': 'List',
            #'Schema': {
                #'Type': 'Map',
                #'Schema': database_schema
            #}
        #}        
    #}

    properties_schema = {
        "SQLInstanceName": {
            "Type":"String",
            "Required":True
        },

        "FlavorRef": {
            "Type":"String",
            "Required":True
        },

        "VolumeSize": {
            "Type":"Number",
            "MinValue":1,
            "MaxValue":150,
            "Required":True
        },
        
        "DBName": {
            "Type": "String",
            "Required": True
        },

        "DBUserName":{
            "Type":"String",
            "Required":True            
        },
        
        "DBPassword":{
            "Type":"String",
            "Required":True            
        },
        
        "RackspaceUserName":{
            "Type":"String",
            "Required":True
        },

        "RackspaceApiKey":{
            "Type":"String",
            "Required":True
        },

        #"databases": {
            #'Type': 'List',
            #'Schema': {
                #'Type': 'Map',
                #'Schema': database_schema
            #}
        #},        

        #"users": {
            #'Type': 'List',
            #'Schema': {
                #'Type': 'Map',
                #'Schema': user_schema
            #}
        #},
    }
        
    def __init__(self, name, json_snippet, stack):
        super(CloudDBInstance, self).__init__(name, json_snippet, stack)
        print "============CLOUDDBInstance-INIT=================="
        #print "json snippet:", json_snippet
        #print "stack:", stack
        #print "resource_id:", self.resource_id
        #print "stack-context", stack.context
        #print "=========================================="

    def handle_create(self):       
        def dbinstancecallback(instance):
            print "/////////////////CREATE-complete-callback-BEGIN////////////////////////"
            print "SQL db-instance status:", instance.status
            # create database
            dbs = [dbname]
            instance.create_database(dbname)

             #add users to database
            instance.create_user(dbusername, dbpassword, dbs)
            print "//////////////////CREATE-complete-callback-END////////////////////////"

        print "//////////////handle-create////////////////"
        sqlinstancename = self.properties['SQLInstanceName'] 
        flavor = self.properties['FlavorRef']
        volume = self.properties['VolumeSize']
        dbname  = self.properties['DBName']
        dbusername = self.properties['DBUserName']
        dbpassword = self.properties['DBPassword']
        rsusername = self.properties['RackspaceUserName']
        rsapikey =  self.properties['RackspaceApiKey']
        
        import pdb
        pdb.set_trace()
        self.authenticate()
        # authenticate
        # authenticate with Rackspace cloud credentials
        #if not self.RackspaceCloudAuthentication(rsapikey, rsusername):
            #print "Rackspace cloud authentication failed."
            #return

        # create db instance
        cdb = self.pyrax.cloud_databases
        instance = cdb.create(sqlinstancename, flavor=flavor, volume=volume)
        print "Name:", instance.name
        print "ID:", instance.id
        print "Status:", instance.status
        print "Flavor:", instance.flavor.name
        print "Volume:", instance.volume.size
        
        pyrax.utils.wait_until(instance, 
                               "status", 
                               ["ACTIVE", "ERROR"], 
                               callback=dbinstancecallback, 
                               interval=5,
                               verbose=True,
                               verbose_atts="progress")

        self.state_set(self.CREATE_IN_PROGRESS)
        return instance

    def handle_delete(self):
        print 
        print "*******************handle-delete*******************"
        print "context:", self.stack.context
        import pdb
        pdb.set_trace()
        sqlinstancename = self.properties['SQLInstanceName'] 
        rsusername = self.properties['RackspaceUserName']
        rsapikey =  self.properties['RackspaceApiKey']

        # authenticate with Rackspace cloud credentials
        #if not self.RackspaceCloudAuthentication(rsapikey, rsusername):
            #print "Rackspace cloud authentication failed."
            #return

        self.authenticate()
        cdb = self.pyrax.cloud_databases
        instances = cdb.list()
        if not instances:
            print "ERROR: Cloud instance '%s' was not found." % sqlinstancename
            return

        for pos, inst in enumerate(instances):
            if inst.name == sqlinstancename:
                inst.delete()
                print "SUCCESS: Successfully deleted sql instance: %s" % sqlinstancename
                return
        print "***************************delete-end*********************"
        print "ERROR: Cloud instance '%s' was not found." % sqlinstancename

    #def RackspaceCloudAuthentication(self, rsapikey, rsusername):
        #cls = pyrax.utils.import_class('pyrax.identity.rax_identity.RaxIdentity')
        #pyrax.identity = cls()
        #pyrax.set_credentials(rsusername, rsapikey)
        #self.cdb = pyrax.cloud_databases
        #return True

    def validate(self):
        print 
        print "*****handle-validate****"
        print "context:", self.stack.context
        print "***********************"
        print
        pass
        #raise NotImplementedError("Update not implemented for Resource %s"
                                  #% type(self))
        
    def FnGetAtt(self, key):
        raise NotImplementedError("Update not implemented for Resource %s"
                                  % type(self))
        
    def FnGetRefId(self):
        raise NotImplementedError("Update not implemented for Resource %s" % 
                                  type(self))

def resource_mapping():
    print "*****resource-mapping********"
    return {
        'AWS::EC2::CloudDBInstance': CloudDBInstance,
    }