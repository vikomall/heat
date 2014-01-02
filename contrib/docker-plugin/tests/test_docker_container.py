# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
# Copyright (c) 2013 Docker, Inc.
# All Rights Reserved.
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

from heat.common import exception
from heat.common import template_format
from heat.engine import resource
from heat.engine import scheduler
from heat.tests.common import HeatTestCase
from heat.tests import utils

from .fake_docker_client import FakeDockerClient  # noqa
from ..plugin import docker_container  # noqa


template = '''
{
    "AWSTemplateFormatVersion" : "2010-09-09",
    "Description": "Test template",
    "Parameters": {},
    "Resources": {
        "Blog": {
            "Type": "OS::Docker::Container",
            "Properties": {
                "image": "samalba/wordpress",
                "env": [
                    "FOO=bar"
                ]
            }
        }
    }
}
'''


class DockerContainerTest(HeatTestCase):

    def setUp(self):
        super(DockerContainerTest, self).setUp()
        utils.setup_dummy_db()
        resource._register_class('OS::Docker::Container',
                                 docker_container.DockerContainer)

    def create_container(self, resource_name):
        t = template_format.parse(template)
        stack = utils.parse_stack(t)
        resource = docker_container.DockerContainer(
            resource_name, t['Resources'][resource_name], stack)
        self.m.StubOutWithMock(resource, 'get_client')
        resource.get_client().MultipleTimes().AndReturn(FakeDockerClient())
        self.assertEqual(None, resource.validate())
        self.m.ReplayAll()
        scheduler.TaskRunner(resource.create)()
        self.assertEqual(resource.state, (resource.CREATE,
                                          resource.COMPLETE))
        return resource

    def get_container_state(self, resource):
        client = resource.get_client()
        return client.inspect_container(resource.resource_id)['State']

    def test_resource_create(self):
        container = self.create_container('Blog')
        self.assertTrue(container.resource_id)
        running = self.get_container_state(container)['Running']
        self.assertEqual(True, running)
        self.m.VerifyAll()

    def test_resource_attributes(self):
        container = self.create_container('Blog')
        # Test network info attributes
        self.assertEqual('172.17.42.1', container.FnGetAtt('network_ip'))
        self.assertEqual('1080', container.FnGetAtt('network_tcp_ports'))
        self.assertEqual('', container.FnGetAtt('network_udp_ports'))
        # Test logs attributes
        self.assertEqual('---logs_begin---', container.FnGetAtt('logs_head'))
        self.assertEqual('---logs_end---', container.FnGetAtt('logs_tail'))
        # Test a non existing attribute
        self.assertRaises(exception.InvalidTemplateAttribute,
                          container.FnGetAtt, 'invalid_attribute')
        self.m.VerifyAll()

    def test_resource_delete(self):
        container = self.create_container('Blog')
        scheduler.TaskRunner(container.delete)()
        self.assertEqual(container.state, (container.DELETE,
                                           container.COMPLETE))
        running = self.get_container_state(container)['Running']
        self.assertEqual(False, running)
        self.m.VerifyAll()

    def test_resource_suspend_resume(self):
        container = self.create_container('Blog')
        # Test suspend
        scheduler.TaskRunner(container.suspend)()
        self.assertEqual(container.state, (container.SUSPEND,
                                           container.COMPLETE))
        running = self.get_container_state(container)['Running']
        self.assertEqual(False, running)
        # Test resume
        scheduler.TaskRunner(container.resume)()
        self.assertEqual(container.state, (container.RESUME,
                                           container.COMPLETE))
        running = self.get_container_state(container)['Running']
        self.assertEqual(True, running)
        self.m.VerifyAll()
