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

import base64
from datetime import datetime

from heat.engine import event
from heat.common import exception
from heat.openstack.common import excutils
from heat.db import api as db_api
from heat.common import identifier
from heat.common import short_id
from heat.engine import timestamp
# import class to avoid name collisions and ugly aliasing
from heat.engine.attributes import Attributes
from heat.engine.properties import Properties

from heat.openstack.common import log as logging
from heat.openstack.common.gettextutils import _

logger = logging.getLogger(__name__)


_resource_classes = {}
_template_class = None


def get_types():
    '''Return an iterator over the list of valid resource types.'''
    return iter(_resource_classes)


def get_class(resource_type, resource_name=None, environment=None):
    '''Return the Resource class for a given resource type.'''
    if environment:
        resource_type = environment.get_resource_type(resource_type,
                                                      resource_name)

    if resource_type.endswith(('.yaml', '.template')):
        cls = _template_class
    else:
        cls = _resource_classes.get(resource_type)
    if cls is None:
        msg = "Unknown resource Type : %s" % resource_type
        raise exception.StackValidationFailed(message=msg)
    else:
        return cls


def _register_class(resource_type, resource_class):
    logger.info(_('Registering resource type %s') % resource_type)
    if resource_type in _resource_classes:
        logger.warning(_('Replacing existing resource type %s') %
                       resource_type)

    _resource_classes[resource_type] = resource_class


def register_template_class(cls):
    global _template_class
    if _template_class is None:
        _template_class = cls


class UpdateReplace(Exception):
    '''
    Raised when resource update requires replacement
    '''
    _message = _("The Resource %s requires replacement.")

    def __init__(self, resource_name='Unknown',
                 message=_("The Resource %s requires replacement.")):
        try:
            msg = message % resource_name
        except TypeError:
            msg = message
        super(Exception, self).__init__(msg)


class Metadata(object):
    '''
    A descriptor for accessing the metadata of a resource while ensuring the
    most up-to-date data is always obtained from the database.
    '''

    def __get__(self, resource, resource_class):
        '''Return the metadata for the owning resource.'''
        if resource is None:
            return None
        if resource.id is None:
            return resource.parsed_template('Metadata')
        rs = db_api.resource_get(resource.stack.context, resource.id)
        rs.refresh(attrs=['rsrc_metadata'])
        return rs.rsrc_metadata

    def __set__(self, resource, metadata):
        '''Update the metadata for the owning resource.'''
        if resource.id is None:
            raise exception.ResourceNotAvailable(resource_name=resource.name)
        rs = db_api.resource_get(resource.stack.context, resource.id)
        rs.update_and_save({'rsrc_metadata': metadata})


class Resource(object):
    ACTIONS = (CREATE, DELETE, UPDATE, ROLLBACK, SUSPEND, RESUME
               ) = ('CREATE', 'DELETE', 'UPDATE', 'ROLLBACK',
                    'SUSPEND', 'RESUME')

    STATUSES = (IN_PROGRESS, FAILED, COMPLETE
                ) = ('IN_PROGRESS', 'FAILED', 'COMPLETE')

    # If True, this resource must be created before it can be referenced.
    strict_dependency = True

    created_time = timestamp.Timestamp(db_api.resource_get, 'created_at')
    updated_time = timestamp.Timestamp(db_api.resource_get, 'updated_at')

    metadata = Metadata()

    # Resource implementation set this to the subset of template keys
    # which are supported for handle_update, used by update_template_diff
    update_allowed_keys = ()

    # Resource implementation set this to the subset of resource properties
    # supported for handle_update, used by update_template_diff_properties
    update_allowed_properties = ()

    # Resource implementations set this to the name: description dictionary
    # that describes the appropriate resource attributes
    attributes_schema = {}

    def __new__(cls, name, json, stack):
        '''Create a new Resource of the appropriate class for its type.'''

        if cls != Resource:
            # Call is already for a subclass, so pass it through
            return super(Resource, cls).__new__(cls)

        # Select the correct subclass to instantiate
        ResourceClass = get_class(json['Type'],
                                  resource_name=name,
                                  environment=stack.env)
        return ResourceClass(name, json, stack)

    def __init__(self, name, json_snippet, stack):
        if '/' in name:
            raise ValueError(_('Resource name may not contain "/"'))

        self.stack = stack
        self.context = stack.context
        self.name = name
        self.json_snippet = json_snippet
        self.t = stack.resolve_static_data(json_snippet)
        self.properties = Properties(self.properties_schema,
                                     self.t.get('Properties', {}),
                                     self.stack.resolve_runtime_data,
                                     self.name)
        self.attributes = Attributes(self.name,
                                     self.attributes_schema,
                                     self._resolve_attribute)

        resource = db_api.resource_get_by_name_and_stack(self.context,
                                                         name, stack.id)
        if resource:
            self.resource_id = resource.nova_instance
            self.action = resource.action
            self.status = resource.status
            self.status_reason = resource.status_reason
            self.id = resource.id
        else:
            self.resource_id = None
            self.action = None
            self.status = None
            self.status_reason = ''
            self.id = None

    def __eq__(self, other):
        '''Allow == comparison of two resources.'''
        # For the purposes of comparison, we declare two resource objects
        # equal if their names and parsed_templates are the same
        if isinstance(other, Resource):
            return (self.name == other.name) and (
                self.parsed_template() == other.parsed_template())
        return NotImplemented

    def __ne__(self, other):
        '''Allow != comparison of two resources.'''
        result = self.__eq__(other)
        if result is NotImplemented:
            return result
        return not result

    def type(self):
        return self.t['Type']

    def identifier(self):
        '''Return an identifier for this resource.'''
        return identifier.ResourceIdentifier(resource_name=self.name,
                                             **self.stack.identifier())

    def parsed_template(self, section=None, default={}):
        '''
        Return the parsed template data for the resource. May be limited to
        only one section of the data, in which case a default value may also
        be supplied.
        '''
        if section is None:
            template = self.t
        else:
            template = self.t.get(section, default)
        return self.stack.resolve_runtime_data(template)

    def update_template_diff(self, after, before):
        '''
        Returns the difference between the before and after json snippets. If
        something has been removed in after which exists in before we set it to
        None. If any keys have changed which are not in update_allowed_keys,
        raises UpdateReplace if the differing keys are not in
        update_allowed_keys
        '''
        update_allowed_set = set(self.update_allowed_keys)

        # Create a set containing the keys in both current and update template
        template_keys = set(before.keys())
        template_keys.update(set(after.keys()))

        # Create a set of keys which differ (or are missing/added)
        changed_keys_set = set([k for k in template_keys
                                if before.get(k) != after.get(k)])

        if not changed_keys_set.issubset(update_allowed_set):
            badkeys = changed_keys_set - update_allowed_set
            raise UpdateReplace(self.name)

        return dict((k, after.get(k)) for k in changed_keys_set)

    def update_template_diff_properties(self, after, before):
        '''
        Returns the changed Properties between the before and after json
        snippets. If a property has been removed in after which exists in
        before we set it to None. If any properties have changed which are not
        in update_allowed_properties, raises UpdateReplace if the modified
        properties are not in the update_allowed_properties
        '''
        update_allowed_set = set(self.update_allowed_properties)

        # Create a set containing the keys in both current and update template
        current_properties = before.get('Properties', {})

        template_properties = set(current_properties.keys())
        updated_properties = after.get('Properties', {})
        template_properties.update(set(updated_properties.keys()))

        # Create a set of keys which differ (or are missing/added)
        changed_properties_set = set(k for k in template_properties
                                     if current_properties.get(k) !=
                                     updated_properties.get(k))

        if not changed_properties_set.issubset(update_allowed_set):
            raise UpdateReplace(self.name)

        return dict((k, updated_properties.get(k))
                    for k in changed_properties_set)

    def __str__(self):
        return '%s "%s"' % (self.__class__.__name__, self.name)

    def _add_dependencies(self, deps, head, fragment):
        if isinstance(fragment, dict):
            for key, value in fragment.items():
                if key in ('DependsOn', 'Ref', 'Fn::GetAtt'):
                    if key == 'Fn::GetAtt':
                        value, head = value

                    try:
                        target = self.stack.resources[value]
                    except KeyError:
                        raise exception.InvalidTemplateReference(
                            resource=value,
                            key=head)
                    if key == 'DependsOn' or target.strict_dependency:
                        deps += (self, target)
                else:
                    self._add_dependencies(deps, key, value)
        elif isinstance(fragment, list):
            for item in fragment:
                self._add_dependencies(deps, head, item)

    def add_dependencies(self, deps):
        self._add_dependencies(deps, None, self.t)
        deps += (self, None)

    def required_by(self):
        '''
        Returns a list of names of resources which directly require this
        resource as a dependency.
        '''
        return list(
            [r.name for r in self.stack.dependencies.required_by(self)])

    def keystone(self):
        return self.stack.clients.keystone()

    def nova(self, service_type='compute'):
        return self.stack.clients.nova(service_type)

    def swift(self):
        return self.stack.clients.swift()

    def quantum(self):
        return self.stack.clients.quantum()

    def cinder(self):
        return self.stack.clients.cinder()

    def _do_action(self, action, pre_func=None):
        '''
        Perform a transition to a new state via a specified action
        action should be e.g self.CREATE, self.UPDATE etc, we set
        status based on this, the transistion is handled by calling the
        corresponding handle_* and check_*_complete functions
        Note pre_func is an optional function reference which will
        be called before the handle_<action> function

        If the resource does not declare a check_$action_complete function,
        we declare COMPLETE status as soon as the handle_$action call has
        finished, and if no handle_$action function is declared, then we do
        nothing, useful e.g if the resource requires no action for a given
        state transition
        '''
        assert action in self.ACTIONS, 'Invalid action %s' % action

        try:
            self.state_set(action, self.IN_PROGRESS)

            action_l = action.lower()
            handle = getattr(self, 'handle_%s' % action_l, None)
            check = getattr(self, 'check_%s_complete' % action_l, None)

            if callable(pre_func):
                pre_func()

            handle_data = None
            if callable(handle):
                handle_data = handle()
                yield
                if callable(check):
                    while not check(handle_data):
                        yield
        except Exception as ex:
            logger.exception('%s : %s' % (action, str(self)))
            failure = exception.ResourceFailure(ex)
            self.state_set(action, self.FAILED, str(failure))
            raise failure
        except:
            with excutils.save_and_reraise_exception():
                try:
                    self.state_set(action, self.FAILED,
                                   '%s aborted' % action)
                except Exception:
                    logger.exception('Error marking resource as failed')
        else:
            self.state_set(action, self.COMPLETE)

    def create(self):
        '''
        Create the resource. Subclasses should provide a handle_create() method
        to customise creation.
        '''
        assert None in (self.action, self.status), 'invalid state for create'

        logger.info('creating %s' % str(self))

        # Re-resolve the template, since if the resource Ref's
        # the AWS::StackId pseudo parameter, it will change after
        # the parser.Stack is stored (which is after the resources
        # are __init__'d, but before they are create()'d)
        self.t = self.stack.resolve_static_data(self.json_snippet)
        self.properties = Properties(self.properties_schema,
                                     self.t.get('Properties', {}),
                                     self.stack.resolve_runtime_data,
                                     self.name)
        return self._do_action(self.CREATE, self.properties.validate)

    def update(self, after, before=None):
        '''
        update the resource. Subclasses should provide a handle_update() method
        to customise update, the base-class handle_update will fail by default.
        '''
        if before is None:
            before = self.parsed_template()

        if (self.action, self.status) in ((self.CREATE, self.IN_PROGRESS),
                                         (self.UPDATE, self.IN_PROGRESS)):
            raise exception.ResourceFailure(Exception(
                'Resource update already requested'))

        logger.info('updating %s' % str(self))

        try:
            self.state_set(self.UPDATE, self.IN_PROGRESS)
            properties = Properties(self.properties_schema,
                                    after.get('Properties', {}),
                                    self.stack.resolve_runtime_data,
                                    self.name)
            properties.validate()
            tmpl_diff = self.update_template_diff(after, before)
            prop_diff = self.update_template_diff_properties(after, before)
            if callable(getattr(self, 'handle_update', None)):
                result = self.handle_update(after, tmpl_diff, prop_diff)
        except UpdateReplace:
            logger.debug("Resource %s update requires replacement" % self.name)
            raise
        except Exception as ex:
            logger.exception('update %s : %s' % (str(self), str(ex)))
            failure = exception.ResourceFailure(ex)
            self.state_set(self.UPDATE, self.FAILED, str(failure))
            raise failure
        else:
            self.t = self.stack.resolve_static_data(after)
            self.state_set(self.UPDATE, self.COMPLETE)

    def suspend(self):
        '''
        Suspend the resource.  Subclasses should provide a handle_suspend()
        method to implement suspend
        '''
        # Don't try to suspend the resource unless it's in a stable state
        if (self.action == self.DELETE or self.status != self.COMPLETE):
            exc = exception.Error('State %s invalid for suspend'
                                  % str(self.state))
            raise exception.ResourceFailure(exc)

        logger.info('suspending %s' % str(self))
        return self._do_action(self.SUSPEND)

    def resume(self):
        '''
        Resume the resource.  Subclasses should provide a handle_resume()
        method to implement resume
        '''
        # Can't resume a resource unless it's SUSPEND_COMPLETE
        if self.state != (self.SUSPEND, self.COMPLETE):
            exc = exception.Error('State %s invalid for resume'
                                  % str(self.state))
            raise exception.ResourceFailure(exc)

        logger.info('resuming %s' % str(self))
        return self._do_action(self.RESUME)

    def physical_resource_name(self):
        if self.id is None:
            return None

        return '%s-%s-%s' % (self.stack.name,
                             self.name,
                             short_id.get_id(self.id))

    def validate(self):
        logger.info('Validating %s' % str(self))

        self.validate_deletion_policy(self.t)
        return self.properties.validate()

    @classmethod
    def validate_deletion_policy(cls, template):
        deletion_policy = template.get('DeletionPolicy', 'Delete')
        if deletion_policy not in ('Delete', 'Retain', 'Snapshot'):
            msg = 'Invalid DeletionPolicy %s' % deletion_policy
            raise exception.StackValidationFailed(message=msg)
        elif deletion_policy == 'Snapshot':
            if not callable(getattr(cls, 'handle_snapshot_delete', None)):
                msg = 'Snapshot DeletionPolicy not supported'
                raise exception.StackValidationFailed(message=msg)

    def delete(self):
        '''
        Delete the resource. Subclasses should provide a handle_delete() method
        to customise deletion.
        '''
        if (self.action, self.status) == (self.DELETE, self.COMPLETE):
            return
        # No need to delete if the resource has never been created
        if self.action is None:
            return

        initial_state = self.state

        logger.info('deleting %s' % str(self))

        try:
            self.state_set(self.DELETE, self.IN_PROGRESS)

            deletion_policy = self.t.get('DeletionPolicy', 'Delete')
            if deletion_policy == 'Delete':
                if callable(getattr(self, 'handle_delete', None)):
                    self.handle_delete()
            elif deletion_policy == 'Snapshot':
                if callable(getattr(self, 'handle_snapshot_delete', None)):
                    self.handle_snapshot_delete(initial_state)
        except Exception as ex:
            logger.exception('Delete %s', str(self))
            failure = exception.ResourceFailure(ex)
            self.state_set(self.DELETE, self.FAILED, str(failure))
            raise failure
        except:
            with excutils.save_and_reraise_exception():
                try:
                    self.state_set(self.DELETE, self.FAILED,
                                   'Deletion aborted')
                except Exception:
                    logger.exception('Error marking resource deletion failed')
        else:
            self.state_set(self.DELETE, self.COMPLETE)

    def destroy(self):
        '''
        Delete the resource and remove it from the database.
        '''
        self.delete()

        if self.id is None:
            return

        try:
            db_api.resource_get(self.context, self.id).delete()
        except exception.NotFound:
            # Don't fail on delete if the db entry has
            # not been created yet.
            pass

        self.id = None

    def resource_id_set(self, inst):
        self.resource_id = inst
        if self.id is not None:
            try:
                rs = db_api.resource_get(self.context, self.id)
                rs.update_and_save({'nova_instance': self.resource_id})
            except Exception as ex:
                logger.warn('db error %s' % str(ex))

    def _store(self):
        '''Create the resource in the database.'''
        try:
            rs = {'action': self.action,
                  'status': self.status,
                  'status_reason': self.status_reason,
                  'stack_id': self.stack.id,
                  'nova_instance': self.resource_id,
                  'name': self.name,
                  'rsrc_metadata': self.metadata,
                  'stack_name': self.stack.name}

            new_rs = db_api.resource_create(self.context, rs)
            self.id = new_rs.id

            self.stack.updated_time = datetime.utcnow()

        except Exception as ex:
            logger.error('DB error %s' % str(ex))

    def _add_event(self, action, status, reason):
        '''Add a state change event to the database.'''
        ev = event.Event(self.context, self.stack, self,
                         action, status, reason,
                         self.resource_id, self.properties)

        try:
            ev.store()
        except Exception as ex:
            logger.error('DB error %s' % str(ex))

    def _store_or_update(self, action, status, reason):
        self.action = action
        self.status = status
        self.status_reason = reason

        if self.id is not None:
            try:
                rs = db_api.resource_get(self.context, self.id)
                rs.update_and_save({'action': self.action,
                                    'status': self.status,
                                    'status_reason': reason,
                                    'nova_instance': self.resource_id})

                self.stack.updated_time = datetime.utcnow()
            except Exception as ex:
                logger.error('DB error %s' % str(ex))

        # store resource in DB on transition to CREATE_IN_PROGRESS
        # all other transistions (other than to DELETE_COMPLETE)
        # should be handled by the update_and_save above..
        elif (action, status) == (self.CREATE, self.IN_PROGRESS):
            self._store()

    def _resolve_attribute(self, name):
        """
        Default implementation; should be overridden by resources that expose
        attributes

        :param name: The attribute to resolve
        :returns: the resource attribute named key
        """
        # By default, no attributes resolve
        pass

    def state_set(self, action, status, reason="state changed"):
        if action not in self.ACTIONS:
            raise ValueError("Invalid action %s" % action)

        if status not in self.STATUSES:
            raise ValueError("Invalid status %s" % status)

        old_state = (self.action, self.status)
        new_state = (action, status)
        self._store_or_update(action, status, reason)

        if new_state != old_state:
            self._add_event(action, status, reason)

    @property
    def state(self):
        '''Returns state, tuple of action, status.'''
        return (self.action, self.status)

    def FnGetRefId(self):
        '''
        http://docs.amazonwebservices.com/AWSCloudFormation/latest/UserGuide/\
        intrinsic-function-reference-ref.html
        '''
        if self.resource_id is not None:
            return unicode(self.resource_id)
        else:
            return unicode(self.name)

    def FnGetAtt(self, key):
        '''
        http://docs.amazonwebservices.com/AWSCloudFormation/latest/UserGuide/\
        intrinsic-function-reference-getatt.html
        '''
        try:
            return self.attributes[key]
        except KeyError:
            raise exception.InvalidTemplateAttribute(resource=self.name,
                                                     key=key)

    def FnBase64(self, data):
        '''
        http://docs.amazonwebservices.com/AWSCloudFormation/latest/UserGuide/\
            intrinsic-function-reference-base64.html
        '''
        return base64.b64encode(data)

    def handle_update(self, json_snippet=None, tmpl_diff=None, prop_diff=None):
        raise UpdateReplace(self.name)

    def metadata_update(self, new_metadata=None):
        '''
        No-op for resources which don't explicitly override this method
        '''
        if new_metadata:
            logger.warning("Resource %s does not implement metadata update" %
                           self.name)
