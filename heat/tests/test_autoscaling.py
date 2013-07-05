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

import datetime
import copy

import mox

from heat.common import template_format
from heat.common import exception
from heat.engine.resources import autoscaling as asc
from heat.engine.resources import loadbalancer
from heat.engine.resources import instance
from heat.engine import parser
from heat.engine import resource
from heat.engine import scheduler
from heat.engine.resource import Metadata
from heat.openstack.common import timeutils
from heat.tests.common import HeatTestCase
from heat.tests.utils import setup_dummy_db
from heat.tests.utils import parse_stack

as_template = '''
{
  "AWSTemplateFormatVersion" : "2010-09-09",
  "Description" : "AutoScaling Test",
  "Parameters" : {
  "KeyName": {
    "Type": "String"
  }
  },
  "Resources" : {
    "WebServerGroup" : {
      "Type" : "AWS::AutoScaling::AutoScalingGroup",
      "Properties" : {
        "AvailabilityZones" : ["nova"],
        "LaunchConfigurationName" : { "Ref" : "LaunchConfig" },
        "MinSize" : "1",
        "MaxSize" : "5",
        "LoadBalancerNames" : [ { "Ref" : "ElasticLoadBalancer" } ]
      }
    },
    "WebServerScaleUpPolicy" : {
      "Type" : "AWS::AutoScaling::ScalingPolicy",
      "Properties" : {
        "AdjustmentType" : "ChangeInCapacity",
        "AutoScalingGroupName" : { "Ref" : "WebServerGroup" },
        "Cooldown" : "60",
        "ScalingAdjustment" : "1"
      }
    },
    "WebServerScaleDownPolicy" : {
      "Type" : "AWS::AutoScaling::ScalingPolicy",
      "Properties" : {
        "AdjustmentType" : "ChangeInCapacity",
        "AutoScalingGroupName" : { "Ref" : "WebServerGroup" },
        "Cooldown" : "60",
        "ScalingAdjustment" : "-1"
      }
    },
    "ElasticLoadBalancer" : {
        "Type" : "AWS::ElasticLoadBalancing::LoadBalancer",
        "Properties" : {
            "AvailabilityZones" : ["nova"],
            "Listeners" : [ {
                "LoadBalancerPort" : "80",
                "InstancePort" : "80",
                "Protocol" : "HTTP"
            }]
        }
    },
    "LaunchConfig" : {
      "Type" : "AWS::AutoScaling::LaunchConfiguration",
      "Properties": {
        "ImageId" : "foo",
        "InstanceType"   : "bar",
      }
    }
  }
}
'''


class AutoScalingTest(HeatTestCase):
    dummy_instance_id = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'

    def setUp(self):
        super(AutoScalingTest, self).setUp()
        setup_dummy_db()

    def create_scaling_group(self, t, stack, resource_name):
        rsrc = asc.AutoScalingGroup(resource_name,
                                    t['Resources'][resource_name],
                                    stack)
        self.assertEqual(None, rsrc.validate())
        scheduler.TaskRunner(rsrc.create)()
        self.assertEqual((rsrc.CREATE, rsrc.COMPLETE), rsrc.state)
        return rsrc

    def create_scaling_policy(self, t, stack, resource_name):
        rsrc = asc.ScalingPolicy(resource_name,
                                 t['Resources'][resource_name],
                                 stack)

        self.assertEqual(None, rsrc.validate())
        scheduler.TaskRunner(rsrc.create)()
        self.assertEqual((rsrc.CREATE, rsrc.COMPLETE), rsrc.state)
        return rsrc

    def _stub_create(self, num):
        self.m.StubOutWithMock(scheduler.TaskRunner, '_sleep')

        self.m.StubOutWithMock(instance.Instance, 'handle_create')
        self.m.StubOutWithMock(instance.Instance, 'check_create_complete')
        cookie = object()
        for x in range(num):
            instance.Instance.handle_create().AndReturn(cookie)
        instance.Instance.check_create_complete(cookie).AndReturn(False)
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        instance.Instance.check_create_complete(
            cookie).MultipleTimes().AndReturn(True)

    def _stub_lb_reload(self, num, unset=True, nochange=False):
        expected_list = [self.dummy_instance_id] * num
        if unset:
            self.m.VerifyAll()
            self.m.UnsetStubs()
        if num > 0:
            self.m.StubOutWithMock(instance.Instance, 'FnGetRefId')
            instance.Instance.FnGetRefId().MultipleTimes().AndReturn(
                self.dummy_instance_id)

        self.m.StubOutWithMock(loadbalancer.LoadBalancer, 'handle_update')
        if nochange:
            loadbalancer.LoadBalancer.handle_update(
                mox.IgnoreArg(), mox.IgnoreArg(), {}).AndReturn(None)
        else:
            loadbalancer.LoadBalancer.handle_update(
                mox.IgnoreArg(), mox.IgnoreArg(),
                {'Instances': expected_list}).AndReturn(None)

    def _stub_meta_expected(self, now, data, nmeta=1):
        # Stop time at now
        self.m.StubOutWithMock(timeutils, 'utcnow')
        timeutils.utcnow().MultipleTimes().AndReturn(now)

        # Then set a stub to ensure the metadata update is as
        # expected based on the timestamp and data
        self.m.StubOutWithMock(Metadata, '__set__')
        expected = {timeutils.strtime(now): data}
        # Note for ScalingPolicy, we expect to get a metadata
        # update for the policy and autoscaling group, so pass nmeta=2
        for x in range(nmeta):
            Metadata.__set__(mox.IgnoreArg(), expected).AndReturn(None)

    def test_scaling_delete_empty(self):
        t = template_format.parse(as_template)
        properties = t['Resources']['WebServerGroup']['Properties']
        properties['MinSize'] = '0'
        properties['MaxSize'] = '0'
        stack = parse_stack(t)

        now = timeutils.utcnow()
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        self.assertEqual(None, rsrc.resource_id)

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_adjust_down_empty(self):
        t = template_format.parse(as_template)
        properties = t['Resources']['WebServerGroup']['Properties']
        properties['MinSize'] = '1'
        properties['MaxSize'] = '1'
        stack = parse_stack(t)

        self._stub_lb_reload(1)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 1')
        self._stub_create(1)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)

        # Reduce the min size to 0, should complete without adjusting
        update_snippet = copy.deepcopy(rsrc.parsed_template())
        update_snippet['Properties']['MinSize'] = '0'
        self.assertEqual(None, rsrc.update(update_snippet))
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)

        # trigger adjustment to reduce to 0, resource_id should be None
        self._stub_lb_reload(0)
        self._stub_meta_expected(now, 'ChangeInCapacity : -1')
        self.m.ReplayAll()
        rsrc.adjust(-1)
        self.assertEqual(None, rsrc.resource_id)

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_group_update_replace(self):
        t = template_format.parse(as_template)
        stack = parse_stack(t)

        self._stub_lb_reload(1)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 1')
        self._stub_create(1)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')

        self.assertEqual('WebServerGroup', rsrc.FnGetRefId())
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)
        update_snippet = copy.deepcopy(rsrc.parsed_template())
        update_snippet['Properties']['LaunchConfigurationName'] = 'foo'
        self.assertRaises(resource.UpdateReplace,
                          rsrc.update, update_snippet)

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_group_suspend(self):
        t = template_format.parse(as_template)
        stack = parse_stack(t)

        self._stub_lb_reload(1)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 1')
        self._stub_create(1)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        self.assertEqual('WebServerGroup', rsrc.FnGetRefId())
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)
        self.assertEqual(rsrc.state, (rsrc.CREATE, rsrc.COMPLETE))

        self.m.VerifyAll()
        self.m.UnsetStubs()

        self.m.StubOutWithMock(scheduler.TaskRunner, '_sleep')
        self.m.StubOutWithMock(instance.Instance, 'handle_suspend')
        self.m.StubOutWithMock(instance.Instance, 'check_suspend_complete')
        inst_cookie = (object(), object(), object())
        instance.Instance.handle_suspend().AndReturn(inst_cookie)
        instance.Instance.check_suspend_complete(inst_cookie).AndReturn(False)
        instance.Instance.check_suspend_complete(inst_cookie).AndReturn(True)
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        self.m.ReplayAll()

        scheduler.TaskRunner(rsrc.suspend)()
        self.assertEqual(rsrc.state, (rsrc.SUSPEND, rsrc.COMPLETE))

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_group_resume(self):
        t = template_format.parse(as_template)
        stack = parse_stack(t)

        self._stub_lb_reload(1)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 1')
        self._stub_create(1)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        self.assertEqual('WebServerGroup', rsrc.FnGetRefId())
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)
        self.assertEqual(rsrc.state, (rsrc.CREATE, rsrc.COMPLETE))

        self.m.VerifyAll()
        self.m.UnsetStubs()

        self.m.StubOutWithMock(scheduler.TaskRunner, '_sleep')
        self.m.StubOutWithMock(instance.Instance, 'handle_resume')
        self.m.StubOutWithMock(instance.Instance, 'check_resume_complete')
        inst_cookie = (object(), object(), object())
        instance.Instance.handle_resume().AndReturn(inst_cookie)
        instance.Instance.check_resume_complete(inst_cookie).AndReturn(False)
        instance.Instance.check_resume_complete(inst_cookie).AndReturn(True)
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        self.m.ReplayAll()

        rsrc.state_set(rsrc.SUSPEND, rsrc.COMPLETE)

        scheduler.TaskRunner(rsrc.resume)()
        self.assertEqual(rsrc.state, (rsrc.RESUME, rsrc.COMPLETE))

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_group_suspend_multiple(self):
        t = template_format.parse(as_template)
        properties = t['Resources']['WebServerGroup']['Properties']
        properties['DesiredCapacity'] = '2'
        stack = parse_stack(t)

        self._stub_lb_reload(2)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 2')
        self._stub_create(2)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        self.assertEqual('WebServerGroup', rsrc.FnGetRefId())
        self.assertEqual('WebServerGroup-0,WebServerGroup-1', rsrc.resource_id)
        self.assertEqual(rsrc.state, (rsrc.CREATE, rsrc.COMPLETE))

        self.m.VerifyAll()
        self.m.UnsetStubs()

        self.m.StubOutWithMock(instance.Instance, 'handle_suspend')
        self.m.StubOutWithMock(instance.Instance, 'check_suspend_complete')
        inst_cookie1 = ('foo1', 'foo2', 'foo3')
        inst_cookie2 = ('bar1', 'bar2', 'bar3')
        instance.Instance.handle_suspend().AndReturn(inst_cookie1)
        instance.Instance.handle_suspend().AndReturn(inst_cookie2)
        instance.Instance.check_suspend_complete(inst_cookie1).AndReturn(True)
        instance.Instance.check_suspend_complete(inst_cookie2).AndReturn(True)
        self.m.ReplayAll()

        scheduler.TaskRunner(rsrc.suspend)()
        self.assertEqual(rsrc.state, (rsrc.SUSPEND, rsrc.COMPLETE))

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_group_resume_multiple(self):
        t = template_format.parse(as_template)
        properties = t['Resources']['WebServerGroup']['Properties']
        properties['DesiredCapacity'] = '2'
        stack = parse_stack(t)

        self._stub_lb_reload(2)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 2')
        self._stub_create(2)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        self.assertEqual('WebServerGroup', rsrc.FnGetRefId())
        self.assertEqual('WebServerGroup-0,WebServerGroup-1', rsrc.resource_id)
        self.assertEqual(rsrc.state, (rsrc.CREATE, rsrc.COMPLETE))

        self.m.VerifyAll()
        self.m.UnsetStubs()

        self.m.StubOutWithMock(instance.Instance, 'handle_resume')
        self.m.StubOutWithMock(instance.Instance, 'check_resume_complete')
        inst_cookie1 = ('foo1', 'foo2', 'foo3')
        inst_cookie2 = ('bar1', 'bar2', 'bar3')
        instance.Instance.handle_resume().AndReturn(inst_cookie1)
        instance.Instance.handle_resume().AndReturn(inst_cookie2)
        instance.Instance.check_resume_complete(inst_cookie1).AndReturn(True)
        instance.Instance.check_resume_complete(inst_cookie2).AndReturn(True)
        self.m.ReplayAll()

        rsrc.state_set(rsrc.SUSPEND, rsrc.COMPLETE)

        scheduler.TaskRunner(rsrc.resume)()
        self.assertEqual(rsrc.state, (rsrc.RESUME, rsrc.COMPLETE))

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_group_suspend_fail(self):
        t = template_format.parse(as_template)
        stack = parse_stack(t)

        self._stub_lb_reload(1)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 1')
        self._stub_create(1)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        self.assertEqual('WebServerGroup', rsrc.FnGetRefId())
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)
        self.assertEqual(rsrc.state, (rsrc.CREATE, rsrc.COMPLETE))

        self.m.VerifyAll()
        self.m.UnsetStubs()

        self.m.StubOutWithMock(instance.Instance, 'handle_suspend')
        self.m.StubOutWithMock(instance.Instance, 'check_suspend_complete')
        inst_cookie = (object(), object(), object())
        instance.Instance.handle_suspend().AndRaise(Exception('oops'))
        self.m.ReplayAll()

        sus_task = scheduler.TaskRunner(rsrc.suspend)
        self.assertRaises(exception.ResourceFailure, sus_task, ())
        self.assertEqual(rsrc.state, (rsrc.SUSPEND, rsrc.FAILED))
        self.assertEqual(rsrc.status_reason, 'Exception: oops')

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_group_resume_fail(self):
        t = template_format.parse(as_template)
        stack = parse_stack(t)

        self._stub_lb_reload(1)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 1')
        self._stub_create(1)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        self.assertEqual('WebServerGroup', rsrc.FnGetRefId())
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)
        self.assertEqual(rsrc.state, (rsrc.CREATE, rsrc.COMPLETE))

        self.m.VerifyAll()
        self.m.UnsetStubs()

        self.m.StubOutWithMock(instance.Instance, 'handle_resume')
        self.m.StubOutWithMock(instance.Instance, 'check_resume_complete')
        inst_cookie = (object(), object(), object())
        instance.Instance.handle_resume().AndRaise(Exception('oops'))
        self.m.ReplayAll()

        rsrc.state_set(rsrc.SUSPEND, rsrc.COMPLETE)

        sus_task = scheduler.TaskRunner(rsrc.resume)
        self.assertRaises(exception.ResourceFailure, sus_task, ())
        self.assertEqual(rsrc.state, (rsrc.RESUME, rsrc.FAILED))
        self.assertEqual(rsrc.status_reason, 'Exception: oops')

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_group_create_error(self):
        t = template_format.parse(as_template)
        stack = parse_stack(t)

        self.m.StubOutWithMock(scheduler.TaskRunner, '_sleep')

        self.m.StubOutWithMock(instance.Instance, 'handle_create')
        self.m.StubOutWithMock(instance.Instance, 'check_create_complete')
        exc = exception.ResourceFailure(Exception())
        instance.Instance.handle_create().AndRaise(exc)

        self.m.ReplayAll()
        rsrc = asc.AutoScalingGroup('WebServerGroup',
                                    t['Resources']['WebServerGroup'],
                                    stack)
        self.assertEqual(None, rsrc.validate())
        self.assertRaises(exception.ResourceFailure,
                          scheduler.TaskRunner(rsrc.create))
        self.assertEqual((rsrc.CREATE, rsrc.FAILED), rsrc.state)

        self.assertEqual(None, rsrc.resource_id)

        self.m.VerifyAll()

    def test_scaling_group_update_ok_maxsize(self):
        t = template_format.parse(as_template)
        properties = t['Resources']['WebServerGroup']['Properties']
        properties['MinSize'] = '1'
        properties['MaxSize'] = '3'
        stack = parse_stack(t)

        self._stub_lb_reload(1)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 1')
        self._stub_create(1)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)

        # Reduce the max size to 2, should complete without adjusting
        update_snippet = copy.deepcopy(rsrc.parsed_template())
        update_snippet['Properties']['MaxSize'] = '2'
        self.assertEqual(None, rsrc.update(update_snippet))
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)

        self.assertEqual('2', rsrc.properties['MaxSize'])

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_group_update_ok_minsize(self):
        t = template_format.parse(as_template)
        properties = t['Resources']['WebServerGroup']['Properties']
        properties['MinSize'] = '1'
        properties['MaxSize'] = '3'
        stack = parse_stack(t)

        self._stub_lb_reload(1)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 1')
        self._stub_create(1)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)

        # Increase min size to 2, should trigger an ExactCapacity adjust
        self._stub_lb_reload(2)
        self._stub_meta_expected(now, 'ExactCapacity : 2')
        self._stub_create(1)
        self.m.ReplayAll()

        update_snippet = copy.deepcopy(rsrc.parsed_template())
        update_snippet['Properties']['MinSize'] = '2'
        self.assertEqual(None, rsrc.update(update_snippet))
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)
        self.assertEqual('2', rsrc.properties['MinSize'])

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_group_update_ok_desired(self):
        t = template_format.parse(as_template)
        properties = t['Resources']['WebServerGroup']['Properties']
        properties['MinSize'] = '1'
        properties['MaxSize'] = '3'
        stack = parse_stack(t)

        self._stub_lb_reload(1)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 1')
        self._stub_create(1)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)

        # Increase min size to 2 via DesiredCapacity, should adjust
        self._stub_lb_reload(2)
        self._stub_meta_expected(now, 'ExactCapacity : 2')
        self._stub_create(1)
        self.m.ReplayAll()

        update_snippet = copy.deepcopy(rsrc.parsed_template())
        update_snippet['Properties']['DesiredCapacity'] = '2'
        self.assertEqual(None, rsrc.update(update_snippet))
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)

        self.assertEqual('2', rsrc.properties['DesiredCapacity'])

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_group_update_ok_desired_remove(self):
        t = template_format.parse(as_template)
        properties = t['Resources']['WebServerGroup']['Properties']
        properties['DesiredCapacity'] = '2'
        stack = parse_stack(t)

        self._stub_lb_reload(2)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 2')
        self._stub_create(2)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)

        # Remove DesiredCapacity from the updated template, which should
        # have no effect, it's an optional parameter
        update_snippet = copy.deepcopy(rsrc.parsed_template())
        del(update_snippet['Properties']['DesiredCapacity'])
        self.assertEqual(None, rsrc.update(update_snippet))
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)

        self.assertEqual(None, rsrc.properties['DesiredCapacity'])

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_group_update_ok_cooldown(self):
        t = template_format.parse(as_template)
        properties = t['Resources']['WebServerGroup']['Properties']
        properties['Cooldown'] = '60'
        stack = parse_stack(t)

        self._stub_lb_reload(1)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 1')
        self._stub_create(1)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')

        self.assertEqual('WebServerGroup', rsrc.FnGetRefId())
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)
        update_snippet = copy.deepcopy(rsrc.parsed_template())
        update_snippet['Properties']['Cooldown'] = '61'
        self.assertEqual(None, rsrc.update(update_snippet))
        self.assertEqual('61', rsrc.properties['Cooldown'])

        rsrc.delete()
        self.m.VerifyAll()

    def test_lb_reload_static_resolve(self):
        t = template_format.parse(as_template)
        properties = t['Resources']['ElasticLoadBalancer']['Properties']
        properties['AvailabilityZones'] = {'Fn::GetAZs': ''}

        self.m.StubOutWithMock(parser.Stack, 'get_availability_zones')
        parser.Stack.get_availability_zones().MultipleTimes().AndReturn(
            ['abc', 'xyz'])

        # Check that the Fn::GetAZs is correctly resolved
        expected = {u'Type': u'AWS::ElasticLoadBalancing::LoadBalancer',
                    u'Properties': {'Instances': ['WebServerGroup-0'],
                                    u'Listeners': [{u'InstancePort': u'80',
                                                    u'LoadBalancerPort': u'80',
                                                    u'Protocol': u'HTTP'}],
                                    u'AvailabilityZones': ['abc', 'xyz']}}
        self.m.StubOutWithMock(loadbalancer.LoadBalancer, 'update')
        loadbalancer.LoadBalancer.update(expected).AndReturn(None)

        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 1')
        self._stub_create(1)
        self.m.ReplayAll()
        stack = parse_stack(t)
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')

        self.assertEqual('WebServerGroup', rsrc.FnGetRefId())
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)
        update_snippet = copy.deepcopy(rsrc.parsed_template())
        update_snippet['Properties']['Cooldown'] = '61'
        self.assertEqual(None, rsrc.update(update_snippet))

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_group_adjust(self):
        t = template_format.parse(as_template)
        stack = parse_stack(t)

        # start with 3
        properties = t['Resources']['WebServerGroup']['Properties']
        properties['DesiredCapacity'] = '3'
        self._stub_lb_reload(3)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 3')
        self._stub_create(3)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        self.assertEqual('WebServerGroup-0,WebServerGroup-1,WebServerGroup-2',
                         rsrc.resource_id)

        # reduce to 1
        self._stub_lb_reload(1)
        self._stub_meta_expected(now, 'ChangeInCapacity : -2')
        self.m.ReplayAll()
        rsrc.adjust(-2)
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)

        # raise to 3
        self._stub_lb_reload(3)
        self._stub_meta_expected(now, 'ChangeInCapacity : 2')
        self._stub_create(2)
        self.m.ReplayAll()
        rsrc.adjust(2)
        self.assertEqual('WebServerGroup-0,WebServerGroup-1,WebServerGroup-2',
                         rsrc.resource_id)

        # set to 2
        self._stub_lb_reload(2)
        self._stub_meta_expected(now, 'ExactCapacity : 2')
        self.m.ReplayAll()
        rsrc.adjust(2, 'ExactCapacity')
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)
        self.m.VerifyAll()

    def test_scaling_group_scale_up_failure(self):
        t = template_format.parse(as_template)
        stack = parse_stack(t)

        # Create initial group
        self._stub_lb_reload(1)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 1')
        self._stub_create(1)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)
        self.m.VerifyAll()
        self.m.UnsetStubs()

        # Scale up one 1 instance with resource failure
        self.m.StubOutWithMock(instance.Instance, 'handle_create')
        exc = exception.ResourceFailure(Exception())
        instance.Instance.handle_create().AndRaise(exc)
        self.m.StubOutWithMock(instance.Instance, 'destroy')
        instance.Instance.destroy()
        self._stub_lb_reload(1, unset=False, nochange=True)
        self.m.ReplayAll()

        rsrc.adjust(1)
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)

        self.m.VerifyAll()

    def test_scaling_group_nochange(self):
        t = template_format.parse(as_template)
        stack = parse_stack(t)

        # Create initial group, 2 instances
        properties = t['Resources']['WebServerGroup']['Properties']
        properties['DesiredCapacity'] = '2'
        self._stub_lb_reload(2)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 2')
        self._stub_create(2)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        stack.resources['WebServerGroup'] = rsrc
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)

        # raise above the max
        rsrc.adjust(4)
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)

        # lower below the min
        rsrc.adjust(-2)
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)

        # no change
        rsrc.adjust(0)
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)
        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_group_percent(self):
        t = template_format.parse(as_template)
        stack = parse_stack(t)

        # Create initial group, 2 instances
        properties = t['Resources']['WebServerGroup']['Properties']
        properties['DesiredCapacity'] = '2'
        self._stub_lb_reload(2)
        self._stub_create(2)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 2')
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        stack.resources['WebServerGroup'] = rsrc
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)

        # reduce by 50%
        self._stub_lb_reload(1)
        self._stub_meta_expected(now, 'PercentChangeInCapacity : -50')
        self.m.ReplayAll()
        rsrc.adjust(-50, 'PercentChangeInCapacity')
        self.assertEqual('WebServerGroup-0',
                         rsrc.resource_id)

        # raise by 200%
        self._stub_lb_reload(3)
        self._stub_meta_expected(now, 'PercentChangeInCapacity : 200')
        self._stub_create(2)
        self.m.ReplayAll()
        rsrc.adjust(200, 'PercentChangeInCapacity')
        self.assertEqual('WebServerGroup-0,WebServerGroup-1,WebServerGroup-2',
                         rsrc.resource_id)

        rsrc.delete()

    def test_scaling_group_cooldown_toosoon(self):
        t = template_format.parse(as_template)
        stack = parse_stack(t)

        # Create initial group, 2 instances, Cooldown 60s
        properties = t['Resources']['WebServerGroup']['Properties']
        properties['DesiredCapacity'] = '2'
        properties['Cooldown'] = '60'
        self._stub_lb_reload(2)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 2')
        self._stub_create(2)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        stack.resources['WebServerGroup'] = rsrc
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)

        # reduce by 50%
        self._stub_lb_reload(1)
        self._stub_meta_expected(now, 'PercentChangeInCapacity : -50')
        self.m.ReplayAll()
        rsrc.adjust(-50, 'PercentChangeInCapacity')
        self.assertEqual('WebServerGroup-0',
                         rsrc.resource_id)

        # Now move time on 10 seconds - Cooldown in template is 60
        # so this should not update the policy metadata, and the
        # scaling group instances should be unchanged
        # Note we have to stub Metadata.__get__ since up_policy isn't
        # stored in the DB (because the stack hasn't really been created)
        previous_meta = {timeutils.strtime(now):
                         'PercentChangeInCapacity : -50'}

        self.m.VerifyAll()
        self.m.UnsetStubs()

        now = now + datetime.timedelta(seconds=10)
        self.m.StubOutWithMock(timeutils, 'utcnow')
        timeutils.utcnow().MultipleTimes().AndReturn(now)

        self.m.StubOutWithMock(Metadata, '__get__')
        Metadata.__get__(mox.IgnoreArg(), rsrc, mox.IgnoreArg()
                         ).AndReturn(previous_meta)

        self.m.ReplayAll()

        # raise by 200%, too soon for Cooldown so there should be no change
        rsrc.adjust(200, 'PercentChangeInCapacity')
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)

        rsrc.delete()

    def test_scaling_group_cooldown_ok(self):
        t = template_format.parse(as_template)
        stack = parse_stack(t)

        # Create initial group, 2 instances, Cooldown 60s
        properties = t['Resources']['WebServerGroup']['Properties']
        properties['DesiredCapacity'] = '2'
        properties['Cooldown'] = '60'
        self._stub_lb_reload(2)
        self._stub_create(2)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 2')
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        stack.resources['WebServerGroup'] = rsrc
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)

        # reduce by 50%
        self._stub_lb_reload(1)
        self._stub_meta_expected(now, 'PercentChangeInCapacity : -50')
        self.m.ReplayAll()
        rsrc.adjust(-50, 'PercentChangeInCapacity')
        self.assertEqual('WebServerGroup-0',
                         rsrc.resource_id)

        # Now move time on 61 seconds - Cooldown in template is 60
        # so this should update the policy metadata, and the
        # scaling group instances updated
        previous_meta = {timeutils.strtime(now):
                         'PercentChangeInCapacity : -50'}

        self.m.VerifyAll()
        self.m.UnsetStubs()

        now = now + datetime.timedelta(seconds=61)

        self.m.StubOutWithMock(Metadata, '__get__')
        Metadata.__get__(mox.IgnoreArg(), rsrc, mox.IgnoreArg()
                         ).AndReturn(previous_meta)

        # raise by 200%, should work
        self._stub_lb_reload(3, unset=False)
        self._stub_create(2)
        self._stub_meta_expected(now, 'PercentChangeInCapacity : 200')
        self.m.ReplayAll()
        rsrc.adjust(200, 'PercentChangeInCapacity')
        self.assertEqual('WebServerGroup-0,WebServerGroup-1,WebServerGroup-2',
                         rsrc.resource_id)

        rsrc.delete()

    def test_scaling_group_cooldown_zero(self):
        t = template_format.parse(as_template)
        stack = parse_stack(t)

        # Create initial group, 2 instances, Cooldown 0
        properties = t['Resources']['WebServerGroup']['Properties']
        properties['DesiredCapacity'] = '2'
        properties['Cooldown'] = '0'
        self._stub_lb_reload(2)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 2')
        self._stub_create(2)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        stack.resources['WebServerGroup'] = rsrc
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)

        # reduce by 50%
        self._stub_lb_reload(1)
        self._stub_meta_expected(now, 'PercentChangeInCapacity : -50')
        self.m.ReplayAll()
        rsrc.adjust(-50, 'PercentChangeInCapacity')
        self.assertEqual('WebServerGroup-0',
                         rsrc.resource_id)

        # Don't move time, since cooldown is zero, it should work
        previous_meta = {timeutils.strtime(now):
                         'PercentChangeInCapacity : -50'}

        self.m.VerifyAll()
        self.m.UnsetStubs()

        self.m.StubOutWithMock(Metadata, '__get__')
        Metadata.__get__(mox.IgnoreArg(), rsrc, mox.IgnoreArg()
                         ).AndReturn(previous_meta)

        # raise by 200%, should work
        self._stub_lb_reload(3, unset=False)
        self._stub_meta_expected(now, 'PercentChangeInCapacity : 200')
        self._stub_create(2)
        self.m.ReplayAll()
        rsrc.adjust(200, 'PercentChangeInCapacity')
        self.assertEqual('WebServerGroup-0,WebServerGroup-1,WebServerGroup-2',
                         rsrc.resource_id)

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_policy_up(self):
        t = template_format.parse(as_template)
        stack = parse_stack(t)

        # Create initial group
        self._stub_lb_reload(1)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 1')
        self._stub_create(1)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        stack.resources['WebServerGroup'] = rsrc
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)

        # Scale up one
        self._stub_lb_reload(2)
        self._stub_meta_expected(now, 'ChangeInCapacity : 1', 2)
        self._stub_create(1)
        self.m.ReplayAll()
        up_policy = self.create_scaling_policy(t, stack,
                                               'WebServerScaleUpPolicy')
        up_policy.alarm()
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_policy_down(self):
        t = template_format.parse(as_template)
        stack = parse_stack(t)

        # Create initial group, 2 instances
        properties = t['Resources']['WebServerGroup']['Properties']
        properties['DesiredCapacity'] = '2'
        self._stub_lb_reload(2)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 2')
        self._stub_create(2)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        stack.resources['WebServerGroup'] = rsrc
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)

        # Scale down one
        self._stub_lb_reload(1)
        self._stub_meta_expected(now, 'ChangeInCapacity : -1', 2)
        self.m.ReplayAll()
        down_policy = self.create_scaling_policy(t, stack,
                                                 'WebServerScaleDownPolicy')
        down_policy.alarm()
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_policy_cooldown_toosoon(self):
        t = template_format.parse(as_template)
        stack = parse_stack(t)

        # Create initial group
        self._stub_lb_reload(1)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 1')
        self._stub_create(1)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        stack.resources['WebServerGroup'] = rsrc
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)

        # Scale up one
        self._stub_lb_reload(2)
        self._stub_meta_expected(now, 'ChangeInCapacity : 1', 2)
        self._stub_create(1)
        self.m.ReplayAll()
        up_policy = self.create_scaling_policy(t, stack,
                                               'WebServerScaleUpPolicy')
        up_policy.alarm()
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)

        # Now move time on 10 seconds - Cooldown in template is 60
        # so this should not update the policy metadata, and the
        # scaling group instances should be unchanged
        # Note we have to stub Metadata.__get__ since up_policy isn't
        # stored in the DB (because the stack hasn't really been created)
        previous_meta = {timeutils.strtime(now): 'ChangeInCapacity : 1'}

        self.m.VerifyAll()
        self.m.UnsetStubs()

        now = now + datetime.timedelta(seconds=10)
        self.m.StubOutWithMock(timeutils, 'utcnow')
        timeutils.utcnow().MultipleTimes().AndReturn(now)

        self.m.StubOutWithMock(Metadata, '__get__')
        Metadata.__get__(mox.IgnoreArg(), up_policy, mox.IgnoreArg()
                         ).AndReturn(previous_meta)

        self.m.ReplayAll()
        up_policy.alarm()
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_policy_cooldown_ok(self):
        t = template_format.parse(as_template)
        stack = parse_stack(t)

        # Create initial group
        self._stub_lb_reload(1)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 1')
        self._stub_create(1)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        stack.resources['WebServerGroup'] = rsrc
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)

        # Scale up one
        self._stub_lb_reload(2)
        self._stub_meta_expected(now, 'ChangeInCapacity : 1', 2)
        self._stub_create(1)
        self.m.ReplayAll()
        up_policy = self.create_scaling_policy(t, stack,
                                               'WebServerScaleUpPolicy')
        up_policy.alarm()
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)

        # Now move time on 61 seconds - Cooldown in template is 60
        # so this should trigger a scale-up
        previous_meta = {timeutils.strtime(now): 'ChangeInCapacity : 1'}
        self.m.VerifyAll()
        self.m.UnsetStubs()

        self.m.StubOutWithMock(Metadata, '__get__')
        Metadata.__get__(mox.IgnoreArg(), up_policy, mox.IgnoreArg()
                         ).AndReturn(previous_meta)
        Metadata.__get__(mox.IgnoreArg(), rsrc, mox.IgnoreArg()
                         ).AndReturn(previous_meta)

        now = now + datetime.timedelta(seconds=61)
        self._stub_lb_reload(3, unset=False)
        self._stub_meta_expected(now, 'ChangeInCapacity : 1', 2)
        self._stub_create(1)

        self.m.ReplayAll()
        up_policy.alarm()
        self.assertEqual('WebServerGroup-0,WebServerGroup-1,WebServerGroup-2',
                         rsrc.resource_id)

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_policy_cooldown_zero(self):
        t = template_format.parse(as_template)
        stack = parse_stack(t)

        # Create initial group
        self._stub_lb_reload(1)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 1')
        self._stub_create(1)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        stack.resources['WebServerGroup'] = rsrc
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)

        # Create the scaling policy (with Cooldown=0) and scale up one
        properties = t['Resources']['WebServerScaleUpPolicy']['Properties']
        properties['Cooldown'] = '0'
        self._stub_lb_reload(2)
        self._stub_meta_expected(now, 'ChangeInCapacity : 1', 2)
        self._stub_create(1)
        self.m.ReplayAll()
        up_policy = self.create_scaling_policy(t, stack,
                                               'WebServerScaleUpPolicy')
        up_policy.alarm()
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)

        # Now trigger another scale-up without changing time, should work
        previous_meta = {timeutils.strtime(now): 'ChangeInCapacity : 1'}
        self.m.VerifyAll()
        self.m.UnsetStubs()

        self.m.StubOutWithMock(Metadata, '__get__')
        Metadata.__get__(mox.IgnoreArg(), up_policy, mox.IgnoreArg()
                         ).AndReturn(previous_meta)
        Metadata.__get__(mox.IgnoreArg(), rsrc, mox.IgnoreArg()
                         ).AndReturn(previous_meta)

        self._stub_lb_reload(3, unset=False)
        self._stub_meta_expected(now, 'ChangeInCapacity : 1', 2)
        self._stub_create(1)

        self.m.ReplayAll()
        up_policy.alarm()
        self.assertEqual('WebServerGroup-0,WebServerGroup-1,WebServerGroup-2',
                         rsrc.resource_id)

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_policy_cooldown_none(self):
        t = template_format.parse(as_template)
        stack = parse_stack(t)

        # Create initial group
        self._stub_lb_reload(1)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 1')
        self._stub_create(1)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        stack.resources['WebServerGroup'] = rsrc
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)

        # Create the scaling policy no Cooldown property, should behave the
        # same as when Cooldown==0
        properties = t['Resources']['WebServerScaleUpPolicy']['Properties']
        del(properties['Cooldown'])
        self._stub_lb_reload(2)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ChangeInCapacity : 1', 2)
        self._stub_create(1)
        self.m.ReplayAll()
        up_policy = self.create_scaling_policy(t, stack,
                                               'WebServerScaleUpPolicy')
        up_policy.alarm()
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)

        # Now trigger another scale-up without changing time, should work
        previous_meta = {timeutils.strtime(now): 'ChangeInCapacity : 1'}
        self.m.VerifyAll()
        self.m.UnsetStubs()

        self.m.StubOutWithMock(Metadata, '__get__')
        Metadata.__get__(mox.IgnoreArg(), up_policy, mox.IgnoreArg()
                         ).AndReturn(previous_meta)
        Metadata.__get__(mox.IgnoreArg(), rsrc, mox.IgnoreArg()
                         ).AndReturn(previous_meta)

        self._stub_lb_reload(3, unset=False)
        self._stub_meta_expected(now, 'ChangeInCapacity : 1', 2)
        self._stub_create(1)

        self.m.ReplayAll()
        up_policy.alarm()
        self.assertEqual('WebServerGroup-0,WebServerGroup-1,WebServerGroup-2',
                         rsrc.resource_id)

        rsrc.delete()
        self.m.VerifyAll()

    def test_scaling_policy_update(self):
        t = template_format.parse(as_template)
        stack = parse_stack(t)

        # Create initial group
        self._stub_lb_reload(1)
        now = timeutils.utcnow()
        self._stub_meta_expected(now, 'ExactCapacity : 1')
        self._stub_create(1)
        self.m.ReplayAll()
        rsrc = self.create_scaling_group(t, stack, 'WebServerGroup')
        stack.resources['WebServerGroup'] = rsrc
        self.assertEqual('WebServerGroup-0', rsrc.resource_id)

        # Create initial scaling policy
        up_policy = self.create_scaling_policy(t, stack,
                                               'WebServerScaleUpPolicy')

        # Scale up one
        self._stub_lb_reload(2)
        self._stub_meta_expected(now, 'ChangeInCapacity : 1', 2)
        self._stub_create(1)
        self.m.ReplayAll()

        # Trigger alarm
        up_policy.alarm()
        self.assertEqual('WebServerGroup-0,WebServerGroup-1',
                         rsrc.resource_id)

        # Update scaling policy
        update_snippet = copy.deepcopy(up_policy.parsed_template())
        update_snippet['Properties']['ScalingAdjustment'] = '2'
        self.assertEqual(None, up_policy.update(update_snippet))
        self.assertEqual('2',
                         up_policy.properties['ScalingAdjustment'])

        # Now move time on 61 seconds - Cooldown in template is 60
        # so this should trigger a scale-up
        previous_meta = {timeutils.strtime(now): 'ChangeInCapacity : 1'}
        self.m.VerifyAll()
        self.m.UnsetStubs()

        self.m.StubOutWithMock(Metadata, '__get__')
        Metadata.__get__(mox.IgnoreArg(), up_policy, mox.IgnoreArg()
                         ).AndReturn(previous_meta)
        Metadata.__get__(mox.IgnoreArg(), rsrc, mox.IgnoreArg()
                         ).AndReturn(previous_meta)

        now = now + datetime.timedelta(seconds=61)

        self._stub_lb_reload(4, unset=False)
        self._stub_meta_expected(now, 'ChangeInCapacity : 2', 2)
        self._stub_create(2)
        self.m.ReplayAll()

        # Trigger alarm
        up_policy.alarm()
        self.assertEqual('WebServerGroup-0,WebServerGroup-1,'
                         'WebServerGroup-2,WebServerGroup-3',
                         rsrc.resource_id)

        rsrc.delete()
        self.m.VerifyAll()
