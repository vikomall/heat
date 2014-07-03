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

import copy
from novaclient import exceptions as nova_exceptions
from oslo.config import cfg
import six

from heat.common import exception
from heat.engine import attributes
from heat.engine import constraints
from heat.engine import properties
from heat.engine import resource
from heat.engine.resources import glance_utils
from heat.engine.resources.network_interface import NetworkInterface
from heat.engine.resources.neutron import neutron
from heat.engine.resources import nova_utils
from heat.engine.resources import volume
from heat.engine import scheduler
from heat.engine import signal_responder
from heat.openstack.common.gettextutils import _
from heat.openstack.common import log as logging

cfg.CONF.import_opt('instance_user', 'heat.common.config')

LOG = logging.getLogger(__name__)


class Restarter(signal_responder.SignalResponder):
    PROPERTIES = (
        INSTANCE_ID,
    ) = (
        'InstanceId',
    )

    ATTRIBUTES = (
        ALARM_URL,
    ) = (
        'AlarmUrl',
    )

    properties_schema = {
        INSTANCE_ID: properties.Schema(
            properties.Schema.STRING,
            _('Instance ID to be restarted.'),
            required=True
        ),
    }

    attributes_schema = {
        ALARM_URL: attributes.Schema(
            _("A signed url to handle the alarm (Heat extension).")
        ),
    }

    def _find_resource(self, resource_id):
        '''
        Return the resource with the specified instance ID, or None if it
        cannot be found.
        '''
        for resource in self.stack.itervalues():
            if resource.resource_id == resource_id:
                return resource
        return None

    def handle_create(self):
        super(Restarter, self).handle_create()
        self.resource_id_set(self._get_user_id())

    def handle_signal(self, details=None):
        if self.action in (self.SUSPEND, self.DELETE):
            msg = _('Cannot signal resource during %s') % self.action
            raise Exception(msg)

        if details is None:
            alarm_state = 'alarm'
        else:
            alarm_state = details.get('state', 'alarm').lower()

        LOG.info(_('%(name)s Alarm, new state %(state)s')
                 % {'name': self.name, 'state': alarm_state})

        if alarm_state != 'alarm':
            return

        victim = self._find_resource(self.properties[self.INSTANCE_ID])
        if victim is None:
            LOG.info(_('%(name)s Alarm, can not find instance %(instance)s')
                     % {'name': self.name,
                        'instance': self.properties[self.INSTANCE_ID]})
            return

        LOG.info(_('%(name)s Alarm, restarting resource: %(victim)s')
                 % {'name': self.name, 'victim': victim.name})
        self.stack.restart_resource(victim.name)

    def _resolve_attribute(self, name):
        '''
        heat extension: "AlarmUrl" returns the url to post to the policy
        when there is an alarm.
        '''
        if name == self.ALARM_URL and self.resource_id is not None:
            return unicode(self._get_signed_url())


class Instance(resource.Resource):

    PROPERTIES = (
        IMAGE_ID, INSTANCE_TYPE, KEY_NAME, AVAILABILITY_ZONE,
        DISABLE_API_TERMINATION, KERNEL_ID, MONITORING,
        PLACEMENT_GROUP_NAME, PRIVATE_IP_ADDRESS, RAM_DISK_ID,
        SECURITY_GROUPS, SECURITY_GROUP_IDS, NETWORK_INTERFACES,
        SOURCE_DEST_CHECK, SUBNET_ID, TAGS, NOVA_SCHEDULER_HINTS, TENANCY,
        USER_DATA, VOLUMES,
    ) = (
        'ImageId', 'InstanceType', 'KeyName', 'AvailabilityZone',
        'DisableApiTermination', 'KernelId', 'Monitoring',
        'PlacementGroupName', 'PrivateIpAddress', 'RamDiskId',
        'SecurityGroups', 'SecurityGroupIds', 'NetworkInterfaces',
        'SourceDestCheck', 'SubnetId', 'Tags', 'NovaSchedulerHints', 'Tenancy',
        'UserData', 'Volumes',
    )

    _TAG_KEYS = (
        TAG_KEY, TAG_VALUE,
    ) = (
        'Key', 'Value',
    )

    _NOVA_SCHEDULER_HINT_KEYS = (
        NOVA_SCHEDULER_HINT_KEY, NOVA_SCHEDULER_HINT_VALUE,
    ) = (
        'Key', 'Value',
    )

    _VOLUME_KEYS = (
        VOLUME_DEVICE, VOLUME_ID,
    ) = (
        'Device', 'VolumeId',
    )

    ATTRIBUTES = (
        AVAILABILITY_ZONE_ATTR, PRIVATE_DNS_NAME, PUBLIC_DNS_NAME, PRIVATE_IP,
        PUBLIC_IP,
    ) = (
        'AvailabilityZone', 'PrivateDnsName', 'PublicDnsName', 'PrivateIp',
        'PublicIp',
    )

    properties_schema = {
        IMAGE_ID: properties.Schema(
            properties.Schema.STRING,
            _('Glance image ID or name.'),
            constraints=[
                constraints.CustomConstraint('glance.image')
            ],
            required=True
        ),
        # AWS does not require InstanceType but Heat does because the nova
        # create api call requires a flavor
        INSTANCE_TYPE: properties.Schema(
            properties.Schema.STRING,
            _('Nova instance type (flavor).'),
            required=True,
            update_allowed=True
        ),
        KEY_NAME: properties.Schema(
            properties.Schema.STRING,
            _('Optional Nova keypair name.'),
            constraints=[
                constraints.CustomConstraint("nova.keypair")
            ]
        ),
        AVAILABILITY_ZONE: properties.Schema(
            properties.Schema.STRING,
            _('Availability zone to launch the instance in.')
        ),
        DISABLE_API_TERMINATION: properties.Schema(
            properties.Schema.STRING,
            _('Not Implemented.'),
            implemented=False
        ),
        KERNEL_ID: properties.Schema(
            properties.Schema.STRING,
            _('Not Implemented.'),
            implemented=False
        ),
        MONITORING: properties.Schema(
            properties.Schema.BOOLEAN,
            _('Not Implemented.'),
            implemented=False
        ),
        PLACEMENT_GROUP_NAME: properties.Schema(
            properties.Schema.STRING,
            _('Not Implemented.'),
            implemented=False
        ),
        PRIVATE_IP_ADDRESS: properties.Schema(
            properties.Schema.STRING,
            _('Not Implemented.'),
            implemented=False
        ),
        RAM_DISK_ID: properties.Schema(
            properties.Schema.STRING,
            _('Not Implemented.'),
            implemented=False
        ),
        SECURITY_GROUPS: properties.Schema(
            properties.Schema.LIST,
            _('Security group names to assign.')
        ),
        SECURITY_GROUP_IDS: properties.Schema(
            properties.Schema.LIST,
            _('Security group IDs to assign.')
        ),
        NETWORK_INTERFACES: properties.Schema(
            properties.Schema.LIST,
            _('Network interfaces to associate with instance.'),
            update_allowed=True
        ),
        SOURCE_DEST_CHECK: properties.Schema(
            properties.Schema.BOOLEAN,
            _('Not Implemented.'),
            implemented=False
        ),
        SUBNET_ID: properties.Schema(
            properties.Schema.STRING,
            _('Subnet ID to launch instance in.'),
            update_allowed=True
        ),
        TAGS: properties.Schema(
            properties.Schema.LIST,
            _('Tags to attach to instance.'),
            schema=properties.Schema(
                properties.Schema.MAP,
                schema={
                    TAG_KEY: properties.Schema(
                        properties.Schema.STRING,
                        required=True
                    ),
                    TAG_VALUE: properties.Schema(
                        properties.Schema.STRING,
                        required=True
                    ),
                },
            ),
            update_allowed=True
        ),
        NOVA_SCHEDULER_HINTS: properties.Schema(
            properties.Schema.LIST,
            _('Scheduler hints to pass to Nova (Heat extension).'),
            schema=properties.Schema(
                properties.Schema.MAP,
                schema={
                    NOVA_SCHEDULER_HINT_KEY: properties.Schema(
                        properties.Schema.STRING,
                        required=True
                    ),
                    NOVA_SCHEDULER_HINT_VALUE: properties.Schema(
                        properties.Schema.STRING,
                        required=True
                    ),
                },
            )
        ),
        TENANCY: properties.Schema(
            properties.Schema.STRING,
            _('Not Implemented.'),
            constraints=[
                constraints.AllowedValues(['dedicated', 'default']),
            ],
            implemented=False
        ),
        USER_DATA: properties.Schema(
            properties.Schema.STRING,
            _('User data to pass to instance.')
        ),
        VOLUMES: properties.Schema(
            properties.Schema.LIST,
            _('Volumes to attach to instance.'),
            default=[],
            schema=properties.Schema(
                properties.Schema.MAP,
                schema={
                    VOLUME_DEVICE: properties.Schema(
                        properties.Schema.STRING,
                        _('The device where the volume is exposed on the '
                          'instance. This assignment may not be honored and '
                          'it is advised that the path '
                          '/dev/disk/by-id/virtio-<VolumeId> be used '
                          'instead.'),
                        required=True
                    ),
                    VOLUME_ID: properties.Schema(
                        properties.Schema.STRING,
                        _('The ID of the volume to be attached.'),
                        required=True
                    ),
                }
            )
        ),
    }

    attributes_schema = {
        AVAILABILITY_ZONE_ATTR: attributes.Schema(
            _('The Availability Zone where the specified instance is '
              'launched.')
        ),
        PRIVATE_DNS_NAME: attributes.Schema(
            _('Private DNS name of the specified instance.')
        ),
        PUBLIC_DNS_NAME: attributes.Schema(
            _('Public DNS name of the specified instance.')
        ),
        PRIVATE_IP: attributes.Schema(
            _('Private IP address of the specified instance.')
        ),
        PUBLIC_IP: attributes.Schema(
            _('Public IP address of the specified instance.')
        ),
    }

    # Server host name limit to 53 characters by due to typical default
    # linux HOST_NAME_MAX of 64, minus the .novalocal appended to the name
    physical_resource_name_limit = 53

    def __init__(self, name, json_snippet, stack):
        super(Instance, self).__init__(name, json_snippet, stack)
        self.ipaddress = None

    def _set_ipaddress(self, networks):
        '''
        Read the server's IP address from a list of networks provided by Nova
        '''
        # Just record the first ipaddress
        for n in networks:
            if len(networks[n]) > 0:
                self.ipaddress = networks[n][0]
                break

    def _ipaddress(self):
        '''
        Return the server's IP address, fetching it from Nova if necessary
        '''
        if self.ipaddress is None:
            self.ipaddress = nova_utils.server_to_ipaddress(
                self.nova(), self.resource_id)

        return self.ipaddress or '0.0.0.0'

    def _resolve_attribute(self, name):
        res = None
        if name == self.AVAILABILITY_ZONE_ATTR:
            res = self.properties[self.AVAILABILITY_ZONE]
        elif name in self.ATTRIBUTES[1:]:
            res = self._ipaddress()

        LOG.info(_('%(name)s._resolve_attribute(%(attname)s) == %(res)s'),
                 {'name': self.name, 'attname': name, 'res': res})
        return unicode(res) if res else None

    def _build_nics(self, network_interfaces,
                    security_groups=None, subnet_id=None):

        nics = None

        if network_interfaces:
            unsorted_nics = []
            for entry in network_interfaces:
                nic = (entry
                       if not isinstance(entry, basestring)
                       else {'NetworkInterfaceId': entry,
                             'DeviceIndex': len(unsorted_nics)})
                unsorted_nics.append(nic)
            sorted_nics = sorted(unsorted_nics,
                                 key=lambda nic: int(nic['DeviceIndex']))
            nics = [{'port-id': nic['NetworkInterfaceId']}
                    for nic in sorted_nics]
        else:
            # if SubnetId property in Instance, ensure subnet exists
            if subnet_id:
                neutronclient = self.neutron()
                network_id = NetworkInterface.network_id_from_subnet_id(
                    neutronclient, subnet_id)
                # if subnet verified, create a port to use this subnet
                # if port is not created explicitly, nova will choose
                # the first subnet in the given network.
                if network_id:
                    fixed_ip = {'subnet_id': subnet_id}
                    props = {
                        'admin_state_up': True,
                        'network_id': network_id,
                        'fixed_ips': [fixed_ip]
                    }

                    if security_groups:
                        props['security_groups'] = \
                            neutron.NeutronResource.get_secgroup_uuids(
                                security_groups, self.neutron())

                    port = neutronclient.create_port({'port': props})['port']
                    nics = [{'port-id': port['id']}]

        return nics

    def _get_security_groups(self):
        security_groups = []
        for key in (self.SECURITY_GROUPS, self.SECURITY_GROUP_IDS):
            if self.properties.get(key) is not None:
                for sg in self.properties.get(key):
                    security_groups.append(sg)
        if not security_groups:
            security_groups = None
        return security_groups

    def _get_nova_metadata(self, properties):
        if properties is None or properties.get(self.TAGS) is None:
            return None

        return dict((tm[self.TAG_KEY], tm[self.TAG_VALUE])
                    for tm in properties[self.TAGS])

    def handle_create(self):
        security_groups = self._get_security_groups()

        userdata = self.properties[self.USER_DATA] or ''
        flavor = self.properties[self.INSTANCE_TYPE]
        availability_zone = self.properties[self.AVAILABILITY_ZONE]

        image_name = self.properties[self.IMAGE_ID]

        image_id = glance_utils.get_image_id(self.glance(), image_name)

        flavor_id = nova_utils.get_flavor_id(self.nova(), flavor)

        scheduler_hints = {}
        if self.properties[self.NOVA_SCHEDULER_HINTS]:
            for tm in self.properties[self.NOVA_SCHEDULER_HINTS]:
                # adopted from novaclient shell
                hint = tm[self.TAG_KEY]
                hint_value = tm[self.TAG_VALUE]
                if hint in scheduler_hints:
                    if isinstance(scheduler_hints[hint], six.string_types):
                        scheduler_hints[hint] = [scheduler_hints[hint]]
                    scheduler_hints[hint].append(hint_value)
                else:
                    scheduler_hints[hint] = hint_value
        else:
            scheduler_hints = None

        nics = self._build_nics(self.properties[self.NETWORK_INTERFACES],
                                security_groups=security_groups,
                                subnet_id=self.properties[self.SUBNET_ID])
        server = None

        # FIXME(shadower): the instance_user config option is deprecated. Once
        # it's gone, we should always use ec2-user for compatibility with
        # CloudFormation.
        if cfg.CONF.instance_user:
            instance_user = cfg.CONF.instance_user
        else:
            instance_user = 'ec2-user'

        try:
            server = self.nova().servers.create(
                name=self.physical_resource_name(),
                image=image_id,
                flavor=flavor_id,
                key_name=self.properties[self.KEY_NAME],
                security_groups=security_groups,
                userdata=nova_utils.build_userdata(self, userdata,
                                                   instance_user),
                meta=self._get_nova_metadata(self.properties),
                scheduler_hints=scheduler_hints,
                nics=nics,
                availability_zone=availability_zone)
        finally:
            # Avoid a race condition where the thread could be cancelled
            # before the ID is stored
            if server is not None:
                self.resource_id_set(server.id)

        return server, scheduler.TaskRunner(self._attach_volumes_task())

    def _attach_volumes_task(self):
        attach_tasks = (volume.VolumeAttachTask(self.stack,
                                                self.resource_id,
                                                volume_id,
                                                device)
                        for volume_id, device in self.volumes())
        return scheduler.PollingTaskGroup(attach_tasks)

    def check_create_complete(self, cookie):
        server, volume_attach_task = cookie
        return (self._check_active(server) and
                self._check_volume_attached(server, volume_attach_task))

    def _check_volume_attached(self, server, volume_attach_task):
        if not volume_attach_task.started():
            self._set_ipaddress(server.networks)
            volume_attach_task.start()
            return volume_attach_task.done()
        else:
            return volume_attach_task.step()

    def _check_active(self, server):
        if server.status != 'ACTIVE':
            nova_utils.refresh_server(server)

        if server.status == 'ACTIVE':
            return True

        # Some clouds append extra (STATUS) strings to the status
        short_server_status = server.status.split('(')[0]
        if short_server_status in nova_utils.deferred_server_statuses:
            return False

        if server.status == 'ERROR':
            fault = getattr(server, 'fault', {})
            message = fault.get('message', 'Unknown')
            code = fault.get('code', 500)
            exc = exception.Error(_("Creation of server %(server)s "
                                    "failed: %(message)s (%(code)s)") %
                                  dict(server=server.name,
                                       message=message,
                                       code=code))
            raise exc

        exc = exception.Error(_("Creation of server %(server)s failed "
                                "with unknown status: %(status)s") %
                              dict(server=server.name,
                                   status=server.status))
        raise exc

    def volumes(self):
        """
        Return an iterator over (volume_id, device) tuples for all volumes
        that should be attached to this instance.
        """
        volumes = self.properties[self.VOLUMES]

        return ((vol[self.VOLUME_ID],
                 vol[self.VOLUME_DEVICE]) for vol in volumes)

    def _remove_matched_ifaces(self, old_network_ifaces, new_network_ifaces):
        # find matches and remove them from old and new ifaces
        old_network_ifaces_copy = copy.deepcopy(old_network_ifaces)
        for iface in old_network_ifaces_copy:
            if iface in new_network_ifaces:
                new_network_ifaces.remove(iface)
                old_network_ifaces.remove(iface)

    def handle_update(self, json_snippet, tmpl_diff, prop_diff):
        if 'Metadata' in tmpl_diff:
            self.metadata_set(tmpl_diff['Metadata'])
        checkers = []
        server = None
        if self.TAGS in prop_diff:
            server = self.nova().servers.get(self.resource_id)
            nova_utils.meta_update(self.nova(),
                                   server,
                                   self._get_nova_metadata(prop_diff))

        if self.INSTANCE_TYPE in prop_diff:
            flavor = prop_diff[self.INSTANCE_TYPE]
            flavor_id = nova_utils.get_flavor_id(self.nova(), flavor)
            if not server:
                server = self.nova().servers.get(self.resource_id)
            checker = scheduler.TaskRunner(nova_utils.resize, server, flavor,
                                           flavor_id)
            checkers.append(checker)
        if self.NETWORK_INTERFACES in prop_diff:
            new_network_ifaces = prop_diff.get(self.NETWORK_INTERFACES)
            old_network_ifaces = self.properties.get(self.NETWORK_INTERFACES)
            subnet_id = (
                prop_diff.get(self.SUBNET_ID) or
                self.properties.get(self.SUBNET_ID))
            security_groups = self._get_security_groups()
            if not server:
                server = self.nova().servers.get(self.resource_id)
            # if there is entrys in old_network_ifaces and new_network_ifaces,
            # remove the same entrys from old and new ifaces
            if old_network_ifaces and new_network_ifaces:
                # there are four situations:
                # 1.old includes new, such as: old = 2,3, new = 2
                # 2.new includes old, such as: old = 2,3, new = 1,2,3
                # 3.has overlaps, such as: old = 2,3, new = 1,2
                # 4.different, such as: old = 2,3, new = 1,4
                # detach unmatched ones in old, attach unmatched ones in new
                self._remove_matched_ifaces(old_network_ifaces,
                                            new_network_ifaces)
                if old_network_ifaces:
                    old_nics = self._build_nics(old_network_ifaces)
                    for nic in old_nics:
                        checker = scheduler.TaskRunner(
                            server.interface_detach,
                            nic['port-id'])
                        checkers.append(checker)
                if new_network_ifaces:
                    new_nics = self._build_nics(new_network_ifaces)
                    for nic in new_nics:
                        checker = scheduler.TaskRunner(
                            server.interface_attach,
                            nic['port-id'],
                            None, None)
                        checkers.append(checker)
            # if the interfaces not come from property 'NetworkInterfaces',
            # the situation is somewhat complex, so to detach the old ifaces,
            # and then attach the new ones.
            else:
                interfaces = server.interface_list()
                for iface in interfaces:
                    checker = scheduler.TaskRunner(server.interface_detach,
                                                   iface.port_id)
                    checkers.append(checker)
                nics = self._build_nics(new_network_ifaces,
                                        security_groups=security_groups,
                                        subnet_id=subnet_id)
                # 'SubnetId' property is empty(or None) and
                # 'NetworkInterfaces' property is empty(or None),
                # _build_nics() will return nics = None,we should attach
                # first free port, according to similar behavior during
                # instance creation
                if not nics:
                    checker = scheduler.TaskRunner(server.interface_attach,
                                                   None, None, None)
                    checkers.append(checker)
                else:
                    for nic in nics:
                        checker = scheduler.TaskRunner(
                            server.interface_attach,
                            nic['port-id'], None, None)
                        checkers.append(checker)

        if checkers:
            checkers[0].start()
        return checkers

    def check_update_complete(self, checkers):
        '''Push all checkers to completion in list order.'''
        for checker in checkers:
            if not checker.started():
                checker.start()
            if not checker.step():
                return False
        return True

    def metadata_update(self, new_metadata=None):
        '''
        Refresh the metadata if new_metadata is None
        '''
        if new_metadata is None:
            self.metadata_set(self.t.metadata())

    def validate(self):
        '''
        Validate any of the provided params
        '''
        res = super(Instance, self).validate()
        if res:
            return res

        # check validity of security groups vs. network interfaces
        security_groups = self._get_security_groups()
        if security_groups and self.properties.get(self.NETWORK_INTERFACES):
            raise exception.ResourcePropertyConflict(
                '/'.join([self.SECURITY_GROUPS, self.SECURITY_GROUP_IDS]),
                self.NETWORK_INTERFACES)

    @scheduler.wrappertask
    def _delete_server(self, server):
        '''
        Return a co-routine that deletes the server and waits for it to
        disappear from Nova.
        '''
        yield self._detach_volumes_task()()
        server.delete()

        while True:
            yield

            try:
                nova_utils.refresh_server(server)
                if server.status == "DELETED":
                    break
            except nova_exceptions.NotFound:
                break
        self.resource_id_set(None)

    def _detach_volumes_task(self):
        '''
        Detach volumes from the instance
        '''
        detach_tasks = (volume.VolumeDetachTask(self.stack,
                                                self.resource_id,
                                                volume_id)
                        for volume_id, device in self.volumes())
        return scheduler.PollingTaskGroup(detach_tasks)

    def handle_delete(self):
        '''
        Delete an instance, blocking until it is disposed by OpenStack
        '''
        if self.resource_id is None:
            return

        try:
            server = self.nova().servers.get(self.resource_id)
        except nova_exceptions.NotFound:
            self.resource_id_set(None)
            return

        server_delete_task = scheduler.TaskRunner(self._delete_server,
                                                  server=server)
        server_delete_task.start()
        return server_delete_task

    def check_delete_complete(self, server_delete_task):
        # if the resource was already deleted, server_delete_task will be None
        if server_delete_task is None:
            return True
        else:
            return server_delete_task.step()

    def handle_suspend(self):
        '''
        Suspend an instance - note we do not wait for the SUSPENDED state,
        this is polled for by check_suspend_complete in a similar way to the
        create logic so we can take advantage of coroutines
        '''
        if self.resource_id is None:
            raise exception.Error(_('Cannot suspend %s, resource_id not set') %
                                  self.name)

        try:
            server = self.nova().servers.get(self.resource_id)
        except nova_exceptions.NotFound:
            raise exception.NotFound(_('Failed to find instance %s') %
                                     self.resource_id)
        else:
            LOG.debug("suspending instance %s" % self.resource_id)
            # We want the server.suspend to happen after the volume
            # detachement has finished, so pass both tasks and the server
            suspend_runner = scheduler.TaskRunner(server.suspend)
            volumes_runner = scheduler.TaskRunner(self._detach_volumes_task())
            return server, suspend_runner, volumes_runner

    def check_suspend_complete(self, cookie):
        server, suspend_runner, volumes_runner = cookie

        if not volumes_runner.started():
            volumes_runner.start()

        if volumes_runner.done():
            if not suspend_runner.started():
                suspend_runner.start()

            if suspend_runner.done():
                if server.status == 'SUSPENDED':
                    return True

                nova_utils.refresh_server(server)
                LOG.debug("%(name)s check_suspend_complete "
                          "status = %(status)s",
                          {'name': self.name, 'status': server.status})
                if server.status in list(nova_utils.deferred_server_statuses +
                                         ['ACTIVE']):
                    return server.status == 'SUSPENDED'
                else:
                    raise exception.Error(_(' nova reported unexpected '
                                            'instance[%(instance)s] '
                                            'status[%(status)s]') %
                                          {'instance': self.name,
                                           'status': server.status})
            else:
                suspend_runner.step()
        else:
            volumes_runner.step()

    def handle_resume(self):
        '''
        Resume an instance - note we do not wait for the ACTIVE state,
        this is polled for by check_resume_complete in a similar way to the
        create logic so we can take advantage of coroutines
        '''
        if self.resource_id is None:
            raise exception.Error(_('Cannot resume %s, resource_id not set') %
                                  self.name)

        try:
            server = self.nova().servers.get(self.resource_id)
        except nova_exceptions.NotFound:
            raise exception.NotFound(_('Failed to find instance %s') %
                                     self.resource_id)
        else:
            LOG.debug("resuming instance %s" % self.resource_id)
            server.resume()
            return server, scheduler.TaskRunner(self._attach_volumes_task())

    def check_resume_complete(self, cookie):
        server, volume_attach_task = cookie
        return (self._check_active(server) and
                self._check_volume_attached(server, volume_attach_task))


def resource_mapping():
    return {
        'AWS::EC2::Instance': Instance,
        'OS::Heat::HARestarter': Restarter,
    }
