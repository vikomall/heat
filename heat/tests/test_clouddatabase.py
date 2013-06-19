# vim: tabstop=4 shiftwidth=4 softtabstop=4

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


from heat.engine.resources.rackspace import clouddatabase
from heat.common import exception
from heat.common import template_format
from heat.engine import parser
from heat.openstack.common import uuidutils
from heat.tests.common import HeatTestCase
from heat.tests.utils import setup_dummy_db


wp_template = '''
{
  "AWSTemplateFormatVersion" : "2010-09-09",
  "Description" : "MYSQL instance running on Rackspace cloud",
  "Parameters" : {
    "FlavorRef": {
      "Description" : "Flavor reference",
      "Type": "String"
    },
    "VolumeSize": {
      "Description" : "The volume size",
      "Type": "Number",
      "MinValue" : "1",
      "MaxValue" : "1024"
    },
    "InstanceName": {
      "Description" : "The database instance name",
      "Type": "String"
    }
  },
  "Resources" : {
    "MySqlCloudDB": {
      "Type": "Rackspace::Cloud::DBInstance",
      "Properties" : {
        "InstanceName" : {"testsqlinstance"},
        "FlavorRef" : {"test-flavor"},
        "VolumeSize" : {"test-volume-size"},
        "Users" : [{"name":"testuser", "password":"testpass123"}] ,
        "Databases" : [{"name":"testdbonetwo"}]
      }
    }
  }

}
'''


class FakeDBInstance(object):
    def __init__(self):
        self.id = 12345
        self.hostname = "testhost"
        self.links = \
            [{"href": "https://adga23dd432a.rackspacecloud.com/132345245"}]
        self.resource_id = 12345


class CloudDBInstanceTest(HeatTestCase):
    def setUp(self):
        super(CloudDBInstanceTest, self).setUp()
        setup_dummy_db()

    def _setup_test_clouddbinstance(self, name):
        stack_name = '%s_stack' % name
        t = template_format.parse(wp_template)
        template = parser.Template(t)
        params = parser.Parameters(stack_name, template, {'KeyName': 'test'})
        stack = parser.Stack(None, stack_name, template, params,
                             stack_id=uuidutils.generate_uuid())

        t['Resources']['MySqlCloudDB']['Properties']['InstanceName'] = 'Test'
        t['Resources']['MySqlCloudDB']['Properties']['FlavorRef'] = '1GB'
        t['Resources']['MySqlCloudDB']['Properties']['VolumeSize'] = '30'
        instance = clouddatabase.CloudDBInstance(
            '%s_name' % name,
            t['Resources']['MySqlCloudDB'],
            stack)
        instance.resource_id = 1234
        self.m.StubOutWithMock(instance, 'cloud_db')
        return instance

    def test_clouddbinstance(self):
        instance = self._setup_test_clouddbinstance('dbinstance')
        self.assertEqual(instance.hostname, None)
        self.assertEqual(instance.href, None)

    def test_clouddbinstance_create(self):
        instance = self._setup_test_clouddbinstance('dbinstance_create')
        fake_client = self.m.CreateMockAnything()
        instance.cloud_db().AndReturn(fake_client)
        fakedbinstance = FakeDBInstance()
        fake_client.create('Test',
                           flavor='1GB',
                           volume='30').AndReturn(fakedbinstance)
        self.m.ReplayAll()
        instance.handle_create()
        expected_hostname = fakedbinstance.hostname
        expected_href = fakedbinstance.links[0]['href']
        self.assertEqual(instance.FnGetAtt('hostname'), expected_hostname)
        self.assertEqual(instance.FnGetAtt('href'), expected_href)
        self.m.VerifyAll()

    def test_clouddbinstance_delete_resource_notfound(self):
        instance = self._setup_test_clouddbinstance('dbinstance_delete')
        instance.resource_id = None
        self.m.ReplayAll()
        self.assertRaises(exception.ResourceNotFound, instance.handle_delete)
        self.m.VerifyAll()

    def test_clouddbinstance_delete(self):
        instance = self._setup_test_clouddbinstance('dbinstance_delete')
        fake_client = self.m.CreateMockAnything()
        cloud_db = instance.cloud_db().AndReturn(fake_client)
        fakedbinstance = FakeDBInstance()
        fake_client.delete(1234).AndReturn(None)
        self.m.ReplayAll()
        instance.handle_delete()
        self.m.VerifyAll()
