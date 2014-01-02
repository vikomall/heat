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

from oslo.config import cfg

cfg.CONF.import_opt('instance_user', 'heat.common.config')

from heat.common import exception
from heat.engine import clients
from heat.engine import scheduler
from heat.engine.resources import nova_utils
from heat.engine import resource
from heat.openstack.common.gettextutils import _
from heat.openstack.common import log as logging
from heat.openstack.common import uuidutils

logger = logging.getLogger(__name__)


class Server(resource.Resource):

    block_mapping_schema = {
        'device_name': {
            'Type': 'String',
            'Required': True,
            'Description': _('A device name where the volume will be '
                             'attached in the system at /dev/device_name. '
                             'This value is typically vda.')},
        'volume_id': {
            'Type': 'String',
            'Description': _('The ID of the volume to boot from. Only one of '
                             'volume_id or snapshot_id should be provided.')},
        'snapshot_id': {
            'Type': 'String',
            'Description': _('The ID of the snapshot to create a volume '
                             'from.')},
        'volume_size': {
            'Type': 'String',
            'Description': _('The size of the volume, in GB. It is safe to '
                             'leave this blank and have the Compute service '
                             'infer the size.')},
        'delete_on_termination': {
            'Type': 'Boolean',
            'Description': _('Indicate whether the volume should be deleted '
                             'when the server is terminated.')}
    }

    networks_schema = {
        'uuid': {
            'Type': 'String',
            'Description': _('DEPRECATED! ID of network '
                             'to create a port on.')},
        'network': {
            'Type': 'String',
            'Description': _('Name or ID of network to create a port on.')},
        'fixed_ip': {
            'Type': 'String',
            'Description': _('Fixed IP address to specify for the port '
                             'created on the requested network.')},
        'port': {
            'Type': 'String',
            'Description': _('ID of an existing port to associate with '
                             'this server.')},
    }

    properties_schema = {
        'name': {
            'Type': 'String',
            'Description': _('Optional server name.')},
        'image': {
            'Type': 'String',
            'Description': _('The ID or name of the image to boot with.'),
            'UpdateAllowed': True},
        'block_device_mapping': {
            'Type': 'List',
            'Description': _('Block device mappings for this server.'),
            'Schema': {
                'Type': 'Map',
                'Schema': block_mapping_schema
            }
        },
        'flavor': {
            'Type': 'String',
            'Description': _('The ID or name of the flavor to boot onto.'),
            'Required': True,
            'UpdateAllowed': True},
        'flavor_update_policy': {
            'Type': 'String',
            'Description': _('Policy on how to apply a flavor update; either '
                             'by requesting a server resize or by replacing '
                             'the entire server.'),
            'Default': 'RESIZE',
            'AllowedValues': ['RESIZE', 'REPLACE'],
            'UpdateAllowed': True},
        'image_update_policy': {
            'Type': 'String',
            'Default': 'REPLACE',
            'Description': _('Policy on how to apply an image-id update; '
                             'either by requesting a server rebuild or by '
                             'replacing the entire server'),
            'AllowedValues': ['REBUILD', 'REPLACE',
                              'REBUILD_PRESERVE_EPHEMERAL'],
            'UpdateAllowed': True},
        'key_name': {
            'Type': 'String',
            'Description': _('Name of keypair to inject into the server.')},
        'admin_user': {
            'Type': 'String',
            'Default': cfg.CONF.instance_user,
            'Description': _('Name of the administrative user to use '
                             'on the server.')},
        'availability_zone': {
            'Type': 'String',
            'Description': _('Name of the availability zone for server '
                             'placement.')},
        'security_groups': {
            'Type': 'List',
            'Description': _('List of security group names or IDs.'),
            'Default': []},
        'networks': {
            'Type': 'List',
            'Description': _('An ordered list of nics to be '
                             'added to this server, with information about '
                             'connected networks, fixed ips, port etc.'),
            'Schema': {
                'Type': 'Map',
                'Schema': networks_schema
            }
        },
        'scheduler_hints': {
            'Type': 'Map',
            'Description': _('Arbitrary key-value pairs specified by the '
                             'client to help boot a server.')},
        'metadata': {
            'Type': 'Map',
            'UpdateAllowed': True,
            'Description': _('Arbitrary key/value metadata to store for this '
                             'server. A maximum of five entries is allowed, '
                             'and both keys and values must be 255 characters '
                             'or less.')},
        'user_data_format': {
            'Type': 'String',
            'Default': 'HEAT_CFNTOOLS',
            'Description': _('How the user_data should be formatted for the '
                             'server. For HEAT_CFNTOOLS, the user_data is '
                             'bundled as part of the heat-cfntools '
                             'cloud-init boot configuration data. For RAW, '
                             'the user_data is passed to Nova unmodified.'),
            'AllowedValues': ['HEAT_CFNTOOLS', 'RAW']},
        'user_data': {
            'Type': 'String',
            'Description': _('User data script to be executed by '
                             'cloud-init.'),
            'Default': ""},
        'reservation_id': {
            'Type': 'String',
            'Description': _('A UUID for the set of servers being requested.')
        },
        'config_drive': {
            'Type': 'String',
            'Description': _('value for config drive either boolean, or '
                             'volume-id.')
        },
        # diskConfig translates to API attribute OS-DCF:diskConfig
        # hence the camel case instead of underscore to separate the words
        'diskConfig': {
            'Type': 'String',
            'Description': _('Control how the disk is partitioned when the '
                             'server is created.'),
            'AllowedValues': ['AUTO', 'MANUAL']}
    }

    attributes_schema = {
        'show': _('A dict of all server details as returned by the API.'),
        'addresses': _('A dict of all network addresses as returned by '
                       'the API.'),
        'networks': _('A dict of assigned network addresses of the form: '
                      '{"public": [ip1, ip2...], "private": [ip3, ip4]}.'),
        'first_address': _('Convenience attribute to fetch the first '
                           'assigned network address, or an '
                           'empty string if nothing has been assigned '
                           'at this time. Result may not be predictable '
                           'if the server has addresses from more than one '
                           'network.'),
        'instance_name': _('AWS compatible instance name.'),
        'accessIPv4': _('The manually assigned alternative public IPv4 '
                        'address of the server.'),
        'accessIPv6': _('The manually assigned alternative public IPv6 '
                        'address of the server.'),
    }

    update_allowed_keys = ('Metadata', 'Properties')

    # Server host name limit to 53 characters by due to typical default
    # linux HOST_NAME_MAX of 64, minus the .novalocal appended to the name
    physical_resource_name_limit = 53

    def __init__(self, name, json_snippet, stack):
        super(Server, self).__init__(name, json_snippet, stack)

    def physical_resource_name(self):
        name = self.properties.get('name')
        if name:
            return name

        return super(Server, self).physical_resource_name()

    def handle_create(self):
        security_groups = self.properties.get('security_groups')

        user_data_format = self.properties.get('user_data_format')
        userdata = nova_utils.build_userdata(
            self,
            self.properties.get('user_data'),
            instance_user=self.properties['admin_user'],
            user_data_format=user_data_format)

        flavor = self.properties['flavor']
        availability_zone = self.properties['availability_zone']

        key_name = self.properties['key_name']
        if key_name:
            # confirm keypair exists
            nova_utils.get_keypair(self.nova(), key_name)

        image = self.properties.get('image')
        if image:
            image = nova_utils.get_image_id(self.nova(), image)

        flavor_id = nova_utils.get_flavor_id(self.nova(), flavor)

        instance_meta = self.properties.get('metadata')
        if instance_meta is not None:
            instance_meta = dict((key, str(value)) for (key, value) in
                                 instance_meta.items())

        scheduler_hints = self.properties.get('scheduler_hints')
        nics = self._build_nics(self.properties.get('networks'))
        block_device_mapping = self._build_block_device_mapping(
            self.properties.get('block_device_mapping'))
        reservation_id = self.properties.get('reservation_id')
        config_drive = self.properties.get('config_drive')
        disk_config = self.properties.get('diskConfig')

        server = None
        try:
            server = self.nova().servers.create(
                name=self.physical_resource_name(),
                image=image,
                flavor=flavor_id,
                key_name=key_name,
                security_groups=security_groups,
                userdata=userdata,
                meta=instance_meta,
                scheduler_hints=scheduler_hints,
                nics=nics,
                availability_zone=availability_zone,
                block_device_mapping=block_device_mapping,
                reservation_id=reservation_id,
                config_drive=config_drive,
                disk_config=disk_config)
        finally:
            # Avoid a race condition where the thread could be cancelled
            # before the ID is stored
            if server is not None:
                self.resource_id_set(server.id)

        return server

    def check_create_complete(self, server):
        return self._check_active(server)

    def _check_active(self, server):

        if server.status != 'ACTIVE':
            server.get()

        # Some clouds append extra (STATUS) strings to the status
        short_server_status = server.status.split('(')[0]
        if short_server_status in nova_utils.deferred_server_statuses:
            return False
        elif server.status == 'ACTIVE':
            return True
        elif server.status == 'ERROR':
            exc = exception.Error(_('Creation of server %s failed.') %
                                  server.name)
            raise exc
        else:
            exc = exception.Error(_('Creation of server %(server)s failed '
                                    'with unknown status: %(status)s') %
                                  dict(server=server.name,
                                       status=server.status))
            raise exc

    @staticmethod
    def _build_block_device_mapping(bdm):
        if not bdm:
            return None
        bdm_dict = {}
        for mapping in bdm:
            mapping_parts = []
            if mapping.get('snapshot_id'):
                mapping_parts.append(mapping.get('snapshot_id'))
                mapping_parts.append('snap')
            else:
                mapping_parts.append(mapping.get('volume_id'))
                mapping_parts.append('')
            if (mapping.get('volume_size') or
                    mapping.get('delete_on_termination')):

                mapping_parts.append(mapping.get('volume_size', '0'))
            if mapping.get('delete_on_termination'):
                mapping_parts.append(str(mapping.get('delete_on_termination')))
            bdm_dict[mapping.get('device_name')] = ':'.join(mapping_parts)

        return bdm_dict

    def _build_nics(self, networks):
        if not networks:
            return None

        nics = []

        for net_data in networks:
            nic_info = {}
            if net_data.get('uuid'):
                nic_info['net-id'] = net_data['uuid']
            label_or_uuid = net_data.get('network')
            if label_or_uuid:
                if uuidutils.is_uuid_like(label_or_uuid):
                    nic_info['net-id'] = label_or_uuid
                else:
                    network = self.nova().networks.find(label=label_or_uuid)
                    nic_info['net-id'] = network.id
            if net_data.get('fixed_ip'):
                nic_info['v4-fixed-ip'] = net_data['fixed_ip']
            if net_data.get('port'):
                nic_info['port-id'] = net_data['port']
            nics.append(nic_info)
        return nics

    def _resolve_attribute(self, name):
        if name == 'first_address':
            return nova_utils.server_to_ipaddress(
                self.nova(), self.resource_id) or ''
        server = self.nova().servers.get(self.resource_id)
        if name == 'addresses':
            return server.addresses
        if name == 'networks':
            return server.networks
        if name == 'instance_name':
            return server._info.get('OS-EXT-SRV-ATTR:instance_name')
        if name == 'accessIPv4':
            return server.accessIPv4
        if name == 'accessIPv6':
            return server.accessIPv6
        if name == 'show':
            return server._info

    def handle_update(self, json_snippet, tmpl_diff, prop_diff):
        if 'Metadata' in tmpl_diff:
            self.metadata = tmpl_diff['Metadata']

        checkers = []
        server = None

        if 'metadata' in prop_diff:
            server = self.nova().servers.get(self.resource_id)
            nova_utils.meta_update(self.nova(),
                                   server,
                                   prop_diff['metadata'])

        if 'flavor' in prop_diff:

            flavor_update_policy = (
                prop_diff.get('flavor_update_policy') or
                self.properties.get('flavor_update_policy'))

            if flavor_update_policy == 'REPLACE':
                raise resource.UpdateReplace(self.name)

            flavor = prop_diff['flavor']
            flavor_id = nova_utils.get_flavor_id(self.nova(), flavor)
            if not server:
                server = self.nova().servers.get(self.resource_id)
            checker = scheduler.TaskRunner(nova_utils.resize, server, flavor,
                                           flavor_id)
            checkers.append(checker)

        if 'image' in prop_diff:
            image_update_policy = (
                prop_diff.get('image_update_policy') or
                self.properties.get('image_update_policy'))
            if image_update_policy == 'REPLACE':
                raise resource.UpdateReplace(self.name)
            image = prop_diff['image']
            image_id = nova_utils.get_image_id(self.nova(), image)
            if not server:
                server = self.nova().servers.get(self.resource_id)
            preserve_ephemeral = (
                image_update_policy == 'REBUILD_PRESERVE_EPHEMERAL')
            checker = scheduler.TaskRunner(
                nova_utils.rebuild, server, image_id,
                preserve_ephemeral=preserve_ephemeral)
            checkers.append(checker)

        # Optimization: make sure the first task is started before
        # check_update_complete.
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
            self.metadata = self.parsed_template('Metadata')

    def validate(self):
        '''
        Validate any of the provided params
        '''
        super(Server, self).validate()

        # check validity of key
        key_name = self.properties.get('key_name')
        if key_name:
            nova_utils.get_keypair(self.nova(), key_name)

        # either volume_id or snapshot_id needs to be specified, but not both
        # for block device mapping.
        bdm = self.properties.get('block_device_mapping') or []
        bootable_vol = False
        for mapping in bdm:
            if mapping['device_name'] == 'vda':
                    bootable_vol = True

            if mapping.get('volume_id') and mapping.get('snapshot_id'):
                raise exception.ResourcePropertyConflict('volume_id',
                                                         'snapshot_id')
            if not mapping.get('volume_id') and not mapping.get('snapshot_id'):
                msg = _('Either volume_id or snapshot_id must be specified for'
                        ' device mapping %s') % mapping['device_name']
                raise exception.StackValidationFailed(message=msg)

        # make sure the image exists if specified.
        image = self.properties.get('image')
        if image:
            nova_utils.get_image_id(self.nova(), image)
        elif not image and not bootable_vol:
            msg = _('Neither image nor bootable volume is specified for'
                    ' instance %s') % self.name
            raise exception.StackValidationFailed(message=msg)
        # network properties 'uuid' and 'network' shouldn't be used
        # both at once for all networks
        networks = self.properties.get('networks') or []
        for network in networks:
            if network.get('uuid') and network.get('network'):
                msg = _('Properties "uuid" and "network" are both set '
                        'to the network "%(network)s" for the server '
                        '"%(server)s". The "uuid" property is deprecated. '
                        'Use only "network" property.'
                        '') % dict(network=network['network'],
                                   server=self.name)
                raise exception.StackValidationFailed(message=msg)
            elif network.get('uuid'):
                logger.info(_('For the server "%(server)s" the "uuid" '
                              'property is set to network "%(network)s". '
                              '"uuid" property is deprecated. Use "network" '
                              'property instead.'
                              '') % dict(network=network['network'],
                                         server=self.name))

    def handle_delete(self):
        '''
        Delete a server, blocking until it is disposed by OpenStack
        '''
        if self.resource_id is None:
            return

        try:
            server = self.nova().servers.get(self.resource_id)
        except clients.novaclient.exceptions.NotFound:
            pass
        else:
            delete = scheduler.TaskRunner(nova_utils.delete_server, server)
            delete(wait_time=0.2)

        self.resource_id_set(None)

    def handle_suspend(self):
        '''
        Suspend a server - note we do not wait for the SUSPENDED state,
        this is polled for by check_suspend_complete in a similar way to the
        create logic so we can take advantage of coroutines
        '''
        if self.resource_id is None:
            raise exception.Error(_('Cannot suspend %s, resource_id not set') %
                                  self.name)

        try:
            server = self.nova().servers.get(self.resource_id)
        except clients.novaclient.exceptions.NotFound:
            raise exception.NotFound(_('Failed to find server %s') %
                                     self.resource_id)
        else:
            logger.debug(_('suspending server %s') % self.resource_id)
            # We want the server.suspend to happen after the volume
            # detachement has finished, so pass both tasks and the server
            suspend_runner = scheduler.TaskRunner(server.suspend)
            return server, suspend_runner

    def check_suspend_complete(self, cookie):
        server, suspend_runner = cookie

        if not suspend_runner.started():
            suspend_runner.start()

        if suspend_runner.done():
            if server.status == 'SUSPENDED':
                return True

            server.get()
            logger.debug(_('%(name)s check_suspend_complete status '
                         '= %(status)s') % {
                         'name': self.name, 'status': server.status})
            if server.status in list(nova_utils.deferred_server_statuses +
                                     ['ACTIVE']):
                return server.status == 'SUSPENDED'
            else:
                exc = exception.Error(_('Suspend of server %(server)s failed '
                                        'with unknown status: %(status)s') %
                                      dict(server=server.name,
                                           status=server.status))
                raise exc

    def handle_resume(self):
        '''
        Resume a server - note we do not wait for the ACTIVE state,
        this is polled for by check_resume_complete in a similar way to the
        create logic so we can take advantage of coroutines
        '''
        if self.resource_id is None:
            raise exception.Error(_('Cannot resume %s, resource_id not set') %
                                  self.name)

        try:
            server = self.nova().servers.get(self.resource_id)
        except clients.novaclient.exceptions.NotFound:
            raise exception.NotFound(_('Failed to find server %s') %
                                     self.resource_id)
        else:
            logger.debug(_('resuming server %s') % self.resource_id)
            server.resume()
            return server

    def check_resume_complete(self, server):
        return self._check_active(server)


def resource_mapping():
    return {
        'OS::Nova::Server': Server,
    }
