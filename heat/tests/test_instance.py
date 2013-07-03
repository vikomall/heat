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

import mox

from heat.engine import environment
from heat.tests.v1_1 import fakes
from heat.engine.resources import instance as instances
from heat.common import exception
from heat.common import template_format
from heat.engine import parser
from heat.engine import resource
from heat.engine import scheduler
from heat.openstack.common import uuidutils
from heat.tests.common import HeatTestCase
from heat.tests import utils
from heat.tests.utils import setup_dummy_db


wp_template = '''
{
  "AWSTemplateFormatVersion" : "2010-09-09",
  "Description" : "WordPress",
  "Parameters" : {
    "KeyName" : {
      "Description" : "KeyName",
      "Type" : "String",
      "Default" : "test"
    }
  },
  "Resources" : {
    "WebServer": {
      "Type": "AWS::EC2::Instance",
      "Properties": {
        "ImageId" : "F17-x86_64-gold",
        "InstanceType"   : "m1.large",
        "KeyName"        : "test",
        "UserData"       : "wordpress"
      }
    }
  }
}
'''


class instancesTest(HeatTestCase):
    def setUp(self):
        super(instancesTest, self).setUp()
        self.fc = fakes.FakeClient()
        setup_dummy_db()

    def _setup_test_stack(self, stack_name):
        t = template_format.parse(wp_template)
        template = parser.Template(t)
        stack = parser.Stack(None, stack_name, template,
                             environment.Environment({'KeyName': 'test'}),
                             stack_id=uuidutils.generate_uuid())
        return (t, stack)

    def _setup_test_instance(self, return_server, name, image_id=None):
        stack_name = '%s_stack' % name
        (t, stack) = self._setup_test_stack(stack_name)

        t['Resources']['WebServer']['Properties']['ImageId'] = \
            image_id or 'CentOS 5.2'
        t['Resources']['WebServer']['Properties']['InstanceType'] = \
            '256 MB Server'
        instance = instances.Instance('%s_name' % name,
                                      t['Resources']['WebServer'], stack)

        self.m.StubOutWithMock(instance, 'nova')
        instance.nova().MultipleTimes().AndReturn(self.fc)

        instance.t = instance.stack.resolve_runtime_data(instance.t)

        # need to resolve the template functions
        server_userdata = instance._build_userdata(
            instance.t['Properties']['UserData'])
        self.m.StubOutWithMock(self.fc.servers, 'create')
        self.fc.servers.create(
            image=1, flavor=1, key_name='test',
            name=utils.PhysName(stack_name, instance.name),
            security_groups=None,
            userdata=server_userdata, scheduler_hints=None,
            meta=None, nics=None, availability_zone=None).AndReturn(
                return_server)

        return instance

    def _create_test_instance(self, return_server, name):
        instance = self._setup_test_instance(return_server, name)
        self.m.ReplayAll()
        scheduler.TaskRunner(instance.create)()
        return instance

    def test_instance_create(self):
        return_server = self.fc.servers.list()[1]
        instance = self._create_test_instance(return_server,
                                              'test_instance_create')
        # this makes sure the auto increment worked on instance creation
        self.assertTrue(instance.id > 0)

        expected_ip = return_server.networks['public'][0]
        self.assertEqual(instance.FnGetAtt('PublicIp'), expected_ip)
        self.assertEqual(instance.FnGetAtt('PrivateIp'), expected_ip)
        self.assertEqual(instance.FnGetAtt('PrivateDnsName'), expected_ip)
        self.assertEqual(instance.FnGetAtt('PrivateDnsName'), expected_ip)

        self.m.VerifyAll()

    def test_instance_create_with_image_id(self):
        return_server = self.fc.servers.list()[1]
        instance = self._setup_test_instance(return_server,
                                             'test_instance_create_image_id',
                                             image_id='1')
        self.m.StubOutWithMock(uuidutils, "is_uuid_like")
        uuidutils.is_uuid_like('1').AndReturn(True)

        self.m.ReplayAll()
        scheduler.TaskRunner(instance.create)()

        # this makes sure the auto increment worked on instance creation
        self.assertTrue(instance.id > 0)

        expected_ip = return_server.networks['public'][0]
        self.assertEqual(instance.FnGetAtt('PublicIp'), expected_ip)
        self.assertEqual(instance.FnGetAtt('PrivateIp'), expected_ip)
        self.assertEqual(instance.FnGetAtt('PrivateDnsName'), expected_ip)
        self.assertEqual(instance.FnGetAtt('PrivateDnsName'), expected_ip)

        self.m.VerifyAll()

    def test_instance_create_image_name_err(self):
        stack_name = 'test_instance_create_image_name_err_stack'
        (t, stack) = self._setup_test_stack(stack_name)

        # create an instance with non exist image name
        t['Resources']['WebServer']['Properties']['ImageId'] = 'Slackware'
        instance = instances.Instance('instance_create_image_err',
                                      t['Resources']['WebServer'], stack)

        self.m.StubOutWithMock(instance, 'nova')
        instance.nova().MultipleTimes().AndReturn(self.fc)
        self.m.ReplayAll()

        self.assertRaises(exception.ImageNotFound, instance.handle_create)

        self.m.VerifyAll()

    def test_instance_create_duplicate_image_name_err(self):
        stack_name = 'test_instance_create_image_name_err_stack'
        (t, stack) = self._setup_test_stack(stack_name)

        # create an instance with a non unique image name
        t['Resources']['WebServer']['Properties']['ImageId'] = 'CentOS 5.2'
        instance = instances.Instance('instance_create_image_err',
                                      t['Resources']['WebServer'], stack)

        self.m.StubOutWithMock(instance, 'nova')
        instance.nova().MultipleTimes().AndReturn(self.fc)
        self.m.StubOutWithMock(self.fc.client, "get_images_detail")
        self.fc.client.get_images_detail().AndReturn((
            200, {'images': [{'id': 1, 'name': 'CentOS 5.2'},
                             {'id': 4, 'name': 'CentOS 5.2'}]}))
        self.m.ReplayAll()

        self.assertRaises(exception.NoUniqueImageFound, instance.handle_create)

        self.m.VerifyAll()

    def test_instance_create_image_id_err(self):
        stack_name = 'test_instance_create_image_id_err_stack'
        (t, stack) = self._setup_test_stack(stack_name)

        # create an instance with non exist image Id
        t['Resources']['WebServer']['Properties']['ImageId'] = '1'
        instance = instances.Instance('instance_create_image_err',
                                      t['Resources']['WebServer'], stack)

        self.m.StubOutWithMock(instance, 'nova')
        instance.nova().MultipleTimes().AndReturn(self.fc)
        self.m.StubOutWithMock(uuidutils, "is_uuid_like")
        uuidutils.is_uuid_like('1').AndReturn(True)
        self.m.StubOutWithMock(self.fc.client, "get_images_1")
        self.fc.client.get_images_1().AndRaise(
            instances.clients.novaclient.exceptions.NotFound(404))
        self.m.ReplayAll()

        self.assertRaises(exception.ImageNotFound, instance.handle_create)

        self.m.VerifyAll()

    def test_instance_validate(self):
        stack_name = 'test_instance_validate_stack'
        (t, stack) = self._setup_test_stack(stack_name)

        # create an instance with non exist image Id
        t['Resources']['WebServer']['Properties']['ImageId'] = '1'
        instance = instances.Instance('instance_create_image_err',
                                      t['Resources']['WebServer'], stack)

        self.m.StubOutWithMock(instance, 'nova')
        instance.nova().MultipleTimes().AndReturn(self.fc)

        self.m.StubOutWithMock(uuidutils, "is_uuid_like")
        uuidutils.is_uuid_like('1').AndReturn(True)
        self.m.ReplayAll()

        self.assertEqual(instance.validate(), None)

        self.m.VerifyAll()

    def test_instance_create_delete(self):
        return_server = self.fc.servers.list()[1]
        instance = self._create_test_instance(return_server,
                                              'test_instance_create_delete')
        instance.resource_id = 1234

        # this makes sure the auto increment worked on instance creation
        self.assertTrue(instance.id > 0)

        self.m.StubOutWithMock(self.fc.client, 'get_servers_1234')
        get = self.fc.client.get_servers_1234
        get().AndRaise(instances.clients.novaclient.exceptions.NotFound(404))
        mox.Replay(get)

        instance.delete()
        self.assertTrue(instance.resource_id is None)
        self.assertEqual(instance.state, (instance.DELETE, instance.COMPLETE))
        self.m.VerifyAll()

    def test_instance_update_metadata(self):
        return_server = self.fc.servers.list()[1]
        instance = self._create_test_instance(return_server,
                                              'test_instance_update')

        update_template = copy.deepcopy(instance.t)
        update_template['Metadata'] = {'test': 123}
        self.assertEqual(None, instance.update(update_template))
        self.assertEqual(instance.metadata, {'test': 123})

    def test_instance_update_replace(self):
        return_server = self.fc.servers.list()[1]
        instance = self._create_test_instance(return_server,
                                              'test_instance_update')

        update_template = copy.deepcopy(instance.t)
        update_template['Notallowed'] = {'test': 123}
        self.assertRaises(resource.UpdateReplace,
                          instance.update, update_template)

    def test_instance_update_properties(self):
        return_server = self.fc.servers.list()[1]
        instance = self._create_test_instance(return_server,
                                              'test_instance_update')

        update_template = copy.deepcopy(instance.t)
        update_template['Properties']['KeyName'] = 'mustreplace'
        self.assertRaises(resource.UpdateReplace,
                          instance.update, update_template)

    def test_instance_status_build(self):
        return_server = self.fc.servers.list()[0]
        instance = self._setup_test_instance(return_server,
                                             'test_instance_status_build')
        instance.resource_id = 1234

        # Bind fake get method which Instance.check_create_complete will call
        def activate_status(server):
            server.status = 'ACTIVE'
        return_server.get = activate_status.__get__(return_server)
        self.m.ReplayAll()

        scheduler.TaskRunner(instance.create)()
        self.assertEqual(instance.state, (instance.CREATE, instance.COMPLETE))

    def test_instance_status_suspend_immediate(self):
        return_server = self.fc.servers.list()[1]
        instance = self._create_test_instance(return_server,
                                              'test_instance_suspend')

        instance.resource_id = 1234
        self.m.ReplayAll()

        # Override the get_servers_1234 handler status to SUSPENDED
        d = {'server': self.fc.client.get_servers_detail()[1]['servers'][0]}
        d['server']['status'] = 'SUSPENDED'
        self.m.StubOutWithMock(self.fc.client, 'get_servers_1234')
        get = self.fc.client.get_servers_1234
        get().AndReturn((200, d))
        mox.Replay(get)

        scheduler.TaskRunner(instance.suspend)()
        self.assertEqual(instance.state, (instance.SUSPEND, instance.COMPLETE))

        self.m.VerifyAll()

    def test_instance_status_resume_immediate(self):
        return_server = self.fc.servers.list()[1]
        instance = self._create_test_instance(return_server,
                                              'test_instance_resume')

        instance.resource_id = 1234
        self.m.ReplayAll()

        # Override the get_servers_1234 handler status to SUSPENDED
        d = {'server': self.fc.client.get_servers_detail()[1]['servers'][0]}
        d['server']['status'] = 'ACTIVE'
        self.m.StubOutWithMock(self.fc.client, 'get_servers_1234')
        get = self.fc.client.get_servers_1234
        get().AndReturn((200, d))
        mox.Replay(get)
        instance.state_set(instance.SUSPEND, instance.COMPLETE)

        scheduler.TaskRunner(instance.resume)()
        self.assertEqual(instance.state, (instance.RESUME, instance.COMPLETE))

        self.m.VerifyAll()

    def test_instance_status_suspend_wait(self):
        return_server = self.fc.servers.list()[1]
        instance = self._create_test_instance(return_server,
                                              'test_instance_suspend')

        instance.resource_id = 1234
        self.m.ReplayAll()

        # Override the get_servers_1234 handler status to SUSPENDED, but
        # return the ACTIVE state first (twice, so we sleep)
        d1 = {'server': self.fc.client.get_servers_detail()[1]['servers'][0]}
        d2 = copy.deepcopy(d1)
        d1['server']['status'] = 'ACTIVE'
        d2['server']['status'] = 'SUSPENDED'
        self.m.StubOutWithMock(self.fc.client, 'get_servers_1234')
        get = self.fc.client.get_servers_1234
        get().AndReturn((200, d1))
        get().AndReturn((200, d1))
        self.m.StubOutWithMock(scheduler.TaskRunner, '_sleep')
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        get().AndReturn((200, d2))
        self.m.ReplayAll()

        scheduler.TaskRunner(instance.suspend)()
        self.assertEqual(instance.state, (instance.SUSPEND, instance.COMPLETE))

        self.m.VerifyAll()

    def test_instance_status_resume_wait(self):
        return_server = self.fc.servers.list()[1]
        instance = self._create_test_instance(return_server,
                                              'test_instance_resume')

        instance.resource_id = 1234
        self.m.ReplayAll()

        # Override the get_servers_1234 handler status to ACTIVE, but
        # return the SUSPENDED state first (twice, so we sleep)
        d1 = {'server': self.fc.client.get_servers_detail()[1]['servers'][0]}
        d2 = copy.deepcopy(d1)
        d1['server']['status'] = 'SUSPENDED'
        d2['server']['status'] = 'ACTIVE'
        self.m.StubOutWithMock(self.fc.client, 'get_servers_1234')
        get = self.fc.client.get_servers_1234
        get().AndReturn((200, d1))
        get().AndReturn((200, d1))
        self.m.StubOutWithMock(scheduler.TaskRunner, '_sleep')
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        get().AndReturn((200, d2))
        self.m.ReplayAll()

        instance.state_set(instance.SUSPEND, instance.COMPLETE)

        scheduler.TaskRunner(instance.resume)()
        self.assertEqual(instance.state, (instance.RESUME, instance.COMPLETE))

        self.m.VerifyAll()

    def test_instance_suspend_volumes_step(self):
        return_server = self.fc.servers.list()[1]
        instance = self._create_test_instance(return_server,
                                              'test_instance_suspend')

        instance.resource_id = 1234
        self.m.ReplayAll()

        # Override the get_servers_1234 handler status to SUSPENDED
        d = {'server': self.fc.client.get_servers_detail()[1]['servers'][0]}
        d['server']['status'] = 'SUSPENDED'

        # Return a dummy PollingTaskGroup to make check_suspend_complete step
        def dummy_detach():
            yield
        dummy_tg = scheduler.PollingTaskGroup([dummy_detach, dummy_detach])
        self.m.StubOutWithMock(instance, '_detach_volumes_task')
        instance._detach_volumes_task().AndReturn(dummy_tg)

        self.m.StubOutWithMock(self.fc.client, 'get_servers_1234')
        get = self.fc.client.get_servers_1234
        get().AndReturn((200, d))
        self.m.ReplayAll()

        scheduler.TaskRunner(instance.suspend)()
        self.assertEqual(instance.state, (instance.SUSPEND, instance.COMPLETE))

        self.m.VerifyAll()

    def test_instance_resume_volumes_step(self):
        return_server = self.fc.servers.list()[1]
        instance = self._create_test_instance(return_server,
                                              'test_instance_resume')

        instance.resource_id = 1234
        self.m.ReplayAll()

        # Override the get_servers_1234 handler status to ACTIVE
        d = {'server': self.fc.client.get_servers_detail()[1]['servers'][0]}
        d['server']['status'] = 'ACTIVE'

        # Return a dummy PollingTaskGroup to make check_resume_complete step
        def dummy_attach():
            yield
        dummy_tg = scheduler.PollingTaskGroup([dummy_attach, dummy_attach])
        self.m.StubOutWithMock(instance, '_attach_volumes_task')
        instance._attach_volumes_task().AndReturn(dummy_tg)

        self.m.StubOutWithMock(self.fc.client, 'get_servers_1234')
        get = self.fc.client.get_servers_1234
        get().AndReturn((200, d))

        self.m.StubOutWithMock(scheduler.TaskRunner, '_sleep')
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        self.m.ReplayAll()

        instance.state_set(instance.SUSPEND, instance.COMPLETE)

        scheduler.TaskRunner(instance.resume)()
        self.assertEqual(instance.state, (instance.RESUME, instance.COMPLETE))

        self.m.VerifyAll()

    def test_instance_status_build_spawning(self):
        self._test_instance_status_not_build_active('BUILD(SPAWNING)')

    def test_instance_status_hard_reboot(self):
        self._test_instance_status_not_build_active('HARD_REBOOT')

    def test_instance_status_password(self):
        self._test_instance_status_not_build_active('PASSWORD')

    def test_instance_status_reboot(self):
        self._test_instance_status_not_build_active('REBOOT')

    def test_instance_status_rescue(self):
        self._test_instance_status_not_build_active('RESCUE')

    def test_instance_status_resize(self):
        self._test_instance_status_not_build_active('RESIZE')

    def test_instance_status_revert_resize(self):
        self._test_instance_status_not_build_active('REVERT_RESIZE')

    def test_instance_status_shutoff(self):
        self._test_instance_status_not_build_active('SHUTOFF')

    def test_instance_status_suspended(self):
        self._test_instance_status_not_build_active('SUSPENDED')

    def test_instance_status_verify_resize(self):
        self._test_instance_status_not_build_active('VERIFY_RESIZE')

    def _test_instance_status_not_build_active(self, uncommon_status):
        return_server = self.fc.servers.list()[0]
        instance = self._setup_test_instance(return_server,
                                             'test_instance_status_build')
        instance.resource_id = 1234

        # Bind fake get method which Instance.check_create_complete will call
        def activate_status(server):
            if hasattr(server, '_test_check_iterations'):
                server._test_check_iterations += 1
            else:
                server._test_check_iterations = 1
            if server._test_check_iterations == 1:
                server.status = uncommon_status
            if server._test_check_iterations > 2:
                server.status = 'ACTIVE'
        return_server.get = activate_status.__get__(return_server)
        self.m.StubOutWithMock(scheduler.TaskRunner, '_sleep')
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        scheduler.TaskRunner._sleep(mox.IsA(int)).AndReturn(None)
        self.m.ReplayAll()

        scheduler.TaskRunner(instance.create)()
        self.assertEqual(instance.state, (instance.CREATE, instance.COMPLETE))

        self.m.VerifyAll()

    def test_build_nics(self):
        return_server = self.fc.servers.list()[1]
        instance = self._create_test_instance(return_server,
                                              'test_build_nics')

        self.assertEqual(None, instance._build_nics([]))
        self.assertEqual(None, instance._build_nics(None))
        self.assertEqual([
            {'port-id': 'id3'}, {'port-id': 'id1'}, {'port-id': 'id2'}],
            instance._build_nics([
                'id3', 'id1', 'id2']))
        self.assertEqual([
            {'port-id': 'id1'},
            {'port-id': 'id2'},
            {'port-id': 'id3'}], instance._build_nics([
                {'NetworkInterfaceId': 'id3', 'DeviceIndex': '3'},
                {'NetworkInterfaceId': 'id1', 'DeviceIndex': '1'},
                {'NetworkInterfaceId': 'id2', 'DeviceIndex': 2},
            ]))
        self.assertEqual([
            {'port-id': 'id1'},
            {'port-id': 'id2'},
            {'port-id': 'id3'},
            {'port-id': 'id4'},
            {'port-id': 'id5'}
        ], instance._build_nics([
            {'NetworkInterfaceId': 'id3', 'DeviceIndex': '3'},
            {'NetworkInterfaceId': 'id1', 'DeviceIndex': '1'},
            {'NetworkInterfaceId': 'id2', 'DeviceIndex': 2},
            'id4',
            'id5'
        ]))

    def test_instance_without_ip_address(self):
        return_server = self.fc.servers.list()[3]
        instance = self._create_test_instance(return_server,
                                              'test_without_ip_address')

        self.assertEqual(instance.FnGetAtt('PrivateIp'), '0.0.0.0')
