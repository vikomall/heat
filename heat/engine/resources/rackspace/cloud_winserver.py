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

import signal
import subprocess
import os
import shlex
import socket
import time
import tempfile

from heat.common import exception
from heat.engine.resources.rackspace import rackspace_resource
from heat.openstack.common import log as logging

logger = logging.getLogger(__name__)

def alarm_handler(signum, frame):
    raise Alarm


def run_command(cmd, lines=None, timeout=None):
    p = subprocess.Popen(shlex.split(cmd),
                         close_fds=True,
                         stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT)

    signal.signal(signal.SIGALRM, alarm_handler)
    if timeout:
        signal.alarm(timeout)

    try:
        if lines:
            (stdout, stderr) = p.communicate(input=lines)

        status = p.wait()
        signal.alarm(0)
    except Alarm:
        logger.warning("Timeout running post-build process")
        status = 1
        stdout = ''
        p.kill()

    if lines:
        output = stdout

        # Remove this cruft from Windows build output
        output = output.replace('\x08', '')
        output = output.replace('\r', '')

    else:
        output = p.stdout.read().strip()

    return (status, output)

def get_wrapper_batch_file(command):
    batch_file_command = "powershell.exe -executionpolicy unrestricted " \
        "-command .\%s" % command
    batch_file = tempfile.NamedTemporaryFile(suffix=".bat", delete=False)
    batch_file.write(batch_file_command)
    batch_file.close()
    return batch_file.name
    
    
def psexec_run_script(username, password, address, filename,
                      command, path="C:\\Windows"):
    psexec = "%s/psexec.py" % os.path.dirname(__file__)
    psscript = "%s/download_wpi.ps1" % os.path.dirname(__file__)
    cmd_string = "nice python %s -path '%s' '%s':'%s'@'%s' " \
             "'c:\\windows\\sysnative\\cmd'"
    cmd = cmd_string % (psexec, path, username, password, address)

    # create a batch file that launches given powershell script
    wrapper_batch_file = get_wrapper_batch_file(command)
    lines = "put %s\nput %s\n%s\nexit\n" % (
        filename, wrapper_batch_file, os.path.basename(wrapper_batch_file))

    return run_command(cmd, lines=lines, timeout=1800)


class CloudWinServer(rackspace_resource.RackspaceResource):
    '''
    Rackspace cloud Windows server resource.
    '''
    properties_schema = {
        'name': {
            'Type': 'String',
            'Default': 'MyWindowsServer'
            },

        'flavor': {
            'Type': 'String',
            'Required': True            
            },
        
        'image': {
            'Type': 'String',
            'Default': 'Windows Server 2012 (with updates)'
            },
        
        'user_data': {
            'Type': 'String',
            'Default': 'None'
            }
    }

    attributes_schema = {'PrivateDnsName': ('Private DNS name of the specified'
                                            ' instance.'),
                         'PublicDnsName': ('Public DNS name of the specified '
                                           'instance.'),
                         'PrivateIp': ('Private IP address of the specified '
                                       'instance.'),
                         'PublicIp': ('Public IP address of the specified '
                                      'instance.')}

    def __init__(self, name, json_snippet, stack):
        super(CloudWinServer, self).__init__(name, json_snippet, stack)
        self._private_ip = None
        self._public_ip = None
        self._server = None

    @property
    def server(self):
        if not self._server:
            logger.debug("Calling nova().servers.get()")
            self._server = self.nova().servers.get(self.resource_id)
        return self._server

    def _get_ip(self, ip_type):
        if ip_type in self.server.addresses:
            for ip in self.server.addresses[ip_type]:
                if ip['version'] == 4:
                    return ip['addr']

        raise exception.Error("Could not determine the %s IP of %s." %
                              (ip_type, self.properties['image']))

    @property
    def public_ip(self):
        """Return the public IP of the Cloud Server."""
        if not self._public_ip:
            self._public_ip = self._get_ip('public')

        return self._public_ip

    @property
    def private_ip(self):
        """Return the private IP of the Cloud Server."""
        if not self._private_ip:
            self._private_ip = self._get_ip('private')
    
        return self._private_ip

    @property
    def images(self):
        """Get the images from the API."""
        logger.debug("Calling nova().images.list()")
        return [im.name for im in self.nova().images.list()]
    
    @property
    def flavors(self):
        """Get the flavors from the API."""
        logger.debug("Calling nova().flavors.list()")
        return [flavor.id for flavor in self.nova().flavors.list()]

    def handle_create(self):
        '''
        Create Rackspace Cloud Windows Server Instance.
        '''
        logger.debug("CloudWinServer instance handle_create called")
        serverinstancename = self.properties['name']
        flavor = self.properties['flavor']
        image = self.properties['image']

        # create Windows server instance
        logger.info("Creating Windows cloud server")
        data = 'netsh advfirewall firewall add rule name="Port 445"' \
            ' dir=in action=allow protocol=TCP localport=445'

        files = {"C:\\cloud-automation\\bootstrap.bat": data,
                 "C:\\cloud-automation\\bootstrap.cmd": data,
                 "C:\\rs-automation\\bootstrap.bat": data,
                 "C:\\rs-automation\\bootstrap.cmd": data}
        imageRef = [im for im in self.nova().images.list()                    
                    if im.name == image][0]
        instance = self.nova().servers.create(serverinstancename,
                                          imageRef,
                                          flavor,
                                          files = files)
        if instance is not None:
            self.resource_id_set(instance.id)

        return instance

    def check_create_complete(self, instance):
        '''
        Check if cloud Windows server instance creation is complete.
        '''
        instance.get()  # get updated attributes
        if instance.status == 'ERROR':
            instance.delete()
            raise exception.Error("CloudWinServer instance creation failed.")

        if instance.status != 'ACTIVE':
            return False

        logger.info("Cloud Windows server %s created (flavor:%s, image:%s)" %
                    (instance.name,
                     instance.flavor['id'],
                     instance.image['id']))
        
        adminPass = instance.adminPass
        user_data = self.properties['user_data']
        # create powershell script with user_data
        powershell_script = tempfile.NamedTemporaryFile(suffix=".ps1",
                                                        delete=False)
        
        powershell_script.write(user_data)
        ps_script_full_path = powershell_script.name
        powershell_script.close()

        # TODO: fix this sleep issue
        time.sleep(8*60)
        # Now connect to server using impacket and do the following
        # 1. copy powershell script to remote server
        # 2. execute the script
        # 3. close the connection (exit)
        (status, output) = psexec_run_script(
            'Administrator',
            adminPass,
            self.public_ip,
            ps_script_full_path,
            os.path.basename(ps_script_full_path))
        
        # remove the temp powershell script
        try:
            os.remove(ps_script_full_path)
        except:
            pass
        
        return True

    def handle_delete(self):
        '''
        Delete a Rackspace Cloud Windows Server Instance.
        '''
        logger.debug("CloudWinServer handle_delete called.")
        if self.resource_id is None:
            return

        instance = self.nova().servers.get(self.resource_id)
        instance.delete()
        self.resource_id = None

    def validate(self):
        '''
        Validate any of the provided params
        '''
        res = super(CloudWinServer, self).validate()
        if res:
            return res

        # check validity of given image
        if self.properties['image'] not in self.images:
            return {'Error': 'Image not found.'}
        
        # check validity of gvien flavor
        if self.properties['flavor'] not in self.flavors:
            return {'Error': "flavor not found."}

    def _resolve_attribute(self, name):
        if name == 'PrivateIp':
            return self.private_ip
        elif name == 'PublicIp':
            return self.public_ip
        else:
            return None


# pyrax module is required to work with Rackspace cloud database provider.
# If it is not installed, don't register clouddatabase provider
def resource_mapping():
    if rackspace_resource.PYRAX_INSTALLED:
        return {
            'Rackspace::Cloud::CloudWinServer': CloudWinServer,
        }
    else:
        return {}
