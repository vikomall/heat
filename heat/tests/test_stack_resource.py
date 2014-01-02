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

import uuid
import mox

from heat.common import template_format
from heat.common import exception
from heat.engine import environment
from heat.engine import parser
from heat.engine import resource
from heat.engine import scheduler
from heat.engine import stack_resource
from heat.engine import template
from heat.tests.common import HeatTestCase
from heat.tests import generic_resource as generic_rsrc
from heat.tests import utils


ws_res_snippet = {"Type": "some_magic_type",
                  "metadata": {
                      "key": "value",
                      "some": "more stuff"}}

param_template = '''
{
  "Parameters" : {
    "KeyName" : {
      "Description" : "KeyName",
      "Type" : "String",
      "Default" : "test"
    }
  },
  "Resources" : {
    "WebServer": {
      "Type": "GenericResource",
      "Properties": {}
    }
  }
}
'''


simple_template = '''
{
  "Parameters" : {},
  "Resources" : {
    "WebServer": {
      "Type": "GenericResource",
      "Properties": {}
    }
  }
}
'''


class MyStackResource(stack_resource.StackResource,
                      generic_rsrc.GenericResource):
    def physical_resource_name(self):
        return "cb2f2b28-a663-4683-802c-4b40c916e1ff"

    def set_template(self, nested_tempalte, params):
        self.nested_tempalte = nested_tempalte
        self.nested_params = params

    def handle_create(self):
        return self.create_with_template(self.nested_tempalte,
                                         self.nested_params)

    def handle_delete(self):
        self.delete_nested()


class StackResourceTest(HeatTestCase):

    def setUp(self):
        super(StackResourceTest, self).setUp()
        utils.setup_dummy_db()
        resource._register_class('some_magic_type',
                                 MyStackResource)
        resource._register_class('GenericResource',
                                 generic_rsrc.GenericResource)
        t = parser.Template({template.RESOURCES:
                             {"provider_resource": ws_res_snippet}})
        self.parent_stack = parser.Stack(utils.dummy_context(), 'test_stack',
                                         t, stack_id=str(uuid.uuid4()))
        self.parent_resource = MyStackResource('test',
                                               ws_res_snippet,
                                               self.parent_stack)
        self.templ = template_format.parse(param_template)
        self.simple_template = template_format.parse(simple_template)

    @utils.stack_delete_after
    def test_create_with_template_ok(self):
        self.parent_resource.create_with_template(self.templ,
                                                  {"KeyName": "key"})
        self.stack = self.parent_resource.nested()

        self.assertEqual(self.parent_resource, self.stack.parent_resource)
        self.assertEqual("cb2f2b28-a663-4683-802c-4b40c916e1ff",
                         self.stack.name)
        self.assertEqual(self.templ, self.stack.t.t)
        self.assertEqual(self.stack.id, self.parent_resource.resource_id)

    @utils.stack_delete_after
    def test_set_deletion_policy(self):
        self.parent_resource.create_with_template(self.templ,
                                                  {"KeyName": "key"})
        self.stack = self.parent_resource.nested()
        self.parent_resource.set_deletion_policy(resource.RETAIN)
        for res in self.stack.resources.values():
            self.assertEqual(resource.RETAIN, res.t['DeletionPolicy'])

    @utils.stack_delete_after
    def test_get_abandon_data(self):
        self.parent_resource.create_with_template(self.templ,
                                                  {"KeyName": "key"})
        ret = self.parent_resource.get_abandon_data()
        # check abandoned data contains all the necessary information.
        # (no need to check stack/resource IDs, because they are
        # randomly generated uuids)
        self.assertEqual(6, len(ret))
        self.assertEqual('CREATE', ret['action'])
        self.assertIn('name', ret)
        self.assertIn('id', ret)
        self.assertIn('resources', ret)
        self.assertEqual(template_format.parse(param_template),
                         ret['template'])

    @utils.stack_delete_after
    def test_create_with_template_validates(self):
        """
        Creating a stack with a template validates the created stack, so that
        an invalid template will cause an error to be raised.
        """
        # Make a parameter key with the same name as the resource to cause a
        # simple validation error
        template = self.simple_template.copy()
        template['Parameters']['WebServer'] = {'Type': 'String'}
        self.assertRaises(
            exception.StackValidationFailed,
            self.parent_resource.create_with_template,
            template, {'WebServer': 'foo'})

    @utils.stack_delete_after
    def test_update_with_template_validates(self):
        """Updating a stack with a template validates the created stack."""
        create_result = self.parent_resource.create_with_template(
            self.simple_template, {})
        while not create_result.step():
            pass

        template = self.simple_template.copy()
        template['Parameters']['WebServer'] = {'Type': 'String'}
        self.assertRaises(
            exception.StackValidationFailed,
            self.parent_resource.update_with_template,
            template, {'WebServer': 'foo'})

    @utils.stack_delete_after
    def test_update_with_template_ok(self):
        """
        The update_with_template method updates the nested stack with the
        given template and user parameters.
        """
        create_result = self.parent_resource.create_with_template(
            self.simple_template, {})
        while not create_result.step():
            pass
        self.stack = self.parent_resource.nested()

        new_templ = self.simple_template.copy()
        inst_snippet = new_templ["Resources"]["WebServer"].copy()
        new_templ["Resources"]["WebServer2"] = inst_snippet
        updater = self.parent_resource.update_with_template(
            new_templ, {})
        updater.run_to_completion()
        self.assertEqual(True,
                         self.parent_resource.check_update_complete(updater))
        self.assertEqual(self.stack.state, ('UPDATE', 'COMPLETE'))
        self.assertEqual(set(self.stack.keys()),
                         set(["WebServer", "WebServer2"]))

        # The stack's owner_id is maintained.
        saved_stack = parser.Stack.load(
            self.parent_stack.context, self.stack.id)
        self.assertEqual(saved_stack.owner_id, self.parent_stack.id)

    @utils.stack_delete_after
    def test_update_with_template_state_err(self):
        """
        update_with_template_state_err method should raise error when update
        task is done but the nested stack is in (UPDATE, FAILED) state.
        """
        create_creator = self.parent_resource.create_with_template(
            self.simple_template, {})
        create_creator.run_to_completion()
        self.stack = self.parent_resource.nested()

        new_templ = self.simple_template.copy()
        inst_snippet = new_templ["Resources"]["WebServer"].copy()
        new_templ["Resources"]["WebServer2"] = inst_snippet

        def update_task():
            yield
            self.stack.state_set(parser.Stack.UPDATE, parser.Stack.FAILED, '')

        self.m.StubOutWithMock(self.stack, 'update_task')
        self.stack.update_task(mox.IgnoreArg()).AndReturn(update_task())
        self.m.ReplayAll()

        updater = self.parent_resource.update_with_template(new_templ, {})
        updater.run_to_completion()
        self.assertEqual((self.stack.UPDATE, self.stack.FAILED),
                         self.stack.state)
        ex = self.assertRaises(exception.Error,
                               self.parent_resource.check_update_complete,
                               updater)
        self.assertEqual('Nested stack update failed: ', str(ex))

        self.m.VerifyAll()

    @utils.stack_delete_after
    def test_load_nested_ok(self):
        self.parent_resource.create_with_template(self.templ,
                                                  {"KeyName": "key"})
        self.stack = self.parent_resource.nested()

        self.parent_resource._nested = None
        self.m.StubOutWithMock(parser.Stack, 'load')
        parser.Stack.load(self.parent_resource.context,
                          self.parent_resource.resource_id,
                          parent_resource=self.parent_resource).AndReturn('s')
        self.m.ReplayAll()

        self.parent_resource.nested()
        self.m.VerifyAll()

    @utils.stack_delete_after
    def test_load_nested_non_exist(self):
        self.parent_resource.create_with_template(self.templ,
                                                  {"KeyName": "key"})
        self.stack = self.parent_resource.nested()

        self.parent_resource._nested = None
        self.m.StubOutWithMock(parser.Stack, 'load')
        parser.Stack.load(self.parent_resource.context,
                          self.parent_resource.resource_id,
                          parent_resource=self.parent_resource)
        self.m.ReplayAll()

        self.assertRaises(exception.NotFound, self.parent_resource.nested)
        self.m.VerifyAll()

    def test_delete_nested_ok(self):
        nested = self.m.CreateMockAnything()
        self.m.StubOutWithMock(stack_resource.StackResource, 'nested')
        stack_resource.StackResource.nested().AndReturn(nested)
        nested.delete()
        self.m.ReplayAll()

        self.parent_resource.delete_nested()
        self.m.VerifyAll()

    def test_get_output_ok(self):
        nested = self.m.CreateMockAnything()
        self.m.StubOutWithMock(stack_resource.StackResource, 'nested')
        stack_resource.StackResource.nested().AndReturn(nested)
        nested.outputs = {"key": "value"}
        nested.output('key').AndReturn("value")
        self.m.ReplayAll()

        self.assertEqual("value", self.parent_resource.get_output("key"))

        self.m.VerifyAll()

    def test_get_output_key_not_found(self):
        nested = self.m.CreateMockAnything()
        self.m.StubOutWithMock(stack_resource.StackResource, 'nested')
        stack_resource.StackResource.nested().AndReturn(nested)
        nested.outputs = {}
        self.m.ReplayAll()

        self.assertRaises(exception.InvalidTemplateAttribute,
                          self.parent_resource.get_output,
                          "key")

        self.m.VerifyAll()

    @utils.stack_delete_after
    def test_create_complete_state_err(self):
        """
        check_create_complete should raise error when create task is
        done but the nested stack is not in (CREATE,COMPLETE) state
        """
        del self.templ['Resources']['WebServer']
        self.parent_resource.set_template(self.templ, {"KeyName": "test"})

        ctx = self.parent_resource.context
        phy_id = "cb2f2b28-a663-4683-802c-4b40c916e1ff"
        templ = parser.Template(self.templ)
        env = environment.Environment({"KeyName": "test"})
        self.stack = parser.Stack(ctx, phy_id, templ, env, timeout_mins=None,
                                  disable_rollback=True,
                                  parent_resource=self.parent_resource)

        self.m.StubOutWithMock(parser, 'Template')
        parser.Template(self.templ).AndReturn(templ)

        self.m.StubOutWithMock(environment, 'Environment')
        environment.Environment({"KeyName": "test"}).AndReturn(env)

        self.m.StubOutWithMock(parser, 'Stack')
        parser.Stack(ctx, phy_id, templ, env, timeout_mins=None,
                     disable_rollback=True,
                     parent_resource=self.parent_resource,
                     owner_id=self.parent_stack.id)\
            .AndReturn(self.stack)

        st_set = self.stack.state_set
        self.m.StubOutWithMock(self.stack, 'state_set')
        self.stack.state_set(self.stack.CREATE, self.stack.IN_PROGRESS,
                             "Stack CREATE started").WithSideEffects(st_set)

        self.stack.state_set(self.stack.CREATE, self.stack.COMPLETE,
                             "Stack create completed successfully")
        self.m.ReplayAll()

        self.assertRaises(exception.ResourceFailure,
                          scheduler.TaskRunner(self.parent_resource.create))
        self.assertEqual(('CREATE', 'FAILED'), self.parent_resource.state)
        self.assertEqual(('Error: Stack CREATE started'),
                         self.parent_resource.status_reason)

        self.m.VerifyAll()
        # Restore state_set to let clean up proceed
        self.stack.state_set = st_set

    @utils.stack_delete_after
    def test_suspend_complete_state_err(self):
        """
        check_suspend_complete should raise error when suspend task is
        done but the nested stack is not in (SUSPEND,COMPLETE) state
        """
        del self.templ['Resources']['WebServer']
        self.parent_resource.set_template(self.templ, {"KeyName": "test"})
        scheduler.TaskRunner(self.parent_resource.create)()
        self.stack = self.parent_resource.nested()

        st_set = self.stack.state_set
        self.m.StubOutWithMock(self.stack, 'state_set')
        self.stack.state_set(parser.Stack.SUSPEND, parser.Stack.IN_PROGRESS,
                             "Stack SUSPEND started").WithSideEffects(st_set)

        self.stack.state_set(parser.Stack.SUSPEND, parser.Stack.COMPLETE,
                             "Stack suspend completed successfully")
        self.m.ReplayAll()

        self.assertRaises(exception.ResourceFailure,
                          scheduler.TaskRunner(self.parent_resource.suspend))
        self.assertEqual(('SUSPEND', 'FAILED'), self.parent_resource.state)
        self.assertEqual(('Error: Stack SUSPEND started'),
                         self.parent_resource.status_reason)

        self.m.VerifyAll()
        # Restore state_set to let clean up proceed
        self.stack.state_set = st_set

    @utils.stack_delete_after
    def test_resume_complete_state_err(self):
        """
        check_resume_complete should raise error when resume task is
        done but the nested stack is not in (RESUME,COMPLETE) state
        """
        del self.templ['Resources']['WebServer']
        self.parent_resource.set_template(self.templ, {"KeyName": "test"})
        scheduler.TaskRunner(self.parent_resource.create)()
        self.stack = self.parent_resource.nested()

        scheduler.TaskRunner(self.parent_resource.suspend)()

        st_set = self.stack.state_set
        self.m.StubOutWithMock(self.stack, 'state_set')
        self.stack.state_set(parser.Stack.RESUME, parser.Stack.IN_PROGRESS,
                             "Stack RESUME started").WithSideEffects(st_set)

        self.stack.state_set(parser.Stack.RESUME, parser.Stack.COMPLETE,
                             "Stack resume completed successfully")
        self.m.ReplayAll()

        self.assertRaises(exception.ResourceFailure,
                          scheduler.TaskRunner(self.parent_resource.resume))
        self.assertEqual(('RESUME', 'FAILED'), self.parent_resource.state)
        self.assertEqual(('Error: Stack RESUME started'),
                         self.parent_resource.status_reason)

        self.m.VerifyAll()
        # Restore state_set to let clean up proceed
        self.stack.state_set = st_set
