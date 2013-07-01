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

import functools
import re

from heat.engine import environment
from heat.common import exception
from heat.engine import dependencies
from heat.common import identifier
from heat.engine import resource
from heat.engine import resources
from heat.engine import scheduler
from heat.engine import template
from heat.engine import timestamp
from heat.engine import update
from heat.engine.parameters import Parameters
from heat.engine.template import Template
from heat.engine.clients import Clients
from heat.db import api as db_api

from heat.openstack.common import log as logging
from heat.common.exception import ServerError
from heat.common.exception import StackValidationFailed

logger = logging.getLogger(__name__)

(PARAM_STACK_NAME, PARAM_REGION) = ('AWS::StackName', 'AWS::Region')


class Stack(object):

    ACTIONS = (CREATE, DELETE, UPDATE, ROLLBACK, SUSPEND
               ) = ('CREATE', 'DELETE', 'UPDATE', 'ROLLBACK', 'SUSPEND')

    STATUSES = (IN_PROGRESS, FAILED, COMPLETE
                ) = ('IN_PROGRESS', 'FAILED', 'COMPLETE')

    created_time = timestamp.Timestamp(db_api.stack_get, 'created_at')
    updated_time = timestamp.Timestamp(db_api.stack_get, 'updated_at')

    _zones = None

    def __init__(self, context, stack_name, tmpl, env=None,
                 stack_id=None, action=None, status=None,
                 status_reason='', timeout_mins=60, resolve_data=True,
                 disable_rollback=True):
        '''
        Initialise from a context, name, Template object and (optionally)
        Environment object. The database ID may also be initialised, if the
        stack is already in the database.
        '''

        if re.match("[a-zA-Z][a-zA-Z0-9_.-]*$", stack_name) is None:
            raise ValueError(_('Invalid stack name %s'
                               ' must contain only alphanumeric or '
                               '\"_-.\" characters, must start with alpha'
                               ) % stack_name)

        self.id = stack_id
        self.context = context
        self.clients = Clients(context)
        self.t = tmpl
        self.name = stack_name
        self.action = action
        self.status = status
        self.status_reason = status_reason
        self.timeout_mins = timeout_mins
        self.disable_rollback = disable_rollback

        resources.initialise()

        self.env = env or environment.Environment({})
        self.parameters = Parameters(self.name, self.t,
                                     user_params=self.env.params)

        self._set_param_stackid()

        if resolve_data:
            self.outputs = self.resolve_static_data(self.t[template.OUTPUTS])
        else:
            self.outputs = {}

        template_resources = self.t[template.RESOURCES]
        self.resources = dict((name,
                               resource.Resource(name, data, self))
                              for (name, data) in template_resources.items())

        self.dependencies = self._get_dependencies(self.resources.itervalues())

    def _set_param_stackid(self):
        '''
        Update self.parameters with the current ARN which is then provided
        via the Parameters class as the AWS::StackId pseudo parameter
        '''
        # This can fail if constructor called without a valid context,
        # as it is in many tests
        try:
            stack_arn = self.identifier().arn()
        except (AttributeError, ValueError, TypeError):
            logger.warning("Unable to set parameters StackId identifier")
        else:
            self.parameters.set_stack_id(stack_arn)

    @staticmethod
    def _get_dependencies(resources):
        '''Return the dependency graph for a list of resources.'''
        deps = dependencies.Dependencies()
        for resource in resources:
            resource.add_dependencies(deps)

        return deps

    @classmethod
    def load(cls, context, stack_id=None, stack=None, resolve_data=True):
        '''Retrieve a Stack from the database.'''
        if stack is None:
            stack = db_api.stack_get(context, stack_id)
        if stack is None:
            message = 'No stack exists with id "%s"' % str(stack_id)
            raise exception.NotFound(message)

        template = Template.load(context, stack.raw_template_id)
        env = environment.Environment(stack.parameters)
        stack = cls(context, stack.name, template, env,
                    stack.id, stack.action, stack.status, stack.status_reason,
                    stack.timeout, resolve_data, stack.disable_rollback)

        return stack

    def store(self, owner=None):
        '''
        Store the stack in the database and return its ID
        If self.id is set, we update the existing stack
        '''
        new_creds = db_api.user_creds_create(self.context)

        s = {
            'name': self.name,
            'raw_template_id': self.t.store(self.context),
            'parameters': self.env.user_env_as_dict(),
            'owner_id': owner and owner.id,
            'user_creds_id': new_creds.id,
            'username': self.context.username,
            'tenant': self.context.tenant_id,
            'action': self.action,
            'status': self.status,
            'status_reason': self.status_reason,
            'timeout': self.timeout_mins,
            'disable_rollback': self.disable_rollback,
        }
        if self.id:
            db_api.stack_update(self.context, self.id, s)
        else:
            new_s = db_api.stack_create(self.context, s)
            self.id = new_s.id

        self._set_param_stackid()

        return self.id

    def identifier(self):
        '''
        Return an identifier for this stack.
        '''
        return identifier.HeatIdentifier(self.context.tenant_id,
                                         self.name, self.id)

    def __iter__(self):
        '''
        Return an iterator over this template's resources in the order that
        they should be started.
        '''
        return iter(self.dependencies)

    def __reversed__(self):
        '''
        Return an iterator over this template's resources in the order that
        they should be stopped.
        '''
        return reversed(self.dependencies)

    def __len__(self):
        '''Return the number of resources.'''
        return len(self.resources)

    def __getitem__(self, key):
        '''Get the resource with the specified name.'''
        return self.resources[key]

    def __setitem__(self, key, value):
        '''Set the resource with the specified name to a specific value.'''
        self.resources[key] = value

    def __contains__(self, key):
        '''Determine whether the stack contains the specified resource.'''
        return key in self.resources

    def keys(self):
        '''Return a list of resource keys for the stack.'''
        return self.resources.keys()

    def __str__(self):
        '''Return a human-readable string representation of the stack.'''
        return 'Stack "%s"' % self.name

    def resource_by_refid(self, refid):
        '''
        Return the resource in this stack with the specified
        refid, or None if not found
        '''
        for r in self.resources.values():
            if r.state in (
                    (r.CREATE, r.IN_PROGRESS),
                    (r.CREATE, r.COMPLETE),
                    (r.UPDATE, r.IN_PROGRESS),
                    (r.UPDATE, r.COMPLETE)) and r.FnGetRefId() == refid:
                return r

    def validate(self):
        '''
        http://docs.amazonwebservices.com/AWSCloudFormation/latest/\
        APIReference/API_ValidateTemplate.html
        '''
        # TODO(sdake) Should return line number of invalid reference

        for res in self:
            try:
                result = res.validate()
            except ServerError as ex:
                logger.exception(ex)
                raise ex
            except Exception as ex:
                logger.exception(ex)
                raise StackValidationFailed(message=str(ex))
            if result:
                raise StackValidationFailed(message=result)

    def state_set(self, action, status, reason):
        '''Update the stack state in the database.'''
        if action not in self.ACTIONS:
            raise ValueError("Invalid action %s" % action)

        if status not in self.STATUSES:
            raise ValueError("Invalid status %s" % status)

        self.action = action
        self.status = status
        self.status_reason = reason

        if self.id is None:
            return

        stack = db_api.stack_get(self.context, self.id)
        stack.update_and_save({'action': action,
                               'status': status,
                               'status_reason': reason})

    @property
    def state(self):
        '''Returns state, tuple of action, status.'''
        return (self.action, self.status)

    def timeout_secs(self):
        '''
        Return the stack creation timeout in seconds, or None if no timeout
        should be used.
        '''
        if self.timeout_mins is None:
            return None

        return self.timeout_mins * 60

    def create(self):
        '''
        Create the stack and all of the resources.
        '''
        def rollback():
            if not self.disable_rollback and self.state == (self.CREATE,
                                                            self.FAILED):
                self.delete(action=self.ROLLBACK)

        creator = scheduler.TaskRunner(self.stack_task,
                                       action=self.CREATE,
                                       reverse=False,
                                       post_func=rollback)
        creator(timeout=self.timeout_secs())

    @scheduler.wrappertask
    def stack_task(self, action, reverse=False, post_func=None):
        '''
        A task to perform an action on the stack and all of the resources
        in forward or reverse dependency order as specfifed by reverse
        '''
        self.state_set(action, self.IN_PROGRESS,
                       'Stack %s started' % action)

        stack_status = self.COMPLETE
        reason = 'Stack %s completed successfully' % action.lower()
        res = None

        def resource_action(r):
            # Find e.g resource.create and call it
            action_l = action.lower()
            handle = getattr(r, '%s' % action_l, None)
            if callable(handle):
                return handle()
            else:
                raise exception.ResourceFailure(
                    AttributeError(_('Resource action %s not found') %
                                   action_l))

        action_task = scheduler.DependencyTaskGroup(self.dependencies,
                                                    resource_action,
                                                    reverse)

        try:
            yield action_task()
        except exception.ResourceFailure as ex:
            stack_status = self.FAILED
            reason = 'Resource %s failed: %s' % (action.lower(), str(ex))
        except scheduler.Timeout:
            stack_status = self.FAILED
            reason = '%s timed out' % action.title()

        self.state_set(action, stack_status, reason)

        if callable(post_func):
            post_func()

    def update(self, newstack, action=UPDATE):
        '''
        Compare the current stack with newstack,
        and where necessary create/update/delete the resources until
        this stack aligns with newstack.

        Note update of existing stack resources depends on update
        being implemented in the underlying resource types

        Update will fail if it exceeds the specified timeout. The default is
        60 minutes, set in the constructor
        '''
        if action not in (self.UPDATE, self.ROLLBACK):
            logger.error("Unexpected action %s passed to update!" % action)
            self.state_set(self.UPDATE, self.FAILED,
                           "Invalid action %s" % action)
            return

        if self.status != self.COMPLETE:
            if (action == self.ROLLBACK and
                    self.state == (self.UPDATE, self.IN_PROGRESS)):
                logger.debug("Starting update rollback for %s" % self.name)
            else:
                self.state_set(action, self.FAILED,
                               'State invalid for %s' % action)
                return

        self.state_set(self.UPDATE, self.IN_PROGRESS,
                       'Stack %s started' % action)

        # cache all the resources runtime data.
        for r in self:
            r.cache_template()

        try:
            update_task = update.StackUpdate(self, newstack)
            updater = scheduler.TaskRunner(update_task)
            try:
                updater(timeout=self.timeout_secs())
            finally:
                cur_deps = self._get_dependencies(self.resources.itervalues())
                self.dependencies = cur_deps

            if action == self.UPDATE:
                reason = 'Stack successfully updated'
            else:
                reason = 'Stack rollback completed'
            stack_status = self.COMPLETE

        except scheduler.Timeout:
            stack_status = self.FAILED
            reason = 'Timed out'
        except exception.ResourceFailure as e:
            reason = str(e)

            stack_status = self.FAILED
            if action == self.UPDATE:
                # If rollback is enabled, we do another update, with the
                # existing template, so we roll back to the original state
                if not self.disable_rollback:
                    oldstack = Stack(self.context, self.name, self.t,
                                     self.env)
                    self.update(oldstack, action=self.ROLLBACK)
                    return

        self.state_set(action, stack_status, reason)

        # flip the template & environment to the newstack values
        # Note we do this on success and failure, so the current
        # stack resources are stored, even if one is in a failed
        # state (otherwise we won't remove them on delete)
        self.t = newstack.t
        self.env = newstack.env
        template_outputs = self.t[template.OUTPUTS]
        self.outputs = self.resolve_static_data(template_outputs)
        self.store()

    def delete(self, action=DELETE):
        '''
        Delete all of the resources, and then the stack itself.
        The action parameter is used to differentiate between a user
        initiated delete and an automatic stack rollback after a failed
        create, which amount to the same thing, but the states are recorded
        differently.
        '''
        if action not in (self.DELETE, self.ROLLBACK):
            logger.error("Unexpected action %s passed to delete!" % action)
            self.state_set(self.DELETE, self.FAILED,
                           "Invalid action %s" % action)
            return

        self.state_set(action, self.IN_PROGRESS, 'Stack %s started' % action)

        failures = []
        for res in reversed(self):
            try:
                res.destroy()
            except exception.ResourceFailure as ex:
                logger.error('Failed to delete %s error: %s' % (str(res),
                                                                str(ex)))
                failures.append(str(res))

        if failures:
            self.state_set(action, self.FAILED,
                           'Failed to %s : %s' % (action, ', '.join(failures)))
        else:
            self.state_set(action, self.COMPLETE, '%s completed' % action)
            db_api.stack_delete(self.context, self.id)
            self.id = None

    def suspend(self):
        '''
        Suspend the stack, which invokes handle_suspend for all stack resources
        waits for all resources to become SUSPEND_COMPLETE then declares the
        stack SUSPEND_COMPLETE.
        Note the default implementation for all resources is to do nothing
        other than move to SUSPEND_COMPLETE, so the resources must implement
        handle_suspend for this to have any effect.
        '''
        sus_task = scheduler.TaskRunner(self.stack_task,
                                        action=self.SUSPEND,
                                        reverse=True)
        sus_task(timeout=self.timeout_secs())

    def output(self, key):
        '''
        Get the value of the specified stack output.
        '''
        value = self.outputs[key].get('Value', '')
        return self.resolve_runtime_data(value)

    def restart_resource(self, resource_name):
        '''
        stop resource_name and all that depend on it
        start resource_name and all that depend on it
        '''
        deps = self.dependencies[self[resource_name]]
        failed = False

        for res in reversed(deps):
            try:
                res.destroy()
            except exception.ResourceFailure as ex:
                failed = True
                logger.error('delete: %s' % str(ex))

        for res in deps:
            if not failed:
                try:
                    scheduler.TaskRunner(res.create)()
                except exception.ResourceFailure as ex:
                    logger.exception('create')
                    failed = True
            else:
                res.state_set(res.CREATE, res.FAILED,
                              'Resource restart aborted')
        # TODO(asalkeld) if any of this fails we Should
        # restart the whole stack

    def get_availability_zones(self):
        if self._zones is None:
            self._zones = [
                zone.zoneName for zone in
                self.clients.nova().availability_zones.list(detailed=False)]
        return self._zones

    def resolve_static_data(self, snippet):
        return resolve_static_data(self.t, self, self.parameters, snippet)

    def resolve_runtime_data(self, snippet):
        return resolve_runtime_data(self.t, self.resources, snippet)


def resolve_static_data(template, stack, parameters, snippet):
    '''
    Resolve static parameters, map lookups, etc. in a template.

    Example:

    >>> from heat.common import template_format
    >>> template_str = '# JSON or YAML encoded template'
    >>> template = Template(template_format.parse(template_str))
    >>> parameters = Parameters('stack', template, {'KeyName': 'my_key'})
    >>> resolve_static_data(template, None, parameters, {'Ref': 'KeyName'})
    'my_key'
    '''
    return transform(snippet,
                     [functools.partial(template.resolve_param_refs,
                                        parameters=parameters),
                      functools.partial(template.resolve_availability_zones,
                                        stack=stack),
                      template.resolve_find_in_map,
                      template.reduce_joins])


def resolve_runtime_data(template, resources, snippet):
    return transform(snippet,
                     [functools.partial(template.resolve_resource_refs,
                                        resources=resources),
                      functools.partial(template.resolve_attributes,
                                        resources=resources),
                      template.resolve_split,
                      template.resolve_select,
                      template.resolve_joins,
                      template.resolve_replace,
                      template.resolve_base64])


def transform(data, transformations):
    '''
    Apply each of the transformation functions in the supplied list to the data
    in turn.
    '''
    for t in transformations:
        data = t(data)
    return data
