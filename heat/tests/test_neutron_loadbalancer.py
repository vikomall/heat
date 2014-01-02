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

import copy

from testtools import skipIf

from heat.common import exception
from heat.common import template_format
from heat.engine import clients
from heat.engine import scheduler
from heat.engine.resources.neutron import loadbalancer
from heat.openstack.common.importutils import try_import
from heat.tests import fakes
from heat.tests import utils
from heat.tests.common import HeatTestCase
from heat.tests.v1_1 import fakes as nova_fakes

neutronclient = try_import('neutronclient.v2_0.client')

health_monitor_template = '''
{
  "AWSTemplateFormatVersion" : "2010-09-09",
  "Description" : "Template to test load balancer resources",
  "Parameters" : {},
  "Resources" : {
    "monitor": {
      "Type": "OS::Neutron::HealthMonitor",
      "Properties": {
        "type": "HTTP",
        "delay": 3,
        "max_retries": 5,
        "timeout": 10
      }
    }
  }
}
'''

pool_template = '''
{
  "AWSTemplateFormatVersion" : "2010-09-09",
  "Description" : "Template to test load balancer resources",
  "Parameters" : {},
  "Resources" : {
    "pool": {
      "Type": "OS::Neutron::Pool",
      "Properties": {
        "protocol": "HTTP",
        "subnet_id": "sub123",
        "lb_method": "ROUND_ROBIN",
        "vip": {
            "protocol_port": 80
        }
      }
    }
  }
}
'''

member_template = '''
{
  "AWSTemplateFormatVersion" : "2010-09-09",
  "Description" : "Template to test load balancer member",
  "Resources" : {
    "member": {
      "Type": "OS::Neutron::PoolMember",
      "Properties": {
        "protocol_port": 8080,
        "pool_id": "pool123",
        "address": "1.2.3.4"
      }
    }
  }
}
'''

lb_template = '''
{
  "AWSTemplateFormatVersion" : "2010-09-09",
  "Description" : "Template to test load balancer resources",
  "Parameters" : {},
  "Resources" : {
    "lb": {
      "Type": "OS::Neutron::LoadBalancer",
      "Properties": {
        "protocol_port": 8080,
        "pool_id": "pool123",
        "members": ["1234"]
      }
    }
  }
}
'''


@skipIf(neutronclient is None, 'neutronclient unavailable')
class HealthMonitorTest(HeatTestCase):

    def setUp(self):
        super(HealthMonitorTest, self).setUp()
        self.m.StubOutWithMock(neutronclient.Client, 'create_health_monitor')
        self.m.StubOutWithMock(neutronclient.Client, 'delete_health_monitor')
        self.m.StubOutWithMock(neutronclient.Client, 'show_health_monitor')
        self.m.StubOutWithMock(neutronclient.Client, 'update_health_monitor')
        self.m.StubOutWithMock(clients.OpenStackClients, 'keystone')
        utils.setup_dummy_db()

    def create_health_monitor(self):
        clients.OpenStackClients.keystone().AndReturn(
            fakes.FakeKeystoneClient())
        neutronclient.Client.create_health_monitor({
            'health_monitor': {
                'delay': 3, 'max_retries': 5, 'type': u'HTTP',
                'timeout': 10, 'admin_state_up': True}}
        ).AndReturn({'health_monitor': {'id': '5678'}})

        snippet = template_format.parse(health_monitor_template)
        stack = utils.parse_stack(snippet)
        return loadbalancer.HealthMonitor(
            'monitor', snippet['Resources']['monitor'], stack)

    def test_create(self):
        rsrc = self.create_health_monitor()
        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        self.assertEqual((rsrc.CREATE, rsrc.COMPLETE), rsrc.state)
        self.m.VerifyAll()

    def test_create_failed(self):
        clients.OpenStackClients.keystone().AndReturn(
            fakes.FakeKeystoneClient())
        neutronclient.Client.create_health_monitor({
            'health_monitor': {
                'delay': 3, 'max_retries': 5, 'type': u'HTTP',
                'timeout': 10, 'admin_state_up': True}}
        ).AndRaise(loadbalancer.NeutronClientException())
        self.m.ReplayAll()

        snippet = template_format.parse(health_monitor_template)
        stack = utils.parse_stack(snippet)
        rsrc = loadbalancer.HealthMonitor(
            'monitor', snippet['Resources']['monitor'], stack)
        error = self.assertRaises(exception.ResourceFailure,
                                  scheduler.TaskRunner(rsrc.create))
        self.assertEqual(
            'NeutronClientException: An unknown exception occurred.',
            str(error))
        self.assertEqual((rsrc.CREATE, rsrc.FAILED), rsrc.state)
        self.m.VerifyAll()

    def test_delete(self):
        neutronclient.Client.delete_health_monitor('5678')
        neutronclient.Client.show_health_monitor('5678').AndRaise(
            loadbalancer.NeutronClientException(status_code=404))

        rsrc = self.create_health_monitor()
        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        scheduler.TaskRunner(rsrc.delete)()
        self.assertEqual((rsrc.DELETE, rsrc.COMPLETE), rsrc.state)
        self.m.VerifyAll()

    def test_delete_already_gone(self):
        neutronclient.Client.delete_health_monitor('5678').AndRaise(
            loadbalancer.NeutronClientException(status_code=404))

        rsrc = self.create_health_monitor()
        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        scheduler.TaskRunner(rsrc.delete)()
        self.assertEqual((rsrc.DELETE, rsrc.COMPLETE), rsrc.state)
        self.m.VerifyAll()

    def test_delete_failed(self):
        neutronclient.Client.delete_health_monitor('5678').AndRaise(
            loadbalancer.NeutronClientException(status_code=400))

        rsrc = self.create_health_monitor()
        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        error = self.assertRaises(exception.ResourceFailure,
                                  scheduler.TaskRunner(rsrc.delete))
        self.assertEqual(
            'NeutronClientException: An unknown exception occurred.',
            str(error))
        self.assertEqual((rsrc.DELETE, rsrc.FAILED), rsrc.state)
        self.m.VerifyAll()

    def test_attribute(self):
        rsrc = self.create_health_monitor()
        neutronclient.Client.show_health_monitor('5678').MultipleTimes(
        ).AndReturn(
            {'health_monitor': {'admin_state_up': True, 'delay': 3}})
        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        self.assertIs(True, rsrc.FnGetAtt('admin_state_up'))
        self.assertEqual(3, rsrc.FnGetAtt('delay'))
        self.m.VerifyAll()

    def test_attribute_failed(self):
        rsrc = self.create_health_monitor()
        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        error = self.assertRaises(exception.InvalidTemplateAttribute,
                                  rsrc.FnGetAtt, 'subnet_id')
        self.assertEqual(
            'The Referenced Attribute (monitor subnet_id) is incorrect.',
            str(error))
        self.m.VerifyAll()

    def test_update(self):
        rsrc = self.create_health_monitor()
        neutronclient.Client.update_health_monitor(
            '5678', {'health_monitor': {'delay': 10}})
        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()

        update_template = copy.deepcopy(rsrc.t)
        update_template['Properties']['delay'] = 10
        scheduler.TaskRunner(rsrc.update, update_template)()

        self.m.VerifyAll()


@skipIf(neutronclient is None, 'neutronclient unavailable')
class PoolTest(HeatTestCase):

    def setUp(self):
        super(PoolTest, self).setUp()
        self.m.StubOutWithMock(neutronclient.Client, 'create_pool')
        self.m.StubOutWithMock(neutronclient.Client, 'delete_pool')
        self.m.StubOutWithMock(neutronclient.Client, 'show_pool')
        self.m.StubOutWithMock(neutronclient.Client, 'update_pool')
        self.m.StubOutWithMock(neutronclient.Client,
                               'associate_health_monitor')
        self.m.StubOutWithMock(neutronclient.Client,
                               'disassociate_health_monitor')
        self.m.StubOutWithMock(neutronclient.Client, 'create_vip')
        self.m.StubOutWithMock(neutronclient.Client, 'delete_vip')
        self.m.StubOutWithMock(neutronclient.Client, 'show_vip')
        self.m.StubOutWithMock(clients.OpenStackClients, 'keystone')
        utils.setup_dummy_db()

    def create_pool(self):
        clients.OpenStackClients.keystone().AndReturn(
            fakes.FakeKeystoneClient())
        neutronclient.Client.create_pool({
            'pool': {
                'subnet_id': 'sub123', 'protocol': u'HTTP',
                'name': utils.PhysName('test_stack', 'pool'),
                'lb_method': 'ROUND_ROBIN', 'admin_state_up': True}}
        ).AndReturn({'pool': {'id': '5678'}})
        neutronclient.Client.create_vip({
            'vip': {
                'protocol': u'HTTP', 'name': 'pool.vip',
                'admin_state_up': True, 'subnet_id': u'sub123',
                'pool_id': '5678', 'protocol_port': 80}}
        ).AndReturn({'vip': {'id': 'xyz'}})
        neutronclient.Client.show_pool('5678').AndReturn(
            {'pool': {'status': 'ACTIVE'}})
        neutronclient.Client.show_vip('xyz').AndReturn(
            {'vip': {'status': 'ACTIVE'}})

        snippet = template_format.parse(pool_template)
        stack = utils.parse_stack(snippet)
        return loadbalancer.Pool(
            'pool', snippet['Resources']['pool'], stack)

    def test_create(self):
        rsrc = self.create_pool()
        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        self.assertEqual((rsrc.CREATE, rsrc.COMPLETE), rsrc.state)
        self.m.VerifyAll()

    def test_create_pending(self):
        clients.OpenStackClients.keystone().AndReturn(
            fakes.FakeKeystoneClient())
        neutronclient.Client.create_pool({
            'pool': {
                'subnet_id': 'sub123', 'protocol': u'HTTP',
                'name': utils.PhysName('test_stack', 'pool'),
                'lb_method': 'ROUND_ROBIN', 'admin_state_up': True}}
        ).AndReturn({'pool': {'id': '5678'}})
        neutronclient.Client.create_vip({
            'vip': {
                'protocol': u'HTTP', 'name': 'pool.vip',
                'admin_state_up': True, 'subnet_id': u'sub123',
                'pool_id': '5678', 'protocol_port': 80}}
        ).AndReturn({'vip': {'id': 'xyz'}})
        neutronclient.Client.show_pool('5678').AndReturn(
            {'pool': {'status': 'PENDING_CREATE'}})
        neutronclient.Client.show_pool('5678').MultipleTimes().AndReturn(
            {'pool': {'status': 'ACTIVE'}})
        neutronclient.Client.show_vip('xyz').AndReturn(
            {'vip': {'status': 'PENDING_CREATE'}})
        neutronclient.Client.show_vip('xyz').AndReturn(
            {'vip': {'status': 'ACTIVE'}})

        snippet = template_format.parse(pool_template)
        stack = utils.parse_stack(snippet)
        rsrc = loadbalancer.Pool(
            'pool', snippet['Resources']['pool'], stack)
        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        self.assertEqual((rsrc.CREATE, rsrc.COMPLETE), rsrc.state)
        self.m.VerifyAll()

    def test_create_failed_unexpected_status(self):
        clients.OpenStackClients.keystone().AndReturn(
            fakes.FakeKeystoneClient())
        neutronclient.Client.create_pool({
            'pool': {
                'subnet_id': 'sub123', 'protocol': u'HTTP',
                'name': utils.PhysName('test_stack', 'pool'),
                'lb_method': 'ROUND_ROBIN', 'admin_state_up': True}}
        ).AndReturn({'pool': {'id': '5678'}})
        neutronclient.Client.create_vip({
            'vip': {
                'protocol': u'HTTP', 'name': 'pool.vip',
                'admin_state_up': True, 'subnet_id': u'sub123',
                'pool_id': '5678', 'protocol_port': 80}}
        ).AndReturn({'vip': {'id': 'xyz'}})
        neutronclient.Client.show_pool('5678').AndReturn(
            {'pool': {'status': 'ERROR', 'name': '5678'}})

        snippet = template_format.parse(pool_template)
        stack = utils.parse_stack(snippet)
        rsrc = loadbalancer.Pool(
            'pool', snippet['Resources']['pool'], stack)
        self.m.ReplayAll()
        error = self.assertRaises(exception.ResourceFailure,
                                  scheduler.TaskRunner(rsrc.create))
        self.assertEqual(
            'Error: neutron report unexpected pool '
            'resource[5678] status[ERROR]',
            str(error))
        self.assertEqual((rsrc.CREATE, rsrc.FAILED), rsrc.state)
        self.m.VerifyAll()

    def test_create_failed_unexpected_vip_status(self):
        clients.OpenStackClients.keystone().AndReturn(
            fakes.FakeKeystoneClient())
        neutronclient.Client.create_pool({
            'pool': {
                'subnet_id': 'sub123', 'protocol': u'HTTP',
                'name': utils.PhysName('test_stack', 'pool'),
                'lb_method': 'ROUND_ROBIN', 'admin_state_up': True}}
        ).AndReturn({'pool': {'id': '5678'}})
        neutronclient.Client.create_vip({
            'vip': {
                'protocol': u'HTTP', 'name': 'pool.vip',
                'admin_state_up': True, 'subnet_id': u'sub123',
                'pool_id': '5678', 'protocol_port': 80}}
        ).AndReturn({'vip': {'id': 'xyz'}})
        neutronclient.Client.show_pool('5678').MultipleTimes().AndReturn(
            {'pool': {'status': 'ACTIVE'}})
        neutronclient.Client.show_vip('xyz').AndReturn(
            {'vip': {'status': 'ERROR', 'name': 'xyz'}})

        snippet = template_format.parse(pool_template)
        stack = utils.parse_stack(snippet)
        rsrc = loadbalancer.Pool(
            'pool', snippet['Resources']['pool'], stack)
        self.m.ReplayAll()
        error = self.assertRaises(exception.ResourceFailure,
                                  scheduler.TaskRunner(rsrc.create))
        self.assertEqual(
            'Error: neutron reported unexpected vip '
            'resource[xyz] status[ERROR]',
            str(error))
        self.assertEqual((rsrc.CREATE, rsrc.FAILED), rsrc.state)
        self.m.VerifyAll()

    def test_create_failed(self):
        clients.OpenStackClients.keystone().AndReturn(
            fakes.FakeKeystoneClient())
        neutronclient.Client.create_pool({
            'pool': {
                'subnet_id': 'sub123', 'protocol': u'HTTP',
                'name': utils.PhysName('test_stack', 'pool'),
                'lb_method': 'ROUND_ROBIN', 'admin_state_up': True}}
        ).AndRaise(loadbalancer.NeutronClientException())
        self.m.ReplayAll()

        snippet = template_format.parse(pool_template)
        stack = utils.parse_stack(snippet)
        rsrc = loadbalancer.Pool(
            'pool', snippet['Resources']['pool'], stack)
        error = self.assertRaises(exception.ResourceFailure,
                                  scheduler.TaskRunner(rsrc.create))
        self.assertEqual(
            'NeutronClientException: An unknown exception occurred.',
            str(error))
        self.assertEqual((rsrc.CREATE, rsrc.FAILED), rsrc.state)
        self.m.VerifyAll()

    def test_delete(self):
        rsrc = self.create_pool()
        neutronclient.Client.delete_vip('xyz')
        neutronclient.Client.show_vip('xyz').AndRaise(
            loadbalancer.NeutronClientException(status_code=404))
        neutronclient.Client.delete_pool('5678')
        neutronclient.Client.show_pool('5678').AndRaise(
            loadbalancer.NeutronClientException(status_code=404))
        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        scheduler.TaskRunner(rsrc.delete)()
        self.assertEqual((rsrc.DELETE, rsrc.COMPLETE), rsrc.state)
        self.m.VerifyAll()

    def test_delete_already_gone(self):
        neutronclient.Client.delete_vip('xyz').AndRaise(
            loadbalancer.NeutronClientException(status_code=404))
        neutronclient.Client.delete_pool('5678').AndRaise(
            loadbalancer.NeutronClientException(status_code=404))

        rsrc = self.create_pool()
        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        scheduler.TaskRunner(rsrc.delete)()
        self.assertEqual((rsrc.DELETE, rsrc.COMPLETE), rsrc.state)
        self.m.VerifyAll()

    def test_delete_vip_failed(self):
        neutronclient.Client.delete_vip('xyz').AndRaise(
            loadbalancer.NeutronClientException(status_code=400))

        rsrc = self.create_pool()
        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        error = self.assertRaises(exception.ResourceFailure,
                                  scheduler.TaskRunner(rsrc.delete))
        self.assertEqual(
            'NeutronClientException: An unknown exception occurred.',
            str(error))
        self.assertEqual((rsrc.DELETE, rsrc.FAILED), rsrc.state)
        self.m.VerifyAll()

    def test_delete_failed(self):
        neutronclient.Client.delete_vip('xyz').AndRaise(
            loadbalancer.NeutronClientException(status_code=404))
        neutronclient.Client.delete_pool('5678').AndRaise(
            loadbalancer.NeutronClientException(status_code=400))

        rsrc = self.create_pool()
        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        error = self.assertRaises(exception.ResourceFailure,
                                  scheduler.TaskRunner(rsrc.delete))
        self.assertEqual(
            'NeutronClientException: An unknown exception occurred.',
            str(error))
        self.assertEqual((rsrc.DELETE, rsrc.FAILED), rsrc.state)
        self.m.VerifyAll()

    def test_attribute(self):
        rsrc = self.create_pool()
        neutronclient.Client.show_pool('5678').MultipleTimes(
        ).AndReturn(
            {'pool': {'admin_state_up': True, 'lb_method': 'ROUND_ROBIN'}})
        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        self.assertIs(True, rsrc.FnGetAtt('admin_state_up'))
        self.assertEqual('ROUND_ROBIN', rsrc.FnGetAtt('lb_method'))
        self.m.VerifyAll()

    def test_vip_attribute(self):
        rsrc = self.create_pool()
        neutronclient.Client.show_vip('xyz').AndReturn(
            {'vip': {'address': '10.0.0.3', 'name': 'xyz'}})
        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        self.assertEqual({'address': '10.0.0.3', 'name': 'xyz'},
                         rsrc.FnGetAtt('vip'))
        self.m.VerifyAll()

    def test_attribute_failed(self):
        rsrc = self.create_pool()
        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        error = self.assertRaises(exception.InvalidTemplateAttribute,
                                  rsrc.FnGetAtt, 'net_id')
        self.assertEqual(
            'The Referenced Attribute (pool net_id) is incorrect.',
            str(error))
        self.m.VerifyAll()

    def test_update(self):
        rsrc = self.create_pool()
        neutronclient.Client.update_pool(
            '5678', {'pool': {'admin_state_up': False}})
        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()

        update_template = copy.deepcopy(rsrc.t)
        update_template['Properties']['admin_state_up'] = False
        scheduler.TaskRunner(rsrc.update, update_template)()

        self.m.VerifyAll()

    def test_update_monitors(self):
        clients.OpenStackClients.keystone().AndReturn(
            fakes.FakeKeystoneClient())
        neutronclient.Client.create_pool({
            'pool': {
                'subnet_id': 'sub123', 'protocol': u'HTTP',
                'name': utils.PhysName('test_stack', 'pool'),
                'lb_method': 'ROUND_ROBIN', 'admin_state_up': True}}
        ).AndReturn({'pool': {'id': '5678'}})
        neutronclient.Client.associate_health_monitor(
            '5678', {'health_monitor': {'id': 'mon123'}})
        neutronclient.Client.associate_health_monitor(
            '5678', {'health_monitor': {'id': 'mon456'}})
        neutronclient.Client.create_vip({
            'vip': {
                'protocol': u'HTTP', 'name': 'pool.vip',
                'admin_state_up': True, 'subnet_id': u'sub123',
                'pool_id': '5678', 'protocol_port': 80}}
        ).AndReturn({'vip': {'id': 'xyz'}})
        neutronclient.Client.show_pool('5678').AndReturn(
            {'pool': {'status': 'ACTIVE'}})
        neutronclient.Client.show_vip('xyz').AndReturn(
            {'vip': {'status': 'ACTIVE'}})
        neutronclient.Client.disassociate_health_monitor(
            '5678', {'health_monitor': {'id': 'mon456'}})
        neutronclient.Client.associate_health_monitor(
            '5678', {'health_monitor': {'id': 'mon789'}})

        snippet = template_format.parse(pool_template)
        stack = utils.parse_stack(snippet)
        snippet['Resources']['pool']['Properties']['monitors'] = [
            'mon123', 'mon456']
        rsrc = loadbalancer.Pool(
            'pool', snippet['Resources']['pool'], stack)
        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()

        update_template = copy.deepcopy(rsrc.t)
        update_template['Properties']['monitors'] = ['mon123', 'mon789']
        scheduler.TaskRunner(rsrc.update, update_template)()

        self.m.VerifyAll()


@skipIf(neutronclient is None, 'neutronclient unavailable')
class PoolMemberTest(HeatTestCase):

    def setUp(self):
        super(PoolMemberTest, self).setUp()
        self.fc = nova_fakes.FakeClient()
        self.m.StubOutWithMock(neutronclient.Client, 'create_member')
        self.m.StubOutWithMock(neutronclient.Client, 'delete_member')
        self.m.StubOutWithMock(neutronclient.Client, 'update_member')
        self.m.StubOutWithMock(neutronclient.Client, 'show_member')
        self.m.StubOutWithMock(clients.OpenStackClients, 'keystone')
        utils.setup_dummy_db()

    def create_member(self):
        clients.OpenStackClients.keystone().AndReturn(
            fakes.FakeKeystoneClient())
        neutronclient.Client.create_member({
            'member': {
                'pool_id': 'pool123', 'protocol_port': 8080,
                'address': '1.2.3.4', 'admin_state_up': True}}
        ).AndReturn({'member': {'id': 'member5678'}})
        snippet = template_format.parse(member_template)
        stack = utils.parse_stack(snippet)
        return loadbalancer.PoolMember(
            'member', snippet['Resources']['member'], stack)

    def test_create(self):
        rsrc = self.create_member()

        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        self.assertEqual((rsrc.CREATE, rsrc.COMPLETE), rsrc.state)
        self.assertEqual('member5678', rsrc.resource_id)
        self.m.VerifyAll()

    def test_create_optional_parameters(self):
        clients.OpenStackClients.keystone().AndReturn(
            fakes.FakeKeystoneClient())
        neutronclient.Client.create_member({
            'member': {
                'pool_id': 'pool123', 'protocol_port': 8080,
                'weight': 100, 'admin_state_up': False,
                'address': '1.2.3.4'}}
        ).AndReturn({'member': {'id': 'member5678'}})
        snippet = template_format.parse(member_template)
        snippet['Resources']['member']['Properties']['admin_state_up'] = False
        snippet['Resources']['member']['Properties']['weight'] = 100
        stack = utils.parse_stack(snippet)
        rsrc = loadbalancer.PoolMember(
            'member', snippet['Resources']['member'], stack)

        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        self.assertEqual((rsrc.CREATE, rsrc.COMPLETE), rsrc.state)
        self.assertEqual('member5678', rsrc.resource_id)
        self.m.VerifyAll()

    def test_attribute(self):
        rsrc = self.create_member()
        neutronclient.Client.show_member('member5678').MultipleTimes(
        ).AndReturn(
            {'member': {'admin_state_up': True, 'weight': 5}})
        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        self.assertIs(True, rsrc.FnGetAtt('admin_state_up'))
        self.assertEqual(5, rsrc.FnGetAtt('weight'))
        self.m.VerifyAll()

    def test_update(self):
        rsrc = self.create_member()
        neutronclient.Client.update_member(
            'member5678', {'member': {'pool_id': 'pool456'}})

        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()

        update_template = copy.deepcopy(rsrc.t)
        update_template['Properties']['pool_id'] = 'pool456'

        scheduler.TaskRunner(rsrc.update, update_template)()
        self.m.VerifyAll()

    def test_delete(self):
        rsrc = self.create_member()
        neutronclient.Client.delete_member(u'member5678')
        neutronclient.Client.show_member(u'member5678').AndRaise(
            loadbalancer.NeutronClientException(status_code=404))

        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        scheduler.TaskRunner(rsrc.delete)()
        self.assertEqual((rsrc.DELETE, rsrc.COMPLETE), rsrc.state)
        self.m.VerifyAll()

    def test_delete_missing_member(self):
        rsrc = self.create_member()
        neutronclient.Client.delete_member(u'member5678').AndRaise(
            loadbalancer.NeutronClientException(status_code=404))

        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        scheduler.TaskRunner(rsrc.delete)()
        self.assertEqual((rsrc.DELETE, rsrc.COMPLETE), rsrc.state)
        self.m.VerifyAll()


@skipIf(neutronclient is None, 'neutronclient unavailable')
class LoadBalancerTest(HeatTestCase):

    def setUp(self):
        super(LoadBalancerTest, self).setUp()
        self.fc = nova_fakes.FakeClient()
        self.m.StubOutWithMock(neutronclient.Client, 'create_member')
        self.m.StubOutWithMock(neutronclient.Client, 'delete_member')
        self.m.StubOutWithMock(clients.OpenStackClients, 'keystone')
        self.m.StubOutWithMock(clients.OpenStackClients, 'nova')
        utils.setup_dummy_db()

    def create_load_balancer(self):
        clients.OpenStackClients.keystone().AndReturn(
            fakes.FakeKeystoneClient())
        clients.OpenStackClients.nova("compute").MultipleTimes().AndReturn(
            self.fc)
        neutronclient.Client.create_member({
            'member': {
                'pool_id': 'pool123', 'protocol_port': 8080,
                'address': '1.2.3.4'}}
        ).AndReturn({'member': {'id': 'member5678'}})
        snippet = template_format.parse(lb_template)
        stack = utils.parse_stack(snippet)
        return loadbalancer.LoadBalancer(
            'lb', snippet['Resources']['lb'], stack)

    def test_create(self):
        rsrc = self.create_load_balancer()

        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        self.assertEqual((rsrc.CREATE, rsrc.COMPLETE), rsrc.state)
        self.m.VerifyAll()

    def test_update(self):
        rsrc = self.create_load_balancer()
        neutronclient.Client.delete_member(u'member5678')
        neutronclient.Client.create_member({
            'member': {
                'pool_id': 'pool123', 'protocol_port': 8080,
                'address': '4.5.6.7'}}
        ).AndReturn({'member': {'id': 'memberxyz'}})

        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()

        update_template = copy.deepcopy(rsrc.t)
        update_template['Properties']['members'] = ['5678']

        scheduler.TaskRunner(rsrc.update, update_template)()
        self.m.VerifyAll()

    def test_update_missing_member(self):
        rsrc = self.create_load_balancer()
        neutronclient.Client.delete_member(u'member5678').AndRaise(
            loadbalancer.NeutronClientException(status_code=404))

        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()

        update_template = copy.deepcopy(rsrc.t)
        update_template['Properties']['members'] = []

        scheduler.TaskRunner(rsrc.update, update_template)()
        self.assertEqual((rsrc.UPDATE, rsrc.COMPLETE), rsrc.state)
        self.m.VerifyAll()

    def test_delete(self):
        rsrc = self.create_load_balancer()
        neutronclient.Client.delete_member(u'member5678')

        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        scheduler.TaskRunner(rsrc.delete)()
        self.assertEqual((rsrc.DELETE, rsrc.COMPLETE), rsrc.state)
        self.m.VerifyAll()

    def test_delete_missing_member(self):
        rsrc = self.create_load_balancer()
        neutronclient.Client.delete_member(u'member5678').AndRaise(
            loadbalancer.NeutronClientException(status_code=404))

        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.create)()
        scheduler.TaskRunner(rsrc.delete)()
        self.assertEqual((rsrc.DELETE, rsrc.COMPLETE), rsrc.state)
        self.m.VerifyAll()
