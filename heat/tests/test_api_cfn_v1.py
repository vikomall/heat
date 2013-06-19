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

import json
import os

from oslo.config import cfg

from heat.common import context
from heat.common import identifier
from heat.common import policy
from heat.openstack.common import rpc
import heat.openstack.common.rpc.common as rpc_common
from heat.common.wsgi import Request
from heat.rpc import api as rpc_api
from heat.api.aws import exception
import heat.api.cfn.v1.stacks as stacks
from heat.tests.common import HeatTestCase

policy_path = os.path.dirname(os.path.realpath(__file__)) + "/policy/"


class CfnStackControllerTest(HeatTestCase):
    '''
    Tests the API class which acts as the WSGI controller,
    the endpoint processing API requests after they are routed
    '''
    # Utility functions
    def _create_context(self, user='api_test_user'):
        ctx = context.get_admin_context()
        self.m.StubOutWithMock(ctx, 'username')
        ctx.username = user
        self.m.StubOutWithMock(ctx, 'tenant_id')
        ctx.tenant_id = 't'
        return ctx

    def _dummy_GET_request(self, params={}):
        # Mangle the params dict into a query string
        qs = "&".join(["=".join([k, str(params[k])]) for k in params])
        environ = {'REQUEST_METHOD': 'GET', 'QUERY_STRING': qs}
        req = Request(environ)
        req.context = self._create_context()
        return req

    # The tests
    def test_stackid_addprefix(self):
        self.m.ReplayAll()

        response = self.controller._id_format({
            'StackName': 'Foo',
            'StackId': {
                u'tenant': u't',
                u'stack_name': u'Foo',
                u'stack_id': u'123',
                u'path': u''
            }
        })
        expected = {'StackName': 'Foo',
                    'StackId': 'arn:openstack:heat::t:stacks/Foo/123'}
        self.assertEqual(response, expected)
        self.m.VerifyAll()

    def test_enforce_default(self):
        self.m.ReplayAll()
        params = {'Action': 'ListStacks'}
        dummy_req = self._dummy_GET_request(params)
        self.controller.policy.policy_path = None
        response = self.controller._enforce(dummy_req, 'ListStacks')
        self.assertEqual(response, None)
        self.m.VerifyAll()

    def test_enforce_denied(self):
        self.m.ReplayAll()
        params = {'Action': 'ListStacks'}
        dummy_req = self._dummy_GET_request(params)
        dummy_req.context.roles = ['heat_stack_user']
        self.controller.policy.policy_path = (policy_path +
                                              'deny_stack_user.json')
        self.assertRaises(exception.HeatAccessDeniedError,
                          self.controller._enforce, dummy_req, 'ListStacks')
        self.m.VerifyAll()

    def test_enforce_ise(self):
        params = {'Action': 'ListStacks'}
        dummy_req = self._dummy_GET_request(params)
        dummy_req.context.roles = ['heat_stack_user']

        self.m.StubOutWithMock(policy.Enforcer, 'enforce')
        policy.Enforcer.enforce(dummy_req.context, 'ListStacks', {}
                                ).AndRaise(AttributeError)
        self.m.ReplayAll()

        self.controller.policy.policy_path = (policy_path +
                                              'deny_stack_user.json')
        self.assertRaises(exception.HeatInternalFailureError,
                          self.controller._enforce, dummy_req, 'ListStacks')
        self.m.VerifyAll()

    def test_list(self):
        # Format a dummy GET request to pass into the WSGI handler
        params = {'Action': 'ListStacks'}
        dummy_req = self._dummy_GET_request(params)

        # Stub out the RPC call to the engine with a pre-canned response
        engine_resp = [{u'stack_identity': {u'tenant': u't',
                                            u'stack_name': u'wordpress',
                                            u'stack_id': u'1',
                                            u'path': u''},
                        u'updated_time': u'2012-07-09T09:13:11Z',
                        u'template_description': u'blah',
                        u'stack_status_reason': u'Stack successfully created',
                        u'creation_time': u'2012-07-09T09:12:45Z',
                        u'stack_name': u'wordpress',
                        u'stack_status': u'CREATE_COMPLETE'}]
        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'list_stacks',
                  'args': {},
                  'version': self.api_version},
                 None).AndReturn(engine_resp)

        self.m.ReplayAll()

        # Call the list controller function and compare the response
        result = self.controller.list(dummy_req)
        expected = {'ListStacksResponse': {'ListStacksResult':
                    {'StackSummaries':
                    [{u'StackId': u'arn:openstack:heat::t:stacks/wordpress/1',
                      u'LastUpdatedTime': u'2012-07-09T09:13:11Z',
                      u'TemplateDescription': u'blah',
                      u'StackStatusReason': u'Stack successfully created',
                      u'CreationTime': u'2012-07-09T09:12:45Z',
                      u'StackName': u'wordpress',
                      u'StackStatus': u'CREATE_COMPLETE'}]}}}
        self.assertEqual(result, expected)
        self.m.VerifyAll()

    def test_list_rmt_aterr(self):
        params = {'Action': 'ListStacks'}
        dummy_req = self._dummy_GET_request(params)

        # Insert an engine RPC error and ensure we map correctly to the
        # heat exception type
        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'list_stacks',
                  'args': {},
                  'version': self.api_version},
                 None).AndRaise(rpc_common.RemoteError("AttributeError"))

        self.m.ReplayAll()

        # Call the list controller function and compare the response
        result = self.controller.list(dummy_req)
        self.assertEqual(type(result),
                         exception.HeatInvalidParameterValueError)
        self.m.VerifyAll()

    def test_list_rmt_interr(self):
        params = {'Action': 'ListStacks'}
        dummy_req = self._dummy_GET_request(params)

        # Insert an engine RPC error and ensure we map correctly to the
        # heat exception type
        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'list_stacks',
                  'args': {},
                  'version': self.api_version},
                 None).AndRaise(rpc_common.RemoteError("Exception"))

        self.m.ReplayAll()

        # Call the list controller function and compare the response
        result = self.controller.list(dummy_req)
        self.assertEqual(type(result), exception.HeatInternalFailureError)
        self.m.VerifyAll()

    def test_describe(self):
        # Format a dummy GET request to pass into the WSGI handler
        stack_name = u"wordpress"
        identity = dict(identifier.HeatIdentifier('t', stack_name, '6'))
        params = {'Action': 'DescribeStacks', 'StackName': stack_name}
        dummy_req = self._dummy_GET_request(params)

        # Stub out the RPC call to the engine with a pre-canned response
        # Note the engine returns a load of keys we don't actually use
        # so this is a subset of the real response format
        engine_resp = [{u'stack_identity':
                        {u'tenant': u't',
                         u'stack_name': u'wordpress',
                         u'stack_id': u'6',
                         u'path': u''},
                        u'updated_time': u'2012-07-09T09:13:11Z',
                        u'parameters': {u'DBUsername': u'admin',
                                        u'LinuxDistribution': u'F17',
                                        u'InstanceType': u'm1.large',
                                        u'DBRootPassword': u'admin',
                                        u'DBPassword': u'admin',
                                        u'DBName': u'wordpress'},
                       u'outputs':
                       [{u'output_key': u'WebsiteURL',
                         u'description': u'URL for Wordpress wiki',
                         u'output_value': u'http://10.0.0.8/wordpress'}],
                       u'stack_status_reason': u'Stack successfully created',
                       u'creation_time': u'2012-07-09T09:12:45Z',
                       u'stack_name': u'wordpress',
                       u'notification_topics': [],
                       u'stack_status': u'CREATE_COMPLETE',
                       u'description': u'blah',
                       u'disable_rollback': 'true',
                       u'timeout_mins':60,
                       u'capabilities':[]}]

        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None).AndReturn(identity)
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'show_stack',
                  'args': {'stack_identity': identity},
                  'version': self.api_version}, None).AndReturn(engine_resp)

        self.m.ReplayAll()

        # Call the list controller function and compare the response
        response = self.controller.describe(dummy_req)

        expected = {'DescribeStacksResponse':
                    {'DescribeStacksResult':
                     {'Stacks':
                      [{'StackId': u'arn:openstack:heat::t:stacks/wordpress/6',
                        'StackStatusReason': u'Stack successfully created',
                        'Description': u'blah',
                        'Parameters':
                        [{'ParameterValue': u'admin',
                          'ParameterKey': u'DBUsername'},
                         {'ParameterValue': u'F17',
                          'ParameterKey': u'LinuxDistribution'},
                         {'ParameterValue': u'm1.large',
                          'ParameterKey': u'InstanceType'},
                         {'ParameterValue': u'admin',
                          'ParameterKey': u'DBRootPassword'},
                         {'ParameterValue': u'admin',
                          'ParameterKey': u'DBPassword'},
                         {'ParameterValue': u'wordpress',
                          'ParameterKey': u'DBName'}],
                        'Outputs':
                        [{'OutputKey': u'WebsiteURL',
                          'OutputValue': u'http://10.0.0.8/wordpress',
                          'Description': u'URL for Wordpress wiki'}],
                        'TimeoutInMinutes': 60,
                        'CreationTime': u'2012-07-09T09:12:45Z',
                        'Capabilities': [],
                        'StackName': u'wordpress',
                        'NotificationARNs': [],
                        'StackStatus': u'CREATE_COMPLETE',
                        'DisableRollback': 'true',
                        'LastUpdatedTime': u'2012-07-09T09:13:11Z'}]}}}

        self.assertEqual(response, expected)
        self.m.VerifyAll()

    def test_describe_arn(self):
        # Format a dummy GET request to pass into the WSGI handler
        stack_name = u"wordpress"
        stack_identifier = identifier.HeatIdentifier('t', stack_name, '6')
        identity = dict(stack_identifier)
        params = {'Action': 'DescribeStacks',
                  'StackName': stack_identifier.arn()}
        dummy_req = self._dummy_GET_request(params)

        # Stub out the RPC call to the engine with a pre-canned response
        # Note the engine returns a load of keys we don't actually use
        # so this is a subset of the real response format
        engine_resp = [{u'stack_identity': {u'tenant': u't',
                                            u'stack_name': u'wordpress',
                                            u'stack_id': u'6',
                                            u'path': u''},
                        u'updated_time': u'2012-07-09T09:13:11Z',
                        u'parameters': {u'DBUsername': u'admin',
                                        u'LinuxDistribution': u'F17',
                                        u'InstanceType': u'm1.large',
                                        u'DBRootPassword': u'admin',
                                        u'DBPassword': u'admin',
                                        u'DBName': u'wordpress'},
                        u'outputs':
                        [{u'output_key': u'WebsiteURL',
                          u'description': u'URL for Wordpress wiki',
                          u'output_value': u'http://10.0.0.8/wordpress'}],
                        u'stack_status_reason': u'Stack successfully created',
                        u'creation_time': u'2012-07-09T09:12:45Z',
                        u'stack_name': u'wordpress',
                        u'notification_topics': [],
                        u'stack_status': u'CREATE_COMPLETE',
                        u'description': u'blah',
                        u'disable_rollback': 'true',
                        u'timeout_mins':60,
                        u'capabilities':[]}]

        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'show_stack',
                  'args': {'stack_identity': identity},
                  'version': self.api_version}, None).AndReturn(engine_resp)

        self.m.ReplayAll()

        # Call the list controller function and compare the response
        response = self.controller.describe(dummy_req)

        expected = {'DescribeStacksResponse':
                    {'DescribeStacksResult':
                     {'Stacks':
                      [{'StackId': u'arn:openstack:heat::t:stacks/wordpress/6',
                        'StackStatusReason': u'Stack successfully created',
                        'Description': u'blah',
                        'Parameters':
                        [{'ParameterValue': u'admin',
                          'ParameterKey': u'DBUsername'},
                         {'ParameterValue': u'F17',
                          'ParameterKey': u'LinuxDistribution'},
                         {'ParameterValue': u'm1.large',
                          'ParameterKey': u'InstanceType'},
                         {'ParameterValue': u'admin',
                          'ParameterKey': u'DBRootPassword'},
                         {'ParameterValue': u'admin',
                          'ParameterKey': u'DBPassword'},
                         {'ParameterValue': u'wordpress',
                          'ParameterKey': u'DBName'}],
                        'Outputs':
                        [{'OutputKey': u'WebsiteURL',
                          'OutputValue': u'http://10.0.0.8/wordpress',
                          'Description': u'URL for Wordpress wiki'}],
                        'TimeoutInMinutes': 60,
                        'CreationTime': u'2012-07-09T09:12:45Z',
                        'Capabilities': [],
                        'StackName': u'wordpress',
                        'NotificationARNs': [],
                        'StackStatus': u'CREATE_COMPLETE',
                        'DisableRollback': 'true',
                        'LastUpdatedTime': u'2012-07-09T09:13:11Z'}]}}}

        self.assertEqual(response, expected)
        self.m.VerifyAll()

    def test_describe_arn_invalidtenant(self):
        # Format a dummy GET request to pass into the WSGI handler
        stack_name = u"wordpress"
        stack_identifier = identifier.HeatIdentifier('wibble', stack_name, '6')
        identity = dict(stack_identifier)
        params = {'Action': 'DescribeStacks',
                  'StackName': stack_identifier.arn()}
        dummy_req = self._dummy_GET_request(params)

        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'show_stack',
                  'args': {'stack_identity': identity},
                  'version': self.api_version},
                 None).AndRaise(rpc_common.RemoteError("InvalidTenant"))

        self.m.ReplayAll()

        result = self.controller.describe(dummy_req)
        self.assertEqual(type(result),
                         exception.HeatInvalidParameterValueError)
        self.m.VerifyAll()

    def test_describe_aterr(self):
        stack_name = "wordpress"
        identity = dict(identifier.HeatIdentifier('t', stack_name, '6'))
        params = {'Action': 'DescribeStacks', 'StackName': stack_name}
        dummy_req = self._dummy_GET_request(params)

        # Insert an engine RPC error and ensure we map correctly to the
        # heat exception type
        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None).AndReturn(identity)
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'show_stack',
                  'args': {'stack_identity': identity},
                  'version': self.api_version}, None
                 ).AndRaise(rpc_common.RemoteError("AttributeError"))

        self.m.ReplayAll()

        result = self.controller.describe(dummy_req)
        self.assertEqual(type(result),
                         exception.HeatInvalidParameterValueError)
        self.m.VerifyAll()

    def test_describe_bad_name(self):
        stack_name = "wibble"
        params = {'Action': 'DescribeStacks', 'StackName': stack_name}
        dummy_req = self._dummy_GET_request(params)

        # Insert an engine RPC error and ensure we map correctly to the
        # heat exception type
        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None
                 ).AndRaise(rpc_common.RemoteError("StackNotFound"))

        self.m.ReplayAll()

        result = self.controller.describe(dummy_req)
        self.assertEqual(type(result),
                         exception.HeatInvalidParameterValueError)
        self.m.VerifyAll()

    def test_get_template_int_body(self):
        '''Test the internal _get_template function.'''
        params = {'TemplateBody': "abcdef"}
        dummy_req = self._dummy_GET_request(params)
        result = self.controller._get_template(dummy_req)
        expected = "abcdef"
        self.assertEqual(result, expected)

    # TODO(shardy) : test the _get_template TemplateUrl case

    def test_create(self):
        # Format a dummy request
        stack_name = "wordpress"
        template = {u'Foo': u'bar'}
        json_template = json.dumps(template)
        params = {'Action': 'CreateStack', 'StackName': stack_name,
                  'TemplateBody': '%s' % json_template,
                  'TimeoutInMinutes': 30,
                  'DisableRollback': 'true',
                  'Parameters.member.1.ParameterKey': 'InstanceType',
                  'Parameters.member.1.ParameterValue': 'm1.xlarge'}
        engine_parms = {u'InstanceType': u'm1.xlarge'}
        engine_args = {'timeout_mins': u'30', 'disable_rollback': 'true'}
        dummy_req = self._dummy_GET_request(params)

        # Stub out the RPC call to the engine with a pre-canned response
        engine_resp = {u'tenant': u't',
                       u'stack_name': u'wordpress',
                       u'stack_id': u'1',
                       u'path': u''}

        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'create_stack',
                  'args': {'stack_name': stack_name,
                           'template': template,
                           'params': engine_parms,
                           'files': {},
                           'args': engine_args},
                  'version': self.api_version}, None).AndReturn(engine_resp)

        self.m.ReplayAll()

        response = self.controller.create(dummy_req)

        expected = {
            'CreateStackResponse': {
                'CreateStackResult': {
                    u'StackId': u'arn:openstack:heat::t:stacks/wordpress/1'
                }
            }
        }

        self.assertEqual(response, expected)
        self.m.VerifyAll()

    def test_create_err_no_template(self):
        # Format a dummy request with a missing template field
        stack_name = "wordpress"
        params = {'Action': 'CreateStack', 'StackName': stack_name}
        dummy_req = self._dummy_GET_request(params)

        result = self.controller.create(dummy_req)
        self.assertEqual(type(result), exception.HeatMissingParameterError)

    def test_create_err_inval_template(self):
        # Format a dummy request with an invalid TemplateBody
        stack_name = "wordpress"
        json_template = "!$%**_+}@~?"
        params = {'Action': 'CreateStack', 'StackName': stack_name,
                  'TemplateBody': '%s' % json_template}
        dummy_req = self._dummy_GET_request(params)

        result = self.controller.create(dummy_req)
        self.assertEqual(type(result),
                         exception.HeatInvalidParameterValueError)

    def test_create_err_rpcerr(self):
        # Format a dummy request
        stack_name = "wordpress"
        template = {u'Foo': u'bar'}
        json_template = json.dumps(template)
        params = {'Action': 'CreateStack', 'StackName': stack_name,
                  'TemplateBody': '%s' % json_template,
                  'TimeoutInMinutes': 30,
                  'Parameters.member.1.ParameterKey': 'InstanceType',
                  'Parameters.member.1.ParameterValue': 'm1.xlarge'}
        engine_parms = {u'InstanceType': u'm1.xlarge'}
        engine_args = {'timeout_mins': u'30'}
        dummy_req = self._dummy_GET_request(params)

        # Insert an engine RPC error and ensure we map correctly to the
        # heat exception type
        self.m.StubOutWithMock(rpc, 'call')

        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'create_stack',
                  'args': {'stack_name': stack_name,
                           'template': template,
                           'params': engine_parms,
                           'files': {},
                           'args': engine_args},
                  'version': self.api_version}, None
                 ).AndRaise(rpc_common.RemoteError("AttributeError"))
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'create_stack',
                  'args': {'stack_name': stack_name,
                           'template': template,
                           'params': engine_parms,
                           'files': {},
                           'args': engine_args},
                  'version': self.api_version}, None
                 ).AndRaise(rpc_common.RemoteError("UnknownUserParameter"))

        self.m.ReplayAll()

        result = self.controller.create(dummy_req)

        self.assertEqual(type(result),
                         exception.HeatInvalidParameterValueError)

        result = self.controller.create(dummy_req)

        self.assertEqual(type(result),
                         exception.HeatInvalidParameterValueError)

        self.m.VerifyAll()

    def test_create_err_exists(self):
        # Format a dummy request
        stack_name = "wordpress"
        template = {u'Foo': u'bar'}
        json_template = json.dumps(template)
        params = {'Action': 'CreateStack', 'StackName': stack_name,
                  'TemplateBody': '%s' % json_template,
                  'TimeoutInMinutes': 30,
                  'Parameters.member.1.ParameterKey': 'InstanceType',
                  'Parameters.member.1.ParameterValue': 'm1.xlarge'}
        engine_parms = {u'InstanceType': u'm1.xlarge'}
        engine_args = {'timeout_mins': u'30'}
        dummy_req = self._dummy_GET_request(params)

        # Insert an engine RPC error and ensure we map correctly to the
        # heat exception type
        self.m.StubOutWithMock(rpc, 'call')

        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'create_stack',
                  'args': {'stack_name': stack_name,
                           'template': template,
                           'params': engine_parms,
                           'files': {},
                           'args': engine_args},
                  'version': self.api_version}, None
                 ).AndRaise(rpc_common.RemoteError("StackExists"))

        self.m.ReplayAll()

        result = self.controller.create(dummy_req)

        self.assertEqual(type(result),
                         exception.AlreadyExistsError)
        self.m.VerifyAll()

    def test_create_err_engine(self):
        # Format a dummy request
        stack_name = "wordpress"
        template = {u'Foo': u'bar'}
        json_template = json.dumps(template)
        params = {'Action': 'CreateStack', 'StackName': stack_name,
                  'TemplateBody': '%s' % json_template,
                  'TimeoutInMinutes': 30,
                  'Parameters.member.1.ParameterKey': 'InstanceType',
                  'Parameters.member.1.ParameterValue': 'm1.xlarge'}
        engine_parms = {u'InstanceType': u'm1.xlarge'}
        engine_args = {'timeout_mins': u'30'}
        dummy_req = self._dummy_GET_request(params)

        # Stub out the RPC call to the engine with a pre-canned response
        self.m.StubOutWithMock(rpc, 'call')

        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'create_stack',
                  'args': {'stack_name': stack_name,
                  'template': template,
                  'params': engine_parms,
                  'files': {},
                  'args': engine_args},
                  'version': self.api_version}, None).AndRaise(
                      rpc_common.RemoteError(
                          'StackValidationFailed',
                          'Something went wrong'))

        self.m.ReplayAll()

        result = self.controller.create(dummy_req)

        self.assertEqual(type(result),
                         exception.HeatInvalidParameterValueError)
        self.m.VerifyAll()

    def test_update(self):
        # Format a dummy request
        stack_name = "wordpress"
        template = {u'Foo': u'bar'}
        json_template = json.dumps(template)
        params = {'Action': 'UpdateStack', 'StackName': stack_name,
                  'TemplateBody': '%s' % json_template,
                  'Parameters.member.1.ParameterKey': 'InstanceType',
                  'Parameters.member.1.ParameterValue': 'm1.xlarge'}
        engine_parms = {u'InstanceType': u'm1.xlarge'}
        engine_args = {}
        dummy_req = self._dummy_GET_request(params)

        # Stub out the RPC call to the engine with a pre-canned response
        identity = dict(identifier.HeatIdentifier('t', stack_name, '1'))

        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None).AndReturn(identity)

        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'update_stack',
                  'args': {'stack_identity': identity,
                           'template': template,
                           'params': engine_parms,
                           'files': {},
                           'args': engine_args},
                  'version': self.api_version},
                 None).AndReturn(identity)

        self.m.ReplayAll()

        response = self.controller.update(dummy_req)

        expected = {
            'UpdateStackResponse': {
                'UpdateStackResult': {
                    u'StackId': u'arn:openstack:heat::t:stacks/wordpress/1'
                }
            }
        }

        self.assertEqual(response, expected)
        self.m.VerifyAll()

    def test_update_bad_name(self):
        stack_name = "wibble"
        template = {u'Foo': u'bar'}
        json_template = json.dumps(template)
        params = {'Action': 'UpdateStack', 'StackName': stack_name,
                  'TemplateBody': '%s' % json_template,
                  'Parameters.member.1.ParameterKey': 'InstanceType',
                  'Parameters.member.1.ParameterValue': 'm1.xlarge'}
        dummy_req = self._dummy_GET_request(params)

        # Insert an engine RPC error and ensure we map correctly to the
        # heat exception type
        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None
                 ).AndRaise(rpc_common.RemoteError("StackNotFound"))

        self.m.ReplayAll()

        result = self.controller.update(dummy_req)
        self.assertEqual(type(result),
                         exception.HeatInvalidParameterValueError)
        self.m.VerifyAll()

    def test_create_or_update_err(self):
        result = self.controller.create_or_update(req={}, action="dsdgfdf")
        self.assertEqual(type(result), exception.HeatInternalFailureError)

    def test_get_template(self):
        # Format a dummy request
        stack_name = "wordpress"
        identity = dict(identifier.HeatIdentifier('t', stack_name, '6'))
        template = {u'Foo': u'bar'}
        params = {'Action': 'GetTemplate', 'StackName': stack_name}
        dummy_req = self._dummy_GET_request(params)

        # Stub out the RPC call to the engine with a pre-canned response
        engine_resp = template

        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None).AndReturn(identity)
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'get_template',
                  'args': {'stack_identity': identity},
                  'version': self.api_version}, None).AndReturn(engine_resp)

        self.m.ReplayAll()

        response = self.controller.get_template(dummy_req)

        expected = {'GetTemplateResponse':
                    {'GetTemplateResult':
                     {'TemplateBody': template}}}

        self.assertEqual(response, expected)
        self.m.VerifyAll()

    def test_get_template_err_rpcerr(self):
        stack_name = "wordpress"
        identity = dict(identifier.HeatIdentifier('t', stack_name, '6'))
        template = {u'Foo': u'bar'}
        params = {'Action': 'GetTemplate', 'StackName': stack_name}
        dummy_req = self._dummy_GET_request(params)

        # Insert an engine RPC error and ensure we map correctly to the
        # heat exception type
        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None).AndReturn(identity)
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'get_template',
                  'args': {'stack_identity': identity},
                  'version': self.api_version}, None
                 ).AndRaise(rpc_common.RemoteError("AttributeError"))

        self.m.ReplayAll()

        result = self.controller.get_template(dummy_req)

        self.assertEqual(type(result),
                         exception.HeatInvalidParameterValueError)
        self.m.VerifyAll()

    def test_get_template_bad_name(self):
        stack_name = "wibble"
        params = {'Action': 'GetTemplate', 'StackName': stack_name}
        dummy_req = self._dummy_GET_request(params)

        # Insert an engine RPC error and ensure we map correctly to the
        # heat exception type
        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None
                 ).AndRaise(rpc_common.RemoteError("StackNotFound"))

        self.m.ReplayAll()

        result = self.controller.get_template(dummy_req)
        self.assertEqual(type(result),
                         exception.HeatInvalidParameterValueError)
        self.m.VerifyAll()

    def test_get_template_err_none(self):
        stack_name = "wordpress"
        identity = dict(identifier.HeatIdentifier('t', stack_name, '6'))
        template = {u'Foo': u'bar'}
        params = {'Action': 'GetTemplate', 'StackName': stack_name}
        dummy_req = self._dummy_GET_request(params)

        # Stub out the RPC call to the engine to return None
        # this test the "no such stack" error path
        engine_resp = None

        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None).AndReturn(identity)
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'get_template',
                  'args': {'stack_identity': identity},
                  'version': self.api_version}, None).AndReturn(engine_resp)

        self.m.ReplayAll()

        result = self.controller.get_template(dummy_req)

        self.assertEqual(type(result),
                         exception.HeatInvalidParameterValueError)
        self.m.VerifyAll()

    def test_validate_err_no_template(self):
        # Format a dummy request with a missing template field
        stack_name = "wordpress"
        params = {'Action': 'ValidateTemplate'}
        dummy_req = self._dummy_GET_request(params)

        result = self.controller.validate_template(dummy_req)
        self.assertEqual(type(result), exception.HeatMissingParameterError)

    def test_validate_err_inval_template(self):
        # Format a dummy request with an invalid TemplateBody
        json_template = "!$%**_+}@~?"
        params = {'Action': 'ValidateTemplate',
                  'TemplateBody': '%s' % json_template}
        dummy_req = self._dummy_GET_request(params)

        result = self.controller.validate_template(dummy_req)
        self.assertEqual(type(result),
                         exception.HeatInvalidParameterValueError)

    def test_delete(self):
        # Format a dummy request
        stack_name = "wordpress"
        identity = dict(identifier.HeatIdentifier('t', stack_name, '1'))
        params = {'Action': 'DeleteStack', 'StackName': stack_name}
        dummy_req = self._dummy_GET_request(params)

        # Stub out the RPC call to the engine with a pre-canned response
        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None).AndReturn(identity)
        # Engine returns None when delete successful
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'delete_stack',
                  'args': {'stack_identity': identity},
                  'version': self.api_version}, None).AndReturn(None)

        self.m.ReplayAll()

        response = self.controller.delete(dummy_req)

        expected = {'DeleteStackResponse': {'DeleteStackResult': ''}}

        self.assertEqual(response, expected)
        self.m.VerifyAll()

    def test_delete_err_rpcerr(self):
        stack_name = "wordpress"
        identity = dict(identifier.HeatIdentifier('t', stack_name, '1'))
        params = {'Action': 'DeleteStack', 'StackName': stack_name}
        dummy_req = self._dummy_GET_request(params)

        # Stub out the RPC call to the engine with a pre-canned response
        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None).AndReturn(identity)

        # Insert an engine RPC error and ensure we map correctly to the
        # heat exception type
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'delete_stack',
                  'args': {'stack_identity': identity},
                  'version': self.api_version}, None
                 ).AndRaise(rpc_common.RemoteError("AttributeError"))

        self.m.ReplayAll()

        result = self.controller.delete(dummy_req)

        self.assertEqual(type(result),
                         exception.HeatInvalidParameterValueError)
        self.m.VerifyAll()

    def test_delete_bad_name(self):
        stack_name = "wibble"
        params = {'Action': 'DeleteStack', 'StackName': stack_name}
        dummy_req = self._dummy_GET_request(params)

        # Insert an engine RPC error and ensure we map correctly to the
        # heat exception type
        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None
                 ).AndRaise(rpc_common.RemoteError("StackNotFound"))

        self.m.ReplayAll()

        result = self.controller.delete(dummy_req)
        self.assertEqual(type(result),
                         exception.HeatInvalidParameterValueError)
        self.m.VerifyAll()

    def test_events_list(self):
        # Format a dummy request
        stack_name = "wordpress"
        identity = dict(identifier.HeatIdentifier('t', stack_name, '6'))
        params = {'Action': 'DescribeStackEvents', 'StackName': stack_name}
        dummy_req = self._dummy_GET_request(params)

        # Stub out the RPC call to the engine with a pre-canned response
        engine_resp = [{u'stack_name': u'wordpress',
                        u'event_time': u'2012-07-23T13:05:39Z',
                        u'stack_identity': {u'tenant': u't',
                                            u'stack_name': u'wordpress',
                                            u'stack_id': u'6',
                                            u'path': u''},
                        u'logical_resource_id': u'WikiDatabase',
                        u'resource_status_reason': u'state changed',
                        u'event_identity':
                        {u'tenant': u't',
                         u'stack_name': u'wordpress',
                         u'stack_id': u'6',
                         u'path': u'/resources/WikiDatabase/events/42'},
                        u'resource_action': u'TEST',
                        u'resource_status': u'IN_PROGRESS',
                        u'physical_resource_id': None,
                        u'resource_properties': {u'UserData': u'blah'},
                        u'resource_type': u'AWS::EC2::Instance'}]

        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None).AndReturn(identity)
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'list_events',
                  'args': {'stack_identity': identity},
                  'version': self.api_version}, None).AndReturn(engine_resp)

        self.m.ReplayAll()

        response = self.controller.events_list(dummy_req)

        expected = {'DescribeStackEventsResponse':
                    {'DescribeStackEventsResult':
                     {'StackEvents':
                      [{'EventId': u'42',
                        'StackId': u'arn:openstack:heat::t:stacks/wordpress/6',
                        'ResourceStatus': u'TEST_IN_PROGRESS',
                        'ResourceType': u'AWS::EC2::Instance',
                        'Timestamp': u'2012-07-23T13:05:39Z',
                        'StackName': u'wordpress',
                        'ResourceProperties':
                        json.dumps({u'UserData': u'blah'}),
                        'PhysicalResourceId': None,
                        'ResourceStatusReason': u'state changed',
                        'LogicalResourceId': u'WikiDatabase'}]}}}

        self.assertEqual(response, expected)
        self.m.VerifyAll()

    def test_events_list_err_rpcerr(self):
        stack_name = "wordpress"
        identity = dict(identifier.HeatIdentifier('t', stack_name, '6'))
        params = {'Action': 'DescribeStackEvents', 'StackName': stack_name}
        dummy_req = self._dummy_GET_request(params)

        # Insert an engine RPC error and ensure we map correctly to the
        # heat exception type
        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None).AndReturn(identity)
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'list_events',
                  'args': {'stack_identity': identity},
                  'version': self.api_version}, None
                 ).AndRaise(rpc_common.RemoteError("Exception"))

        self.m.ReplayAll()

        result = self.controller.events_list(dummy_req)

        self.assertEqual(type(result), exception.HeatInternalFailureError)
        self.m.VerifyAll()

    def test_events_list_bad_name(self):
        stack_name = "wibble"
        params = {'Action': 'DescribeStackEvents', 'StackName': stack_name}
        dummy_req = self._dummy_GET_request(params)

        # Insert an engine RPC error and ensure we map correctly to the
        # heat exception type
        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None
                 ).AndRaise(rpc_common.RemoteError("StackNotFound"))

        self.m.ReplayAll()

        result = self.controller.events_list(dummy_req)
        self.assertEqual(type(result),
                         exception.HeatInvalidParameterValueError)
        self.m.VerifyAll()

    def test_describe_stack_resource(self):
        # Format a dummy request
        stack_name = "wordpress"
        identity = dict(identifier.HeatIdentifier('t', stack_name, '6'))
        params = {'Action': 'DescribeStackResource',
                  'StackName': stack_name,
                  'LogicalResourceId': "WikiDatabase"}
        dummy_req = self._dummy_GET_request(params)

        # Stub out the RPC call to the engine with a pre-canned response
        engine_resp = {u'description': u'',
                       u'resource_identity': {
                           u'tenant': u't',
                           u'stack_name': u'wordpress',
                           u'stack_id': u'6',
                           u'path': u'resources/WikiDatabase'
                       },
                       u'stack_name': u'wordpress',
                       u'logical_resource_id': u'WikiDatabase',
                       u'resource_status_reason': None,
                       u'updated_time': u'2012-07-23T13:06:00Z',
                       u'stack_identity': {u'tenant': u't',
                                           u'stack_name': u'wordpress',
                                           u'stack_id': u'6',
                                           u'path': u''},
                       u'resource_action': u'CREATE',
                       u'resource_status': u'COMPLETE',
                       u'physical_resource_id':
                       u'a3455d8c-9f88-404d-a85b-5315293e67de',
                       u'resource_type': u'AWS::EC2::Instance',
                       u'metadata': {u'wordpress': []}}

        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None).AndReturn(identity)
        args = {
            'stack_identity': identity,
            'resource_name': dummy_req.params.get('LogicalResourceId'),
        }
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'describe_stack_resource',
                  'args': args,
                  'version': self.api_version}, None).AndReturn(engine_resp)

        self.m.ReplayAll()

        response = self.controller.describe_stack_resource(dummy_req)

        expected = {'DescribeStackResourceResponse':
                    {'DescribeStackResourceResult':
                    {'StackResourceDetail':
                    {'StackId': u'arn:openstack:heat::t:stacks/wordpress/6',
                    'ResourceStatus': u'CREATE_COMPLETE',
                    'Description': u'',
                    'ResourceType': u'AWS::EC2::Instance',
                    'ResourceStatusReason': None,
                    'LastUpdatedTimestamp': u'2012-07-23T13:06:00Z',
                    'StackName': u'wordpress',
                    'PhysicalResourceId':
                    u'a3455d8c-9f88-404d-a85b-5315293e67de',
                    'Metadata': {u'wordpress': []},
                    'LogicalResourceId': u'WikiDatabase'}}}}

        self.assertEqual(response, expected)
        self.m.VerifyAll()

    def test_describe_stack_resource_nonexistent_stack(self):
        # Format a dummy request
        stack_name = "wibble"
        identity = dict(identifier.HeatIdentifier('t', stack_name, '6'))
        params = {'Action': 'DescribeStackResource',
                  'StackName': stack_name,
                  'LogicalResourceId': "WikiDatabase"}
        dummy_req = self._dummy_GET_request(params)

        # Stub out the RPC call to the engine with a pre-canned response
        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version},
                 None).AndRaise(rpc_common.RemoteError("StackNotFound"))

        self.m.ReplayAll()

        result = self.controller.describe_stack_resource(dummy_req)
        self.assertEqual(type(result),
                         exception.HeatInvalidParameterValueError)
        self.m.VerifyAll()

    def test_describe_stack_resource_nonexistent(self):
        # Format a dummy request
        stack_name = "wordpress"
        identity = dict(identifier.HeatIdentifier('t', stack_name, '6'))
        params = {'Action': 'DescribeStackResource',
                  'StackName': stack_name,
                  'LogicalResourceId': "wibble"}
        dummy_req = self._dummy_GET_request(params)

        # Stub out the RPC call to the engine with a pre-canned response
        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None).AndReturn(identity)
        args = {
            'stack_identity': identity,
            'resource_name': dummy_req.params.get('LogicalResourceId'),
        }
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'describe_stack_resource',
                  'args': args,
                  'version': self.api_version},
                 None).AndRaise(rpc_common.RemoteError("ResourceNotFound"))

        self.m.ReplayAll()

        result = self.controller.describe_stack_resource(dummy_req)
        self.assertEqual(type(result),
                         exception.HeatInvalidParameterValueError)
        self.m.VerifyAll()

    def test_describe_stack_resources(self):
        # Format a dummy request
        stack_name = "wordpress"
        identity = dict(identifier.HeatIdentifier('t', stack_name, '6'))
        params = {'Action': 'DescribeStackResources',
                  'StackName': stack_name,
                  'LogicalResourceId': "WikiDatabase"}
        dummy_req = self._dummy_GET_request(params)

        # Stub out the RPC call to the engine with a pre-canned response
        engine_resp = [{u'description': u'',
                        u'resource_identity': {
                            u'tenant': u't',
                            u'stack_name': u'wordpress',
                            u'stack_id': u'6',
                            u'path': u'resources/WikiDatabase'
                        },
                        u'stack_name': u'wordpress',
                        u'logical_resource_id': u'WikiDatabase',
                        u'resource_status_reason': None,
                        u'updated_time': u'2012-07-23T13:06:00Z',
                        u'stack_identity': {u'tenant': u't',
                                            u'stack_name': u'wordpress',
                                            u'stack_id': u'6',
                                            u'path': u''},
                        u'resource_action': u'CREATE',
                        u'resource_status': u'COMPLETE',
                        u'physical_resource_id':
                        u'a3455d8c-9f88-404d-a85b-5315293e67de',
                        u'resource_type': u'AWS::EC2::Instance',
                        u'metadata': {u'ensureRunning': u'true''true'}}]

        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None).AndReturn(identity)
        args = {
            'stack_identity': identity,
            'resource_name': dummy_req.params.get('LogicalResourceId'),
        }
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'describe_stack_resources',
                  'args': args,
                  'version': self.api_version}, None).AndReturn(engine_resp)

        self.m.ReplayAll()

        response = self.controller.describe_stack_resources(dummy_req)

        expected = {'DescribeStackResourcesResponse':
                    {'DescribeStackResourcesResult':
                    {'StackResources':
                     [{'StackId': u'arn:openstack:heat::t:stacks/wordpress/6',
                       'ResourceStatus': u'CREATE_COMPLETE',
                       'Description': u'',
                       'ResourceType': u'AWS::EC2::Instance',
                       'Timestamp': u'2012-07-23T13:06:00Z',
                       'ResourceStatusReason': None,
                       'StackName': u'wordpress',
                       'PhysicalResourceId':
                       u'a3455d8c-9f88-404d-a85b-5315293e67de',
                       'LogicalResourceId': u'WikiDatabase'}]}}}

        self.assertEqual(response, expected)
        self.m.VerifyAll()

    def test_describe_stack_resources_bad_name(self):
        stack_name = "wibble"
        params = {'Action': 'DescribeStackResources',
                  'StackName': stack_name,
                  'LogicalResourceId': "WikiDatabase"}
        dummy_req = self._dummy_GET_request(params)

        # Insert an engine RPC error and ensure we map correctly to the
        # heat exception type
        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None
                 ).AndRaise(rpc_common.RemoteError("StackNotFound"))

        self.m.ReplayAll()

        result = self.controller.describe_stack_resources(dummy_req)
        self.assertEqual(type(result),
                         exception.HeatInvalidParameterValueError)
        self.m.VerifyAll()

    def test_describe_stack_resources_physical(self):
        # Format a dummy request
        stack_name = "wordpress"
        identity = dict(identifier.HeatIdentifier('t', stack_name, '6'))
        params = {'Action': 'DescribeStackResources',
                  'LogicalResourceId': "WikiDatabase",
                  'PhysicalResourceId': 'a3455d8c-9f88-404d-a85b-5315293e67de'}
        dummy_req = self._dummy_GET_request(params)

        # Stub out the RPC call to the engine with a pre-canned response
        engine_resp = [{u'description': u'',
                        u'resource_identity': {
                            u'tenant': u't',
                            u'stack_name': u'wordpress',
                            u'stack_id': u'6',
                            u'path': u'resources/WikiDatabase'
                        },
                        u'stack_name': u'wordpress',
                        u'logical_resource_id': u'WikiDatabase',
                        u'resource_status_reason': None,
                        u'updated_time': u'2012-07-23T13:06:00Z',
                        u'stack_identity': {u'tenant': u't',
                                            u'stack_name': u'wordpress',
                                            u'stack_id': u'6',
                                            u'path': u''},
                        u'resource_action': u'CREATE',
                        u'resource_status': u'COMPLETE',
                        u'physical_resource_id':
                        u'a3455d8c-9f88-404d-a85b-5315293e67de',
                        u'resource_type': u'AWS::EC2::Instance',
                        u'metadata': {u'ensureRunning': u'true''true'}}]

        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'find_physical_resource',
                  'args': {'physical_resource_id':
                           'a3455d8c-9f88-404d-a85b-5315293e67de'},
                  'version': self.api_version}, None).AndReturn(identity)
        args = {
            'stack_identity': identity,
            'resource_name': dummy_req.params.get('LogicalResourceId'),
        }
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'describe_stack_resources',
                  'args': args,
                  'version': self.api_version}, None).AndReturn(engine_resp)

        self.m.ReplayAll()

        response = self.controller.describe_stack_resources(dummy_req)

        expected = {'DescribeStackResourcesResponse':
                    {'DescribeStackResourcesResult':
                    {'StackResources':
                     [{'StackId': u'arn:openstack:heat::t:stacks/wordpress/6',
                       'ResourceStatus': u'CREATE_COMPLETE',
                       'Description': u'',
                       'ResourceType': u'AWS::EC2::Instance',
                       'Timestamp': u'2012-07-23T13:06:00Z',
                       'ResourceStatusReason': None,
                       'StackName': u'wordpress',
                       'PhysicalResourceId':
                       u'a3455d8c-9f88-404d-a85b-5315293e67de',
                       'LogicalResourceId': u'WikiDatabase'}]}}}

        self.assertEqual(response, expected)
        self.m.VerifyAll()

    def test_describe_stack_resources_physical_not_found(self):
        # Format a dummy request
        stack_name = "wordpress"
        identity = dict(identifier.HeatIdentifier('t', stack_name, '6'))
        params = {'Action': 'DescribeStackResources',
                  'LogicalResourceId': "WikiDatabase",
                  'PhysicalResourceId': 'aaaaaaaa-9f88-404d-cccc-ffffffffffff'}
        dummy_req = self._dummy_GET_request(params)

        # Stub out the RPC call to the engine with a pre-canned response
        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'find_physical_resource',
                  'args': {'physical_resource_id':
                           'aaaaaaaa-9f88-404d-cccc-ffffffffffff'},
                  'version': self.api_version},
                 None).AndRaise(
                     rpc_common.RemoteError("PhysicalResourceNotFound"))

        self.m.ReplayAll()

        response = self.controller.describe_stack_resources(dummy_req)

        self.assertEqual(type(response),
                         exception.HeatInvalidParameterValueError)
        self.m.VerifyAll()

    def test_describe_stack_resources_err_inval(self):
        # Format a dummy request containing both StackName and
        # PhysicalResourceId, which is invalid and should throw a
        # HeatInvalidParameterCombinationError
        stack_name = "wordpress"
        params = {'Action': 'DescribeStackResources',
                  'StackName': stack_name,
                  'PhysicalResourceId': "123456"}
        dummy_req = self._dummy_GET_request(params)
        ret = self.controller.describe_stack_resources(dummy_req)
        self.assertEqual(type(ret),
                         exception.HeatInvalidParameterCombinationError)
        self.m.VerifyAll()

    def test_list_stack_resources(self):
        # Format a dummy request
        stack_name = "wordpress"
        identity = dict(identifier.HeatIdentifier('t', stack_name, '6'))
        params = {'Action': 'ListStackResources',
                  'StackName': stack_name}
        dummy_req = self._dummy_GET_request(params)

        # Stub out the RPC call to the engine with a pre-canned response
        engine_resp = [{u'resource_identity':
                        {u'tenant': u't',
                         u'stack_name': u'wordpress',
                         u'stack_id': u'6',
                         u'path': u'/resources/WikiDatabase'},
                        u'stack_name': u'wordpress',
                        u'logical_resource_id': u'WikiDatabase',
                        u'resource_status_reason': None,
                        u'updated_time': u'2012-07-23T13:06:00Z',
                        u'stack_identity': {u'tenant': u't',
                                            u'stack_name': u'wordpress',
                                            u'stack_id': u'6',
                                            u'path': u''},
                        u'resource_action': u'CREATE',
                        u'resource_status': u'COMPLETE',
                        u'physical_resource_id':
                        u'a3455d8c-9f88-404d-a85b-5315293e67de',
                        u'resource_type': u'AWS::EC2::Instance'}]

        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None).AndReturn(identity)
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'list_stack_resources',
                 'args': {'stack_identity': identity},
                 'version': self.api_version}, None).AndReturn(engine_resp)

        self.m.ReplayAll()

        response = self.controller.list_stack_resources(dummy_req)

        expected = {'ListStackResourcesResponse': {'ListStackResourcesResult':
                    {'StackResourceSummaries':
                     [{'ResourceStatus': u'CREATE_COMPLETE',
                       'ResourceType': u'AWS::EC2::Instance',
                       'ResourceStatusReason': None,
                       'LastUpdatedTimestamp': u'2012-07-23T13:06:00Z',
                       'PhysicalResourceId':
                       u'a3455d8c-9f88-404d-a85b-5315293e67de',
                       'LogicalResourceId': u'WikiDatabase'}]}}}

        self.assertEqual(response, expected)
        self.m.VerifyAll()

    def test_list_stack_resources_bad_name(self):
        stack_name = "wibble"
        params = {'Action': 'ListStackResources',
                  'StackName': stack_name}
        dummy_req = self._dummy_GET_request(params)

        # Insert an engine RPC error and ensure we map correctly to the
        # heat exception type
        self.m.StubOutWithMock(rpc, 'call')
        rpc.call(dummy_req.context, self.topic,
                 {'namespace': None,
                  'method': 'identify_stack',
                  'args': {'stack_name': stack_name},
                  'version': self.api_version}, None
                 ).AndRaise(rpc_common.RemoteError("StackNotFound"))

        self.m.ReplayAll()

        result = self.controller.list_stack_resources(dummy_req)
        self.assertEqual(type(result),
                         exception.HeatInvalidParameterValueError)
        self.m.VerifyAll()

    def setUp(self):
        super(CfnStackControllerTest, self).setUp()

        opts = [
            cfg.StrOpt('config_dir', default=policy_path),
            cfg.StrOpt('config_file', default='foo'),
            cfg.StrOpt('project', default='heat'),
        ]
        cfg.CONF.register_opts(opts)
        cfg.CONF.set_default('host', 'host')
        self.topic = rpc_api.ENGINE_TOPIC
        self.api_version = '1.0'

        # Create WSGI controller instance
        class DummyConfig():
            bind_port = 8000
        cfgopts = DummyConfig()
        self.controller = stacks.StackController(options=cfgopts)
