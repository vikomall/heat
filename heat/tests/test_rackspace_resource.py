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


from heat.common import context
from heat.common import exception
from heat.common import template_format
from heat.engine import parser
from heat.engine import resource
from heat.engine import scheduler
from heat.openstack.common import uuidutils

from heat.tests import generic_resource as generic_rsrc
from heat.tests.common import HeatTestCase
from heat.tests.utils import setup_dummy_db
from heat.engine.resources.rackspace.rackspace_resource import RackspaceResource
import pyrax

test_template = '''
{
  "AWSTemplateFormatVersion" : "2010-09-09",
  "Description" : "Rackspace resource",
  "Parameters" : {
    "Name" : {
      "Description" : "Name",
      "Type" : "String",
      "Default" : "test"
    }
  },
  "Resources" : {
    "WebServer": {
      "Type": "Rackspace::Cloud::DBInstance",
      "Properties": {
        "Name"        : "test"
      }
    }
  }
}
'''
class RackspaceResourceTest(HeatTestCase):
    def setUp(self):
        super(RackspaceResourceTest, self).setUp()
        setup_dummy_db()        
        self.stack_name = "test_stack"
        self.stack = parser.Stack(None,
                             self.stack_name,
                             parser.Template({}),
                             stack_id=uuidutils.generate_uuid())

    def test_class_new_ok(self):
        rr = RackspaceResource(self.stack_name, {}, self.stack)
        self.assertEqual(rr._cloud_db, None)

    def test_resource_auth_fail(self):
        ctx = context.get_admin_context()
        self.m.StubOutWithMock(ctx, 'username')
        self.m.StubOutWithMock(ctx, 'password')
        ctx.username = 'randomusername'
        ctx.password = 'randompasswd'
        self.stack = parser.Stack(ctx, self.stack_name, parser.Template({}))
        rr = RackspaceResource(self.stack_name, {}, self.stack)
        self.m.ReplayAll()
        self.assertRaises(pyrax.exc.AuthenticationFailed,
                          rr.cloud_db ) 

    def test_resource_auth_success(self):
        ctx = context.get_admin_context()
        self.m.StubOutWithMock(ctx, 'username')
        self.m.StubOutWithMock(ctx, 'password')
        ctx.username = 'randomusername'
        ctx.password = 'randompasswd'
        self.stack = parser.Stack(ctx, self.stack_name, parser.Template({}))
        rr = RackspaceResource(self.stack_name, {}, self.stack)
        self.m.ReplayAll()
        self.assertRaises(pyrax.exc.AuthenticationFailed,
                          rr.cloud_db ) 

    def test_cloud_db_client_ok(self):
        stack_name = "test_stack"
        stack = parser.Stack(None, stack_name, parser.Template({}),
                                          stack_id=uuidutils.generate_uuid())
        rr = RackspaceResource(stack_name, {}, stack)
        
        self.m.StubOutWithMock(rr, "__authenticate")
        rr.__authenticate(rr.self).AndReturn(None)
        self.assertEqual(rr._cloud_db, None)

