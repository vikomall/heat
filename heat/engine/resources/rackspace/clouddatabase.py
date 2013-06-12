from heat.engine import resource
from heat.engine import clients
from heat.engine import resource
from heat.common import exception
from heat.engine.resources.rackspace import rackspaceresoure
import exceptions as exc

from heat.openstack.common import log as logging

logger = logging.getLogger(__name__)


class CloudDatabase(rackspaceresource.RackspaceResource):
    database_schema = {
        "character_set": {
            "Type": "String",
            "Default":" utf8",
            "Required": False
        },
        "collate": {
            "Type":"String",
            "Default": "utf8_general_ci",
            "Required":False
        },
        "name": {
            "Type":"String",
            "Required": False
        }
    }
    
    user_schema = {
        "name": {
            "Type":"String",
            "Required": False
        },
        "password": {
            "NoEcho": True,
            "Type":"String",
            "Required": False
        },
        "host": {
            "Type": "String",
            "Default": "%"
        },
        "databases": {
            'Type': 'List',
            'Required': False
        }
    }

    properties_schema = {
        "InstanceName": {
            "Type": "String",
            "Required": True,
            "MaxLength": 255
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
        
        #"DBName": {
            #"Type": "String",
            #"Required": False
        #},

        #"DBUserName":{
            #"Type":"String",
            #"Required": False
        #},
        
        #"DBPassword":{
            #"Type":"String",
            #"Required":True            
        #},
        
        "RackspaceUserName":{
            "Type":"String",
            "Required":True
        },

        "RackspaceApiKey":{
            "Type":"String",
            "Required":True
        },

        "Databases": {
            'Type': 'List',
            'Required': False,
            'Schema': {
                'Type': 'Map',
                'Schema': database_schema
            }
        },        

        "Users": {
            'Type': 'List',
            'Required': False,
            'Schema': {
                'Type': 'Map',
                'Schema': user_schema
            }
        },
    }
    #check schema with cloud db API reference
    
        
    def __init__(self, name, json_snippet, stack):
        super(CloudDatabase, self).__init__(name, json_snippet, stack)
        print "============CLOUDDBInstance-INIT=================="

    def handle_create(self):       
        def dbinstancecallback(instance):
            print "/////////////////CREATE-complete-callback-BEGIN////////////////////////"
            # create database
            dbs = [dbname]
            instance.create_database(dbname)

             #add users to database
            instance.create_user(dbusername, dbpassword, dbs)
            logger.debug("SUCCESS: Cloud database %s created" % instance.name)            
            print "//////////////////CREATE-complete-callback-END////////////////////////"
            self.create_complete = True

        print "//////////////handle-create////////////////"
        sqlinstancename = self.properties['SQLInstanceName'] 
        flavor = self.properties['FlavorRef']
        volume = self.properties['VolumeSize']
        self.dbname  = self.properties['DBName']
        self.dbusername = self.properties['DBUserName']
        self.dbpassword = self.properties['DBPassword']
        self.rsusername = self.properties['RackspaceUserName']
        self.rsapikey =  self.properties['RackspaceApiKey']
        
        import pdb
        pdb.set_trace()
        #self.authenticate()

        # create db instance
        self.create_complete = False
        #cdb = self.pyrax.cloud_databases
        logger.debug("Creating could db instance %s" % instance.name)
        instance =  self.cloud_db.create(sqlinstancename, flavor=flavor, volume=volume)
        if instance is not None:
            self.resource_id_set(instance.id)
        print "Name:", instance.name
        print "ID:", instance.id
        print "Status:", instance.status
        print "Flavor:", instance.flavor.name
        print "Volume:", instance.volume.size
        
        self.pyrax.utils.wait_until(instance, 
                               "status", 
                               ["ACTIVE", "ERROR"], 
                               callback=dbinstancecallback, 
                               interval=5,
                               verbose=True,
                               verbose_atts="progress")

        return instance

    def check_create_complete(self, cookie):
        if self.create_complete == True:
            return True
        else:
            return False

        #instance  = cookie

        #if instance.status != 'ACTIVE':
            #return False            
        #return True

    def handle_delete(self):
        print 
        print "*******************handle-delete*******************"
        print "context:", self.stack.context
        if self.resource_id is None:
            print "resourc_id is null and returning without delete"
            return

        import pdb
        pdb.set_trace()
        logger.debug("Deleting cloud database %s" % instance.name)        
        sqlinstancename = self.properties['InstanceName'] 
        rsusername = self.properties['RackspaceUserName']
        rsapikey =  self.properties['RackspaceApiKey']

        #self.authenticate()
        #cdb = self.pyrax.cloud_databases
        instances = self.cloud_db.list()
        if not instances:
            logger.debug("ERROR: Cloud instance '%d' was not found." % self.resource_id)
            return

        for pos, inst in enumerate(instances):
            if inst.id == self.resource_id:
                inst.delete()
                logger.debug("SUCCESS: Deleted sql instance %d" % self.resource_id)                
                print " %s" % sqlinstancename
                return
        print "***************************delete-end*********************"
        logger.debug("ERROR: Cloud instance '%d' was not found" % self.resource_id)


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
        'Rackspace': CloudDBInstance,
    }