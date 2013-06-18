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


import mox

from oslo.config import cfg
from heat.tests import fakes
from heat.tests.common import HeatTestCase
from heat.tests.utils import setup_dummy_db
from heat.tests.utils import stack_delete_after

from heat.common import identifier
from heat.common import template_format
from heat.engine import parser
from heat.engine import scheduler
from heat.engine import service
from heat.engine.resources import instance
from heat.common import context
from heat.engine.resources import wait_condition as wc


test_template_metadata = '''
{
  "AWSTemplateFormatVersion" : "2010-09-09",
  "Description" : "",
  "Parameters" : {
    "KeyName" : {"Type" : "String", "Default": "mine" },
  },
  "Resources" : {
    "S1": {
      "Type": "AWS::EC2::Instance",
      "Metadata" : {
        "AWS::CloudFormation::Init" : {
          "config" : {
            "files" : {
              "/tmp/random_file" : {
                "content" : { "Fn::Join" : ["", [
                  "s2-ip=", {"Fn::GetAtt": ["S2", "PublicIp"]}
                ]]},
                "mode"    : "000400",
                "owner"   : "root",
                "group"   : "root"
              }
            }
          }
        }
      },
      "Properties": {
        "ImageId"      : "a",
        "InstanceType" : "m1.large",
        "KeyName"      : { "Ref" : "KeyName" },
        "UserData"     : "#!/bin/bash -v\n"
      }
    },
    "S2": {
      "Type": "AWS::EC2::Instance",
      "Properties": {
        "ImageId"      : "a",
        "InstanceType" : "m1.large",
        "KeyName"      : { "Ref" : "KeyName" },
        "UserData"     : "#!/bin/bash -v\n"
      }
    }
  }
}
'''

test_template_waitcondition = '''
{
  "AWSTemplateFormatVersion" : "2010-09-09",
  "Description" : "Just a WaitCondition.",
  "Parameters" : {
    "KeyName" : {"Type" : "String", "Default": "mine" },
  },
  "Resources" : {
    "WH" : {
      "Type" : "AWS::CloudFormation::WaitConditionHandle"
    },
    "S1": {
      "Type": "AWS::EC2::Instance",
      "Properties": {
        "ImageId"      : "a",
        "InstanceType" : "m1.large",
        "KeyName"      : { "Ref" : "KeyName" },
        "UserData"     : { "Fn::Join" : [ "", [ "#!/bin/bash -v\n",
                                                "echo ",
                                                { "Ref" : "WH" },
                                                "\n" ] ] }
      }
    },
    "WC" : {
      "Type" : "AWS::CloudFormation::WaitCondition",
      "DependsOn": "S1",
      "Properties" : {
        "Handle" : {"Ref" : "WH"},
        "Timeout" : "5"
      }
    },
    "S2": {
      "Type": "AWS::EC2::Instance",
      "Metadata" : {
        "test" : {"Fn::GetAtt": ["WC", "Data"]}
      },
      "Properties": {
        "ImageId"      : "a",
        "InstanceType" : "m1.large",
        "KeyName"      : { "Ref" : "KeyName" },
        "UserData"     : "#!/bin/bash -v\n"
      }
    }
  }
}
'''


class MetadataRefreshTest(HeatTestCase):
    '''
    The point of the test is to confirm that metadata gets updated
    when FnGetAtt() returns something different.
    gets called.
    '''
    def setUp(self):
        super(MetadataRefreshTest, self).setUp()
        self.m.StubOutWithMock(scheduler.TaskRunner, '_sleep')
        self.fc = fakes.FakeKeystoneClient()
        setup_dummy_db()

    # Note tests creating a stack should be decorated with @stack_delete_after
    # to ensure the stack is properly cleaned up
    def create_stack(self, stack_name='test_stack', params={}):
        temp = template_format.parse(test_template_metadata)
        template = parser.Template(temp)
        parameters = parser.Parameters(stack_name, template, params)
        ctx = context.get_admin_context()
        ctx.tenant_id = 'test_tenant'
        stack = parser.Stack(ctx, stack_name, template, parameters,
                             disable_rollback=True)

        self.stack_id = stack.store()

        self.m.StubOutWithMock(instance.Instance, 'handle_create')
        self.m.StubOutWithMock(instance.Instance, 'check_create_complete')
        for cookie in (object(), object()):
            instance.Instance.handle_create().AndReturn(cookie)
            create_complete = instance.Instance.check_create_complete(cookie)
            create_complete.InAnyOrder().AndReturn(True)
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        self.m.StubOutWithMock(instance.Instance, 'FnGetAtt')

        return stack

    @stack_delete_after
    def test_FnGetAtt(self):
        self.stack = self.create_stack()

        instance.Instance.FnGetAtt('PublicIp').AndReturn('1.2.3.5')

        # called by metadata_update()
        instance.Instance.FnGetAtt('PublicIp').AndReturn('10.0.0.5')

        self.m.ReplayAll()
        self.stack.create()

        self.assertEqual(self.stack.state, self.stack.CREATE_COMPLETE)

        s1 = self.stack.resources['S1']
        s2 = self.stack.resources['S2']
        files = s1.metadata['AWS::CloudFormation::Init']['config']['files']
        cont = files['/tmp/random_file']['content']
        self.assertEqual((s2.CREATE, s2.COMPLETE), s2.state)
        self.assertEqual(cont, 's2-ip=1.2.3.5')

        s1.metadata_update()
        s2.metadata_update()
        files = s1.metadata['AWS::CloudFormation::Init']['config']['files']
        cont = files['/tmp/random_file']['content']
        self.assertEqual(cont, 's2-ip=10.0.0.5')

        self.m.VerifyAll()


class WaitCondMetadataUpdateTest(HeatTestCase):
    def setUp(self):
        super(WaitCondMetadataUpdateTest, self).setUp()
        setup_dummy_db()
        self.ctx = context.get_admin_context()
        self.ctx.tenant_id = 'test_tenant'
        self.fc = fakes.FakeKeystoneClient()
        self.man = service.EngineService('a-host', 'a-topic')
        cfg.CONF.set_default('heat_waitcondition_server_url',
                             'http://127.0.0.1:8000/v1/waitcondition')

    # Note tests creating a stack should be decorated with @stack_delete_after
    # to ensure the stack is properly cleaned up
    def create_stack(self, stack_name='test_stack'):
        temp = template_format.parse(test_template_waitcondition)
        template = parser.Template(temp)
        parameters = parser.Parameters(stack_name, template, {})
        stack = parser.Stack(self.ctx, stack_name, template, parameters,
                             disable_rollback=True)

        self.stack_id = stack.store()

        self.m.StubOutWithMock(instance.Instance, 'handle_create')
        self.m.StubOutWithMock(instance.Instance, 'check_create_complete')
        for cookie in (object(), object()):
            instance.Instance.handle_create().AndReturn(cookie)
            instance.Instance.check_create_complete(cookie).AndReturn(True)

        self.m.StubOutWithMock(wc.WaitConditionHandle, 'keystone')
        wc.WaitConditionHandle.keystone().MultipleTimes().AndReturn(self.fc)

        id = identifier.ResourceIdentifier('test_tenant', stack.name,
                                           stack.id, '', 'WH')
        self.m.StubOutWithMock(wc.WaitConditionHandle, 'identifier')
        wc.WaitConditionHandle.identifier().MultipleTimes().AndReturn(id)

        self.m.StubOutWithMock(scheduler.TaskRunner, '_sleep')

        return stack

    @stack_delete_after
    def test_wait_meta(self):
        '''
        1 create stack
        2 assert empty instance metadata
        3 service.metadata_update()
        4 assert valid waitcond metadata
        5 assert valid instance metadata
        '''

        self.stack = self.create_stack()

        watch = self.stack['WC']
        inst = self.stack['S2']

        def check_empty(sleep_time):
            self.assertEqual(watch.FnGetAtt('Data'), '{}')
            self.assertEqual(inst.metadata['test'], None)

        def update_metadata(id, data, reason):
            self.man.metadata_update(self.ctx,
                                     dict(self.stack.identifier()),
                                     'WH',
                                     {'Data': data, 'Reason': reason,
                                      'Status': 'SUCCESS', 'UniqueId': id})

        def post_success(sleep_time):
            update_metadata('123', 'foo', 'bar')

        scheduler.TaskRunner._sleep(mox.IsA(int)).WithSideEffects(check_empty)
        scheduler.TaskRunner._sleep(mox.IsA(int)).WithSideEffects(post_success)
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)

        self.m.ReplayAll()
        self.stack.create()

        self.assertEqual(self.stack.state, self.stack.CREATE_COMPLETE)

        self.assertEqual(watch.FnGetAtt('Data'), '{"123": "foo"}')
        self.assertEqual(inst.metadata['test'], '{"123": "foo"}')

        update_metadata('456', 'blarg', 'wibble')
        self.assertEqual(watch.FnGetAtt('Data'),
                         '{"123": "foo", "456": "blarg"}')
        self.assertEqual(inst.metadata['test'],
                         '{"123": "foo", "456": "blarg"}')

        self.m.VerifyAll()
