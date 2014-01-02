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

import mox

from testtools import skipIf

from heat.common import exception
from heat.common import template_format
from heat.engine import scheduler
from heat.engine.resources import instance
from heat.engine.resources import nova_utils
from heat.engine.resources import volume as vol
from heat.engine import clients
from heat.engine import resource
from heat.openstack.common.importutils import try_import
from heat.tests.common import HeatTestCase
from heat.tests.v1_1 import fakes
from heat.tests import utils

from cinderclient.v1 import client as cinderclient


volume_backups = try_import('cinderclient.v1.volume_backups')

volume_template = '''
{
  "AWSTemplateFormatVersion" : "2010-09-09",
  "Description" : "Volume Test",
  "Parameters" : {},
  "Resources" : {
    "WikiDatabase": {
      "Type": "AWS::EC2::Instance",
      "Properties": {
        "ImageId" : "foo",
        "InstanceType"   : "m1.large",
        "KeyName"        : "test",
        "UserData"       : "some data"
      }
    },
    "DataVolume" : {
      "Type" : "AWS::EC2::Volume",
      "Properties" : {
        "Size" : "1",
        "AvailabilityZone" : {"Fn::GetAtt": ["WikiDatabase",
                                             "AvailabilityZone"]},
        "Tags" : [{ "Key" : "Usage", "Value" : "Wiki Data Volume" }]
      }
    },
    "MountPoint" : {
      "Type" : "AWS::EC2::VolumeAttachment",
      "Properties" : {
        "InstanceId" : { "Ref" : "WikiDatabase" },
        "VolumeId"  : { "Ref" : "DataVolume" },
        "Device" : "/dev/vdc"
      }
    }
  }
}
'''


class VolumeTest(HeatTestCase):
    def setUp(self):
        super(VolumeTest, self).setUp()
        self.fc = fakes.FakeClient()
        self.cinder_fc = cinderclient.Client('username', 'password')
        self.m.StubOutWithMock(clients.OpenStackClients, 'cinder')
        self.m.StubOutWithMock(clients.OpenStackClients, 'nova')
        self.m.StubOutWithMock(self.cinder_fc.volumes, 'create')
        self.m.StubOutWithMock(self.cinder_fc.volumes, 'get')
        self.m.StubOutWithMock(self.cinder_fc.volumes, 'delete')
        self.m.StubOutWithMock(self.fc.volumes, 'create_server_volume')
        self.m.StubOutWithMock(self.fc.volumes, 'delete_server_volume')
        self.m.StubOutWithMock(nova_utils, 'get_image_id')
        utils.setup_dummy_db()

    def create_volume(self, t, stack, resource_name):
        data = t['Resources'][resource_name]
        data['Properties']['AvailabilityZone'] = 'nova'
        rsrc = vol.Volume(resource_name, data, stack)
        self.assertEqual(rsrc.validate(), None)
        scheduler.TaskRunner(rsrc.create)()
        self.assertEqual(rsrc.state, (rsrc.CREATE, rsrc.COMPLETE))
        return rsrc

    def create_attachment(self, t, stack, resource_name):
        rsrc = vol.VolumeAttachment(resource_name,
                                    t['Resources'][resource_name],
                                    stack)
        self.assertEqual(rsrc.validate(), None)
        scheduler.TaskRunner(rsrc.create)()
        self.assertEqual(rsrc.state, (rsrc.CREATE, rsrc.COMPLETE))
        return rsrc

    def _mock_create_volume(self, fv, stack_name):
        clients.OpenStackClients.cinder().MultipleTimes().AndReturn(
            self.cinder_fc)
        vol_name = utils.PhysName(stack_name, 'DataVolume')
        self.cinder_fc.volumes.create(
            size=1, availability_zone='nova',
            display_description=vol_name,
            display_name=vol_name,
            metadata={u'Usage': u'Wiki Data Volume'}).AndReturn(fv)

    def _stubout_delete_volume(self, fv):
        self.m.StubOutWithMock(fv, 'delete')
        fv.delete().AndReturn(True)
        self.m.StubOutWithMock(fv, 'get')
        fv.get().AndReturn(None)
        fv.get().AndRaise(
            clients.cinderclient.exceptions.NotFound('Not found'))
        self.m.ReplayAll()

    def _mock_create_server_volume_script(self, fva):
        clients.OpenStackClients.nova().MultipleTimes().AndReturn(self.fc)
        self.fc.volumes.create_server_volume(
            device=u'/dev/vdc', server_id=u'WikiDatabase',
            volume_id=u'vol-123').AndReturn(fva)
        self.cinder_fc.volumes.get('vol-123').AndReturn(fva)

    def test_volume(self):
        fv = FakeVolume('creating', 'available')
        stack_name = 'test_volume_stack'

        # create script
        self._mock_create_volume(fv, stack_name)

        # delete script
        self.cinder_fc.volumes.get('vol-123').AndReturn(fv)

        self.cinder_fc.volumes.get('vol-123').AndReturn(fv)
        self.m.ReplayAll()

        t = template_format.parse(volume_template)
        stack = utils.parse_stack(t, stack_name=stack_name)

        rsrc = self.create_volume(t, stack, 'DataVolume')
        self.assertEqual(fv.status, 'available')

        self.assertRaises(resource.UpdateReplace,
                          rsrc.handle_update, {}, {}, {})

        fv.status = 'in-use'
        self.assertRaises(exception.ResourceFailure,
                          scheduler.TaskRunner(rsrc.destroy))

        self._stubout_delete_volume(fv)
        fv.status = 'available'
        scheduler.TaskRunner(rsrc.destroy)()

        # Test when volume already deleted
        rsrc.state_set(rsrc.CREATE, rsrc.COMPLETE)
        scheduler.TaskRunner(rsrc.destroy)()

        self.m.VerifyAll()

    def test_volume_default_az(self):
        fv = FakeVolume('creating', 'available')
        stack_name = 'test_volume_stack'

        # create script
        self.m.StubOutWithMock(instance.Instance, 'handle_create')
        self.m.StubOutWithMock(instance.Instance, 'check_create_complete')
        self.m.StubOutWithMock(vol.VolumeAttachment, 'handle_create')
        self.m.StubOutWithMock(vol.VolumeAttachment, 'check_create_complete')
        instance.Instance.handle_create().AndReturn(None)
        instance.Instance.check_create_complete(None).AndReturn(True)
        clients.OpenStackClients.cinder().MultipleTimes().AndReturn(
            self.cinder_fc)
        vol_name = utils.PhysName(stack_name, 'DataVolume')
        self.cinder_fc.volumes.create(
            size=1, availability_zone=None,
            display_description=vol_name,
            display_name=vol_name,
            metadata={u'Usage': u'Wiki Data Volume'}).AndReturn(fv)
        vol.VolumeAttachment.handle_create().AndReturn(None)
        vol.VolumeAttachment.check_create_complete(None).AndReturn(True)

        # delete script
        self.m.StubOutWithMock(instance.Instance, 'handle_delete')
        self.m.StubOutWithMock(vol.VolumeAttachment, 'handle_delete')
        instance.Instance.handle_delete().AndReturn(None)
        self.cinder_fc.volumes.get('vol-123').AndRaise(
            clients.cinderclient.exceptions.NotFound('Not found'))
        vol.VolumeAttachment.handle_delete().AndReturn(None)
        self.m.ReplayAll()

        t = template_format.parse(volume_template)
        stack = utils.parse_stack(t, stack_name=stack_name)

        rsrc = stack['DataVolume']
        self.assertEqual(rsrc.validate(), None)
        scheduler.TaskRunner(stack.create)()
        self.assertEqual(rsrc.state, (rsrc.CREATE, rsrc.COMPLETE))

        scheduler.TaskRunner(stack.delete)()

        self.m.VerifyAll()

    def test_volume_create_error(self):
        fv = FakeVolume('creating', 'error')
        stack_name = 'test_volume_create_error_stack'

        self._mock_create_volume(fv, stack_name)

        self.m.ReplayAll()

        t = template_format.parse(volume_template)
        t['Resources']['DataVolume']['Properties']['AvailabilityZone'] = 'nova'
        stack = utils.parse_stack(t, stack_name=stack_name)

        rsrc = vol.Volume('DataVolume',
                          t['Resources']['DataVolume'],
                          stack)
        create = scheduler.TaskRunner(rsrc.create)
        self.assertRaises(exception.ResourceFailure, create)

        self.m.VerifyAll()

    def test_volume_bad_tags(self):
        t = template_format.parse(volume_template)
        t['Resources']['DataVolume']['Properties']['Tags'] = [{'Foo': 'bar'}]
        stack = utils.parse_stack(t, stack_name='test_volume_bad_tags_stack')

        rsrc = vol.Volume('DataVolume',
                          t['Resources']['DataVolume'],
                          stack)
        self.assertRaises(exception.StackValidationFailed, rsrc.validate)

        self.m.VerifyAll()

    def test_volume_attachment_error(self):
        fv = FakeVolume('creating', 'available')
        fva = FakeVolume('attaching', 'error')
        stack_name = 'test_volume_attach_error_stack'

        self._mock_create_volume(fv, stack_name)

        self._mock_create_server_volume_script(fva)

        self.m.ReplayAll()

        t = template_format.parse(volume_template)
        t['Resources']['DataVolume']['Properties']['AvailabilityZone'] = 'nova'
        stack = utils.parse_stack(t, stack_name=stack_name)

        scheduler.TaskRunner(stack['DataVolume'].create)()
        self.assertEqual(fv.status, 'available')
        rsrc = vol.VolumeAttachment('MountPoint',
                                    t['Resources']['MountPoint'],
                                    stack)
        create = scheduler.TaskRunner(rsrc.create)
        self.assertRaises(exception.ResourceFailure, create)

        self.m.VerifyAll()

    def test_volume_attachment(self):
        fv = FakeVolume('creating', 'available')
        fva = FakeVolume('attaching', 'in-use')
        stack_name = 'test_volume_attach_stack'

        self._mock_create_volume(fv, stack_name)

        self._mock_create_server_volume_script(fva)

        # delete script
        fva = FakeVolume('in-use', 'available')
        self.fc.volumes.delete_server_volume('WikiDatabase',
                                             'vol-123').AndReturn(None)
        self.cinder_fc.volumes.get('vol-123').AndReturn(fva)

        self.m.ReplayAll()

        t = template_format.parse(volume_template)
        t['Resources']['DataVolume']['Properties']['AvailabilityZone'] = 'nova'
        stack = utils.parse_stack(t, stack_name=stack_name)

        scheduler.TaskRunner(stack['DataVolume'].create)()
        self.assertEqual(fv.status, 'available')
        rsrc = self.create_attachment(t, stack, 'MountPoint')

        self.assertRaises(resource.UpdateReplace,
                          rsrc.handle_update, {}, {}, {})

        scheduler.TaskRunner(rsrc.delete)()

        self.m.VerifyAll()

    def test_volume_detachment_err(self):
        fv = FakeVolume('creating', 'available')
        fva = FakeVolume('in-use', 'available')
        stack_name = 'test_volume_detach_stack'

        self._mock_create_volume(fv, stack_name)

        self._mock_create_server_volume_script(fva)

        # delete script
        fva = FakeVolume('i-use', 'available')
        self.m.StubOutWithMock(fva, 'get')
        fva.get().MultipleTimes()
        fva.status = "in-use"

        self.cinder_fc.volumes.get('vol-123').AndReturn(fva)

        self.fc.volumes.delete_server_volume(
            'WikiDatabase', 'vol-123').AndRaise(
                clients.novaclient.exceptions.BadRequest('Already detached'))

        self.fc.volumes.delete_server_volume(
            'WikiDatabase', 'vol-123').AndRaise(
                clients.novaclient.exceptions.NotFound('Not found'))

        self.fc.volumes.delete_server_volume(
            'WikiDatabase', 'vol-123').AndRaise(
                clients.novaclient.exceptions.NotFound('Not found'))

        self.fc.volumes.delete_server_volume(
            'WikiDatabase', 'vol-123').AndRaise(
                clients.cinderclient.exceptions.NotFound('Not found'))

        self.m.ReplayAll()

        t = template_format.parse(volume_template)
        t['Resources']['DataVolume']['Properties']['AvailabilityZone'] = 'nova'
        stack = utils.parse_stack(t, stack_name=stack_name)

        scheduler.TaskRunner(stack['DataVolume'].create)()
        self.assertEqual(fv.status, 'available')
        rsrc = self.create_attachment(t, stack, 'MountPoint')

        self.assertRaises(resource.UpdateReplace,
                          rsrc.handle_update, {}, {}, {})

        scheduler.TaskRunner(rsrc.delete)()

        self.m.VerifyAll()

    def test_volume_detach_non_exist(self):
        fv = FakeVolume('creating', 'available')
        fva = FakeVolume('in-use', 'available')
        stack_name = 'test_volume_detach_stack'

        self._mock_create_volume(fv, stack_name)

        self._mock_create_server_volume_script(fva)

        # delete script
        self.cinder_fc.volumes.get('vol-123').AndRaise(
            clients.cinderclient.exceptions.NotFound('Not found'))

        self.m.ReplayAll()

        t = template_format.parse(volume_template)
        t['Resources']['DataVolume']['Properties']['AvailabilityZone'] = 'nova'
        stack = utils.parse_stack(t, stack_name=stack_name)

        scheduler.TaskRunner(stack['DataVolume'].create)()
        rsrc = self.create_attachment(t, stack, 'MountPoint')

        scheduler.TaskRunner(rsrc.delete)()

        self.m.VerifyAll()

    def test_volume_detach_with_latency(self):
        fv = FakeVolume('creating', 'available')
        fva = FakeVolume('attaching', 'in-use')
        stack_name = 'test_volume_attach_stack'

        self._mock_create_volume(fv, stack_name)

        self._mock_create_server_volume_script(fva)

        # delete script
        volume_detach_cycle = 'in-use', 'detaching', 'available'
        fva = FakeLatencyVolume(life_cycle=volume_detach_cycle)
        self.fc.volumes.delete_server_volume(
            'WikiDatabase', 'vol-123').MultipleTimes().AndReturn(None)
        self.cinder_fc.volumes.get('vol-123').AndReturn(fva)

        self.m.ReplayAll()

        t = template_format.parse(volume_template)
        t['Resources']['DataVolume']['Properties']['AvailabilityZone'] = 'nova'
        stack = utils.parse_stack(t, stack_name=stack_name)

        scheduler.TaskRunner(stack['DataVolume'].create)()
        self.assertEqual(fv.status, 'available')
        rsrc = self.create_attachment(t, stack, 'MountPoint')

        scheduler.TaskRunner(rsrc.delete)()

        self.m.VerifyAll()

    def test_volume_detach_with_error(self):
        fv = FakeVolume('creating', 'available')
        fva = FakeVolume('attaching', 'in-use')
        stack_name = 'test_volume_attach_stack'

        self._mock_create_volume(fv, stack_name)

        self._mock_create_server_volume_script(fva)

        # delete script
        fva = FakeVolume('in-use', 'error')
        self.fc.volumes.delete_server_volume('WikiDatabase',
                                             'vol-123').AndReturn(None)
        self.cinder_fc.volumes.get('vol-123').AndReturn(fva)

        self.m.ReplayAll()

        t = template_format.parse(volume_template)
        t['Resources']['DataVolume']['Properties']['AvailabilityZone'] = 'nova'
        stack = utils.parse_stack(t, stack_name=stack_name)

        scheduler.TaskRunner(stack['DataVolume'].create)()
        self.assertEqual(fv.status, 'available')
        rsrc = self.create_attachment(t, stack, 'MountPoint')
        detach_task = scheduler.TaskRunner(rsrc.delete)

        self.assertRaises(exception.ResourceFailure, detach_task)

        self.m.VerifyAll()

    def test_volume_delete(self):
        stack_name = 'test_volume_stack'
        fv = FakeVolume('creating', 'available')
        fb = FakeBackup('creating', 'available')

        self._mock_create_volume(fv, stack_name)
        self.m.ReplayAll()

        t = template_format.parse(volume_template)
        t['Resources']['DataVolume']['DeletionPolicy'] = 'Delete'
        stack = utils.parse_stack(t, stack_name=stack_name)

        rsrc = self.create_volume(t, stack, 'DataVolume')

        self.m.StubOutWithMock(rsrc, "handle_delete")
        rsrc.handle_delete().AndReturn(None)
        self.m.StubOutWithMock(rsrc, "check_delete_complete")
        rsrc.check_delete_complete(mox.IgnoreArg()).AndReturn(True)
        self.m.ReplayAll()
        scheduler.TaskRunner(rsrc.destroy)()

        self.m.VerifyAll()

    @skipIf(volume_backups is None, 'unable to import volume_backups')
    def test_snapshot(self):
        stack_name = 'test_volume_stack'
        fv = FakeVolume('creating', 'available')
        fb = FakeBackup('creating', 'available')

        self._mock_create_volume(fv, stack_name)

        # snapshot script
        self.m.StubOutWithMock(self.cinder_fc.backups, 'create')
        self.cinder_fc.backups.create('vol-123').AndReturn(fb)
        self.cinder_fc.volumes.get('vol-123').AndReturn(fv)

        self.m.ReplayAll()

        t = template_format.parse(volume_template)
        t['Resources']['DataVolume']['DeletionPolicy'] = 'Snapshot'
        stack = utils.parse_stack(t, stack_name=stack_name)

        rsrc = self.create_volume(t, stack, 'DataVolume')

        self._stubout_delete_volume(fv)
        scheduler.TaskRunner(rsrc.destroy)()

        self.m.VerifyAll()

    @skipIf(volume_backups is None, 'unable to import volume_backups')
    def test_snapshot_error(self):
        stack_name = 'test_volume_stack'
        fv = FakeVolume('creating', 'available')
        fb = FakeBackup('creating', 'error')

        self._mock_create_volume(fv, stack_name)

        # snapshot script
        self.cinder_fc.volumes.get('vol-123').AndReturn(fv)
        self.m.StubOutWithMock(self.cinder_fc.backups, 'create')
        self.cinder_fc.backups.create('vol-123').AndReturn(fb)
        self.m.ReplayAll()

        t = template_format.parse(volume_template)
        t['Resources']['DataVolume']['DeletionPolicy'] = 'Snapshot'
        stack = utils.parse_stack(t, stack_name=stack_name)

        rsrc = self.create_volume(t, stack, 'DataVolume')

        self.assertRaises(exception.ResourceFailure,
                          scheduler.TaskRunner(rsrc.destroy))

        self.m.VerifyAll()

    @skipIf(volume_backups is None, 'unable to import volume_backups')
    def test_snapshot_no_volume(self):
        stack_name = 'test_volume_stack'
        fv = FakeVolume('creating', 'error')

        self._mock_create_volume(fv, stack_name)

        self.cinder_fc.volumes.get('vol-123').AndReturn(fv)

        self.m.ReplayAll()

        t = template_format.parse(volume_template)
        t['Resources']['DataVolume']['DeletionPolicy'] = 'Snapshot'
        t['Resources']['DataVolume']['Properties']['AvailabilityZone'] = 'nova'
        stack = utils.parse_stack(t, stack_name=stack_name)
        rsrc = vol.Volume('DataVolume',
                          t['Resources']['DataVolume'],
                          stack)

        create = scheduler.TaskRunner(rsrc.create)
        self.assertRaises(exception.ResourceFailure, create)

        self._stubout_delete_volume(fv)
        scheduler.TaskRunner(rsrc.destroy)()

        self.m.VerifyAll()

    @skipIf(volume_backups is None, 'unable to import volume_backups')
    def test_create_from_snapshot(self):
        stack_name = 'test_volume_stack'
        fv = FakeVolumeWithStateTransition('restoring-backup', 'available')
        fvbr = FakeBackupRestore('vol-123')

        # create script
        clients.OpenStackClients.cinder().MultipleTimes().AndReturn(
            self.cinder_fc)
        self.m.StubOutWithMock(self.cinder_fc.restores, 'restore')
        self.cinder_fc.restores.restore('backup-123').AndReturn(fvbr)
        self.cinder_fc.volumes.get('vol-123').AndReturn(fv)
        self.m.StubOutWithMock(fv, 'update')
        vol_name = utils.PhysName(stack_name, 'DataVolume')
        fv.update(
            display_description=vol_name,
            display_name=vol_name)

        self.m.ReplayAll()

        t = template_format.parse(volume_template)
        t['Resources']['DataVolume']['Properties']['SnapshotId'] = 'backup-123'
        stack = utils.parse_stack(t, stack_name=stack_name)

        self.create_volume(t, stack, 'DataVolume')
        self.assertEqual(fv.status, 'available')

        self.m.VerifyAll()

    @skipIf(volume_backups is None, 'unable to import volume_backups')
    def test_create_from_snapshot_error(self):
        stack_name = 'test_volume_stack'
        fv = FakeVolumeWithStateTransition('restoring-backup', 'error')
        fvbr = FakeBackupRestore('vol-123')

        # create script
        clients.OpenStackClients.cinder().MultipleTimes().AndReturn(
            self.cinder_fc)
        self.m.StubOutWithMock(self.cinder_fc.restores, 'restore')
        self.cinder_fc.restores.restore('backup-123').AndReturn(fvbr)
        self.cinder_fc.volumes.get('vol-123').AndReturn(fv)
        self.m.StubOutWithMock(fv, 'update')
        vol_name = utils.PhysName(stack_name, 'DataVolume')
        fv.update(
            display_description=vol_name,
            display_name=vol_name)

        self.m.ReplayAll()

        t = template_format.parse(volume_template)
        t['Resources']['DataVolume']['Properties']['SnapshotId'] = 'backup-123'
        t['Resources']['DataVolume']['Properties']['AvailabilityZone'] = 'nova'
        stack = utils.parse_stack(t, stack_name=stack_name)

        rsrc = vol.Volume('DataVolume',
                          t['Resources']['DataVolume'],
                          stack)
        create = scheduler.TaskRunner(rsrc.create)
        self.assertRaises(exception.ResourceFailure, create)

        self.m.VerifyAll()

    def test_cinder_create(self):
        fv = FakeVolume('creating', 'available')
        stack_name = 'test_volume_stack'

        clients.OpenStackClients.cinder().MultipleTimes().AndReturn(
            self.cinder_fc)
        self.cinder_fc.volumes.create(
            size=1, availability_zone='nova',
            display_description='CustomDescription',
            display_name='CustomName',
            imageRef='46988116-6703-4623-9dbc-2bc6d284021b',
            snapshot_id='snap-123',
            metadata={'key': 'value'},
            source_volid='vol-012',
            volume_type='lvm').AndReturn(fv)

        self.m.ReplayAll()

        t = template_format.parse(volume_template)
        t['Resources']['DataVolume']['Properties'] = {
            'size': '1',
            'availability_zone': 'nova',
            'name': 'CustomName',
            'description': 'CustomDescription',
            'volume_type': 'lvm',
            'metadata': {'key': 'value'},
            # Note that specifying all these arguments doesn't work in
            # practice, as they are conflicting, but we just want to check they
            # are sent to the backend.
            'imageRef': '46988116-6703-4623-9dbc-2bc6d284021b',
            'snapshot_id': 'snap-123',
            'source_volid': 'vol-012',
        }
        stack = utils.parse_stack(t, stack_name=stack_name)

        rsrc = vol.CinderVolume('DataVolume',
                                t['Resources']['DataVolume'],
                                stack)
        self.assertEqual(rsrc.validate(), None)
        scheduler.TaskRunner(rsrc.create)()
        self.assertEqual(rsrc.state, (rsrc.CREATE, rsrc.COMPLETE))
        self.assertEqual(fv.status, 'available')

        self.m.VerifyAll()

    def test_cinder_create_from_image(self):
        fv = FakeVolumeWithStateTransition('downloading', 'available')
        stack_name = 'test_volume_stack'

        clients.OpenStackClients.cinder().MultipleTimes().AndReturn(
            self.cinder_fc)
        clients.OpenStackClients.nova('compute').AndReturn(self.fc)
        nova_utils.get_image_id(
            self.fc, '46988116-6703-4623-9dbc-2bc6d284021b').AndReturn(
                '46988116-6703-4623-9dbc-2bc6d284021b')
        self.cinder_fc.volumes.create(
            size=1, availability_zone='nova',
            display_description='ImageVolumeDescription',
            display_name='ImageVolume',
            imageRef='46988116-6703-4623-9dbc-2bc6d284021b').AndReturn(fv)

        self.m.ReplayAll()

        t = template_format.parse(volume_template)
        t['Resources']['DataVolume']['Properties'] = {
            'size': '1',
            'name': 'ImageVolume',
            'description': 'ImageVolumeDescription',
            'availability_zone': 'nova',
            'image': '46988116-6703-4623-9dbc-2bc6d284021b',
        }
        stack = utils.parse_stack(t, stack_name=stack_name)

        rsrc = vol.CinderVolume('DataVolume',
                                t['Resources']['DataVolume'],
                                stack)
        self.assertEqual(rsrc.validate(), None)
        scheduler.TaskRunner(rsrc.create)()
        self.assertEqual(rsrc.state, (rsrc.CREATE, rsrc.COMPLETE))
        self.assertEqual(fv.status, 'available')

        self.m.VerifyAll()

    def test_cinder_default(self):
        fv = FakeVolume('creating', 'available')
        stack_name = 'test_volume_stack'

        clients.OpenStackClients.cinder().MultipleTimes().AndReturn(
            self.cinder_fc)
        vol_name = utils.PhysName(stack_name, 'DataVolume')
        self.cinder_fc.volumes.create(
            size=1, availability_zone='nova',
            display_description=None,
            display_name=vol_name).AndReturn(fv)

        self.m.ReplayAll()

        t = template_format.parse(volume_template)
        t['Resources']['DataVolume']['Properties'] = {
            'size': '1',
            'availability_zone': 'nova',
        }
        stack = utils.parse_stack(t, stack_name=stack_name)

        rsrc = vol.CinderVolume('DataVolume',
                                t['Resources']['DataVolume'],
                                stack)
        self.assertEqual(rsrc.validate(), None)
        scheduler.TaskRunner(rsrc.create)()
        self.assertEqual(rsrc.state, (rsrc.CREATE, rsrc.COMPLETE))
        self.assertEqual(fv.status, 'available')

        self.m.VerifyAll()

    def test_cinder_fn_getatt(self):
        fv = FakeVolume('creating', 'available', availability_zone='zone1',
                        size=1, snapshot_id='snap-123', display_name='name',
                        display_description='desc', volume_type='lvm',
                        metadata={'key': 'value'}, source_volid=None,
                        status='available', bootable=False,
                        created_at='2013-02-25T02:40:21.000000')
        stack_name = 'test_volume_stack'

        clients.OpenStackClients.cinder().MultipleTimes().AndReturn(
            self.cinder_fc)
        vol_name = utils.PhysName(stack_name, 'DataVolume')
        self.cinder_fc.volumes.create(
            size=1, availability_zone='nova',
            display_description=None,
            display_name=vol_name).AndReturn(fv)

        self.cinder_fc.volumes.get('vol-123').MultipleTimes().AndReturn(fv)

        self.m.ReplayAll()

        t = template_format.parse(volume_template)
        t['Resources']['DataVolume']['Properties'] = {
            'size': '1',
            'availability_zone': 'nova',
        }
        stack = utils.parse_stack(t, stack_name=stack_name)

        rsrc = vol.CinderVolume('DataVolume',
                                t['Resources']['DataVolume'],
                                stack)
        scheduler.TaskRunner(rsrc.create)()
        self.assertEqual(u'zone1', rsrc.FnGetAtt('availability_zone'))
        self.assertEqual(u'1', rsrc.FnGetAtt('size'))
        self.assertEqual(u'snap-123', rsrc.FnGetAtt('snapshot_id'))
        self.assertEqual(u'name', rsrc.FnGetAtt('display_name'))
        self.assertEqual(u'desc', rsrc.FnGetAtt('display_description'))
        self.assertEqual(u'lvm', rsrc.FnGetAtt('volume_type'))
        self.assertEqual(json.dumps({'key': 'value'}),
                         rsrc.FnGetAtt('metadata'))
        self.assertEqual(u'None', rsrc.FnGetAtt('source_volid'))
        self.assertEqual(u'available', rsrc.FnGetAtt('status'))
        self.assertEqual(u'2013-02-25T02:40:21.000000',
                         rsrc.FnGetAtt('created_at'))
        self.assertEqual(u'False', rsrc.FnGetAtt('bootable'))
        error = self.assertRaises(exception.InvalidTemplateAttribute,
                                  rsrc.FnGetAtt, 'unknown')
        self.assertEqual(
            'The Referenced Attribute (DataVolume unknown) is incorrect.',
            str(error))

        self.m.VerifyAll()

    def test_cinder_attachment(self):
        fv = FakeVolume('creating', 'available')
        fva = FakeVolume('attaching', 'in-use')
        stack_name = 'test_volume_attach_stack'

        self._mock_create_volume(fv, stack_name)

        self._mock_create_server_volume_script(fva)

        # delete script
        fva = FakeVolume('in-use', 'available')
        self.fc.volumes.delete_server_volume('WikiDatabase',
                                             'vol-123').AndReturn(None)
        self.cinder_fc.volumes.get('vol-123').AndReturn(fva)

        self.m.ReplayAll()

        t = template_format.parse(volume_template)
        t['Resources']['DataVolume']['Properties']['AvailabilityZone'] = 'nova'
        t['Resources']['MountPoint']['Properties'] = {
            'instance_uuid': {'Ref': 'WikiDatabase'},
            'volume_id': {'Ref': 'DataVolume'},
            'mountpoint': '/dev/vdc'
        }
        stack = utils.parse_stack(t, stack_name=stack_name)

        scheduler.TaskRunner(stack['DataVolume'].create)()
        self.assertEqual(fv.status, 'available')
        rsrc = vol.CinderVolumeAttachment('MountPoint',
                                          t['Resources']['MountPoint'],
                                          stack)
        self.assertEqual(rsrc.validate(), None)
        scheduler.TaskRunner(rsrc.create)()
        self.assertEqual(rsrc.state, (rsrc.CREATE, rsrc.COMPLETE))

        self.assertRaises(resource.UpdateReplace, rsrc.handle_update,
                          {}, {}, {})

        scheduler.TaskRunner(rsrc.delete)()

        self.m.VerifyAll()


class FakeVolume(object):
    status = 'attaching'
    id = 'vol-123'

    def __init__(self, initial_status, final_status, **attrs):
        self.status = initial_status
        self.final_status = final_status
        for key, value in attrs.iteritems():
            setattr(self, key, value)

    def get(self):
        self.status = self.final_status

    def update(self, **kw):
        pass

    def delete(self):
        pass


class FakeLatencyVolume(object):
    status = 'attaching'
    id = 'vol-123'

    def __init__(self, life_cycle=('creating', 'available'), **attrs):
        if not isinstance(life_cycle, tuple):
            raise exception.Error('life_cycle need to be a tuple.')
        if not len(life_cycle):
            raise exception.Error('life_cycle should not be an empty tuple.')
        self.life_cycle = iter(life_cycle)
        self.status = next(self.life_cycle)
        for key, value in attrs.iteritems():
            setattr(self, key, value)

    def get(self):
        self.status = next(self.life_cycle)

    def update(self, **kw):
        pass


class FakeBackup(FakeVolume):
    status = 'creating'
    id = 'backup-123'


class FakeBackupRestore(object):
    volume_id = 'vol-123'

    def __init__(self, volume_id):
        self.volume_id = volume_id


class FakeVolumeWithStateTransition(FakeVolume):
    status = 'restoring-backup'
    get_call_count = 0

    def get(self):
        # Allow get to be called once without changing the status
        # This is to allow the check_create_complete method to
        # check the inital status.
        if self.get_call_count < 1:
            self.get_call_count += 1
        else:
            self.status = self.final_status
