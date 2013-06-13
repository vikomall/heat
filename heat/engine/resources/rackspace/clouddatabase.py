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

from heat.engine import resource
from heat.engine import clients
from heat.engine import resource
from heat.common import exception
from heat.engine.resources.rackspace import rackspace_resource
import exceptions as exc

from heat.openstack.common import log as logging

logger = logging.getLogger(__name__)


class CloudDBInstance(rackspace_resource.RackspaceResource):
    database_schema = {
        "character_set": {
            "Type": "String",
            "Default": "utf8",
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
            "Type":"String",
            "Required": False
        },
        "host": {
            "Type": "String",
            "Default": "%"
        },
        "databases": {
            "Type": "List",
            "Required": False
        }
    }

    properties_schema = {
        "InstanceName": {
            "Type": "String",
            "Required": True
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
    
        
    def __init__(self, name, json_snippet, stack):
        super(CloudDBInstance, self).__init__(name, json_snippet, stack)

    def handle_create(self):
        logger.debug("CloudDatabase handle_create called")
        self.sqlinstancename = self.properties['InstanceName'] 
        self.flavor = self.properties['FlavorRef']
        self.volume = self.properties['VolumeSize']
        
        self.databases  = []
        self.databases = self.properties['Databases']
        self.users = []
        self.users = self.properties['Users']

        # create db instance
        logger.info("Creating could db instance %s" % self.sqlinstancename)
        instance = self.cloud_db().create(self.sqlinstancename, 
                                          flavor=self.flavor, 
                                          volume=self.volume)
        if instance is not None:
            self.resource_id_set(instance.id)
 
        return instance

    def check_create_complete(self, instance):
        if instance.status != 'ACTIVE':
            instance.get()

        if instance.status == 'ERROR':
            logger.debug("ERROR: Cloud DB instance creation failed.")
            raise Exception("Cloud DB instance creation failed.")

        if instance.status != 'ACTIVE':
            return False

        logger.info("SQL instance %s created (flavor:%s, volume:%s)" % 
                     (self.sqlinstancename, self.flavor, self.volume))
        try:
            # create databases
            for database in self.databases:
                instance.create_database(database['name'],
                                    character_set=database['character_set'],
                                    collate=database['collate'])
                logger.info("Database %s created on SQL instance %s" %
                            (database['name'], self.sqlinstancename))
    
            # add users
            dbs = []
            for user in self.users:
                if user['databases']:
                    dbs = user['databases']                
                instance.create_user(user['name'], user['password'], dbs)
                logger.info("Database user %s created successfully" %
                            (user['name']))

            return True
        except Exception as ex:
            logger.debug("ERROR: exception %s" % ex)
            raise ex

    def handle_delete(self):
        logger.debug("CloudDatabase handle_delete called")
        if self.resource_id is None:
            logger.debug("resourc_id is null and returning without delete")
            return

        sqlinstancename = self.properties['InstanceName']
        instances = self.cloud_db().list()
        if not instances:
            logger.debug("Cloud DB instance % not found" % sqlinstancename)
            return

        for pos, inst in enumerate(instances):
            if inst.id == self.resource_id:
                inst.delete()
                logger.info("Cloud DB instance deleted(id:%s)" % self.resource_id)
                return

    def FnGetAtt(self, key):
        raise NotImplementedError("Update not implemented for Resource %s"
                                  % type(self))
        
    def FnGetRefId(self):
        raise NotImplementedError("Update not implemented for Resource %s" % 
                                  type(self))

def resource_mapping():
    return {
        'Rackspace::Cloud::DBInstance': CloudDBInstance,
    }