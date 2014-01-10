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

import os
import shlex
import socket
import subprocess
import tempfile
import time

import novaclient.exceptions as novaexception

from heat.common import exception
from heat.engine.resources import nova_utils
from heat.engine import properties
from heat.engine import resource
from heat.openstack.common import log as logging

logger = logging.getLogger(__name__)


def wait_net_service(server, port, timeout=None):
    """Wait for network service to appear
        @param timeout: in seconds, if None or 0 wait forever
        @return: True of False, if timeout is None may return only True or
                 throw unhandled network exception
    """

    s = socket.socket()
    if timeout:
        from time import time as now
        # time module is needed to calc timeout shared between two exceptions
        end = now() + timeout

    while True:
        try:
            if timeout:
                next_timeout = end - now()
                if next_timeout < 0:
                    return False
                else:
                    s.settimeout(next_timeout)

            s.connect((server, port))

        except:
            # Handle refused connections, etc.
            if timeout:
                next_timeout = end - now()
                if next_timeout < 0:
                    return False
                else:
                    s.settimeout(next_timeout)

            time.sleep(1)

        else:
            s.close()
            return True


class PsexecWrapper(object):
    def __init__(self, username, password, address, filename,
                 wrapper_batch_file, path="C:\\Windows"):
        psexec = "%s/psexec.py" % os.path.dirname(__file__)
        cmd_string = "nice python %s -path '%s' '%s':'%s'@'%s' " \
            "'c:\\windows\\sysnative\\cmd'"
        self._cmd = cmd_string % (psexec, path, username, password, address)
        self._lines = "put %s\nput %s\n%s\nexit\n" % (
            filename, wrapper_batch_file, os.path.basename(wrapper_batch_file))
        self._psexec = None

    def run_cmd(self):
        self._psexec = subprocess.Popen(shlex.split(self._cmd),
                                        close_fds=True,
                                        stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT)

        self._psexec.stdin.write(self._lines)

    def is_alive(self):
        return self._psexec is not None and self._psexec.poll() is None

    def exit_code(self):
        self._raise_if_proc_running()
        return self._psexec.returncode

    def std_out(self):
        self._raise_if_proc_running()
        return self._psexec.stdout.readlines()

    def std_err(self):
        self._raise_if_proc_running()
        return self._psexec.stderr.readlines()

    def kill(self):
        if not self._psexec and self._psexec.poll() is None:
            self._psexec.kill()

    def _raise_if_proc_running(self):
        if self._psexec.poll() is None:
            raise exception.Error("process is still running")


class WinServer(resource.Resource):
    '''
    Rackspace cloud Windows server resource.
    '''
    PROPERTIES = (
        NAME, FLAVOR, IMAGE, USER_DATA
    ) = (
        'name', 'flavor', 'image', 'user_data'
    )

    properties_schema = {
        FLAVOR: properties.Schema(
            properties.Schema.STRING,
            required=True,
            update_allowed=True
        ),
        IMAGE: properties.Schema(
            properties.Schema.STRING,
            required=True
        ),
        USER_DATA: properties.Schema(
            properties.Schema.STRING
        ),
        NAME: properties.Schema(
            properties.Schema.STRING
        ),
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
        super(WinServer, self).__init__(name, json_snippet, stack)
        self._private_ip = None
        self._public_ip = None
        self._server = None
        self._process = None
        self._last_time_stamp = None
        self._retry_count = 0
        self._max_retry_limit = 10
        self._timeout_start = None
        self._server_up = False
        self._wait_for_server_retry = 0
        self._ps_script = None
        self._tmp_batch_file = None

    def _exithandler(self, singnum, frame):
        try:
            if self._process is not None and self._process.is_alive():
                self._process.terminate()
        except:
            pass

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
                              (ip_type, self.properties[self.IMAGE]))

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

    #@property
    #def flavors(self):
        #"""Get the flavors from the API."""
        #logger.debug("Calling nova().flavors.list()")
        #return [flavor.id for flavor in self.nova().flavors.list()]

    def handle_create(self):
        '''
        Create Rackspace Cloud Windows Server Instance.
        '''
        logger.debug("WinServer instance handle_create called")
        serverinstancename = self.properties[self.NAME]
        flavor = self.properties[self.FLAVOR]
        image = self.properties[self.IMAGE]
        flavor_id = nova_utils.get_flavor_id(self.nova(), flavor)

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
        instance = self.nova().servers.create(
            serverinstancename,
            imageRef,
            flavor_id,
            files=files)
        if instance is not None:
            self.resource_id_set(instance.id)

        return instance

    def _is_time_to_get_status(self):
        if self._last_time_stamp is None:
            self._last_time_stamp = time.time()
            return True

        # For now get status for every 30secs
        if time.time() - self._last_time_stamp > 30:
            self._last_time_stamp = time.time()
            return True

        return False

    def check_create_complete(self, instance):
        '''
        Check if cloud Windows server instance creation is complete.
        '''
        if not self._is_time_to_get_status():
            return False

        try:
            instance.get()  # get updated attributes
        except Exception as ex:
            if self._retry_count < self._max_retry_limit:
                logger.info("Exception found in get status...going to retry.")
                logger.info("Exception:%s res_id:%s" % (ex, self.resource_id))
                self._retry_count += 1
                return False
            raise ex

        if instance.status == 'ERROR':
            if self._retry_count < self._max_retry_limit:
                logger.info("Cloud server returned ERROR...going to retry.")
                self._retry_count += 1
                return False

            msg = "Retried %s times." % self._retry_count
            instance.delete()
            raise exception.Error("Cloud server creation failed.%s" % msg)

        if instance.status != 'ACTIVE':
            return False

        if not self._server_up:
            if not self._timeout_start:
                self._timeout_start = time.time()
            if wait_net_service(self.public_ip, 445, timeout=5):
                self._server_up = True

        if not self._server_up:
            if time.time() - self._timeout_start >= 1500:
                raise exception.Error("Server is not active... timedout!")

        if self._process is None:
            logger.info("Windows server %s created (flavor:%s, image:%s)" %
                        (instance.name,
                         instance.flavor['id'],
                         instance.image['id']))
            self._timeout_start = time.time()
            logger.info("Spawning a process to begin installation steps.")
            # create a powershellscript with given user_data
            self._ps_script = self._userdata_ps_script(
                self.properties[self.USER_DATA])
            # create a batch file that launches given powershell script
            self._tmp_batch_file = self._wrapper_batch_script(
                os.path.basename(self._ps_script))

            publicip = self.public_ip
            adminPass = instance.adminPass
            self._process = PsexecWrapper("Administrator", adminPass,
                                          self.public_ip, self._ps_script,
                                          self._tmp_batch_file, "C:\\Windows")
            self._process.run_cmd()
            return False

        if time.time() - self._timeout_start > 3600:
            logger.info("Installation timed out")
            self._process.kill()
            self._cleanup_script_files(self._ps_script, self._tmp_batch_file)
            raise exception.Error("Resource instalation timed out")

        if self._process.is_alive():
            return False

        self._cleanup_script_files(self._ps_script, self._tmp_batch_file)

        if self._process.exit_code() != 0:
            logger.info("Installation exitcode %s" % self._process.exit_code())
            raise exception.Error("Install error:%s" % self._process.std_out())

        logger.info("Server resource %s configuration done" % self.resource_id)
        return True

    def _cleanup_script_files(self, ps_script, tmp_batch_file):
        # remove the temp powershell and batch script
        try:
            os.remove(ps_script)
        except:
            pass
        try:
            os.remove(tmp_batch_file)
        except:
            pass

    def _userdata_ps_script(self, user_data):
        # create powershell script with user_data
        powershell_script = tempfile.NamedTemporaryFile(suffix=".ps1",
                                                        delete=False)

        powershell_script.write(user_data)
        ps_script_full_path = powershell_script.name
        powershell_script.close()
        return ps_script_full_path

    def _wrapper_batch_script(self, command):
        batch_file_command = "powershell.exe -executionpolicy unrestricted " \
            "-command .\%s" % command
        batch_file = tempfile.NamedTemporaryFile(suffix=".bat", delete=False)
        batch_file.write(batch_file_command)
        batch_file.close()
        return batch_file.name

    def handle_delete(self):
        '''
        Delete a Rackspace Cloud Windows Server Instance.
        '''
        logger.debug("WinServer handle_delete called.")
        if self.resource_id is None:
            return

        try:
            instance = self.nova().servers.get(self.resource_id)
            instance.delete()
        except novaexception.NotFound:
            pass

        self.resource_id = None

    def validate(self):
        '''
        Validate any of the provided params
        '''
        res = super(WinServer, self).validate()
        if res:
            return res

        # check validity of given image
        if self.properties[self.IMAGE] not in self.images:
            return {'Error': 'Image not found.'}
        ## check validity of gvien flavor
        #if self.properties['flavor'] not in self.flavors:
            #return {'Error': "flavor not found."}

    def _resolve_attribute(self, name):
        if name == 'PrivateIp':
            return self.private_ip
        elif name == 'PublicIp':
            return self.public_ip
        else:
            return None

try:
    import pyrax  # noqa
except ImportError:
    def resource_mapping():
        return {}
else:
    def resource_mapping():
        return {'Rackspace::Cloud::WinServer': WinServer}
