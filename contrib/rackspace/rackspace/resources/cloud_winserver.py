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

import copy
import os
import tempfile
import time

from oslo.config import cfg

from heat.common import exception
from heat.engine.resources import nova_utils
from heat.engine.resources import server
from heat.openstack.common import log as logging

import psexec  # noqa

logger = logging.getLogger(__name__)


class WinServer(server.Server):
    '''
    Rackspace cloud Windows server resource.
    '''
    PROPERTIES = (
        NAME, FLAVOR, IMAGE, USER_DATA
    ) = (
        'name', 'flavor', 'image', 'user_data'
    )

    attributes_schema = copy.deepcopy(server.Server.attributes_schema)
    attributes_schema.update(
        {
            'privateIPv4': _('The private IPv4 address of the server.'),
        }
    )

    def __init__(self, name, json_snippet, stack):
        super(WinServer, self).__init__(name, json_snippet, stack)
        self._public_ip = None
        self._server = None
        self._process = None
        self._last_time_stamp = None
        self._retry_count = 0
        self._max_retry_limit = 10
        self._timeout_start = None
        self._server_up = False
        self._ps_script = None
        self._tmp_batch_file = None

    @property
    def server(self):
        if not self._server:
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

    def handle_create(self):
        '''
        Create Rackspace Cloud Windows Server Instance.
        '''
        serverinstancename = self.properties[self.NAME]
        flavor = self.properties[self.FLAVOR]
        image = self.properties[self.IMAGE]
        flavor_id = nova_utils.get_flavor_id(self.nova(), flavor)
        image = nova_utils.get_image_id(self.nova(), image)

        # create Windows server instance
        logger.info("Creating Windows cloud server")
        data = 'netsh advfirewall firewall add rule name="Port 445"' \
            ' dir=in action=allow protocol=TCP localport=445'

        files = {"C:\\cloud-automation\\bootstrap.bat": data,
                 "C:\\cloud-automation\\bootstrap.cmd": data,
                 "C:\\rs-automation\\bootstrap.bat": data,
                 "C:\\rs-automation\\bootstrap.cmd": data}

        instance = self.nova().servers.create(
            serverinstancename,
            image=image,
            flavor=flavor_id,
            files=files)
        if instance is not None:
            self.resource_id_set(instance.id)

        return instance

    def check_create_complete(self, instance):
        '''
        Check if cloud Windows server instance creation is complete.
        '''
        if not self._is_time_to_get_status():
            return False

        if not self._is_server_active(instance):
            return False

        # server status is ACTIVE, but server may not be ready for network
        # connection, so wait until it is reachable or until timeout happens
        if not self._is_server_reachable():
            return False

        if self._process is None:
            self._start_installation_process(instance)
            return False

        self._throw_if_installation_timed_out()

        if self._process.is_alive():
            return False

        # installation completed, so do cleanup
        self._cleanup_script_files(self._ps_script, self._tmp_batch_file)

        if self._process.exit_code() != 0:
            logger.info("Installation exitcode %s" % self._process.exit_code())
            msg = "Install error:%s" % self._process.std_out()
            if cfg.CONF.debug:
                msg += "\n%s %s exitcode:%s" % (self.public_ip,
                                                instance.adminPass,
                                                self._process.exit_code())
            raise exception.Error(msg)

        logger.info("Server %s configuration completed." % self.resource_id)
        return True

    def _is_server_reachable(self):
        if not self._server_up:
            if not self._timeout_start:
                self._timeout_start = time.time()
            if psexec.wait_net_service(self.public_ip, 445, timeout=5):
                self._server_up = True
                return True

            time_diff = time.time() - self._timeout_start
            if not self._server_up and time_diff >= 1500:
                raise exception.Error("Server is not accessible... timedout!")

            return False

        return True

    def _is_server_active(self, instance):
        try:
            if instance.status != 'ACTIVE':
                instance.get()  # get updated attributes
        except Exception as ex:
            if self._retry_count < self._max_retry_limit:
                logger.info("Exception found in get status...going to retry.")
                self._retry_count += 1
                return False
            raise ex

        if instance.status == 'ERROR':
            raise exception.Error("Cloud server creation failed.")

        if instance.status != 'ACTIVE':
            return False

        return True

    def _throw_if_installation_timed_out(self):
        if time.time() - self._timeout_start > 3600:
            logger.info("Installation timed out")
            self._process.kill()
            self._cleanup_script_files(self._ps_script, self._tmp_batch_file)
            raise exception.Error("Resource instalation timed out")

    def _start_installation_process(self, instance):
        logger.info("Starting installation on windows server %s" %
                    instance.name)
        self._timeout_start = time.time()
        # create a powershellscript with given user_data
        self._ps_script = self._userdata_ps_script(
            self.properties[self.USER_DATA])

        # create a batch file that launches given powershell script
        self._tmp_batch_file = self._wrapper_batch_script(
            os.path.basename(self._ps_script))

        self._process = psexec.PsexecWrapper("Administrator",
                                             instance.adminPass,
                                             self.public_ip,
                                             self._ps_script,
                                             self._tmp_batch_file,
                                             "C:\\Windows")
        self._process.run_cmd()

    def _is_time_to_get_status(self):
        if self._last_time_stamp is None:
            self._last_time_stamp = time.time()
            return True

        # For now get status for every 30secs
        if time.time() - self._last_time_stamp > 30:
            self._last_time_stamp = time.time()
            return True

        return False

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
        # Stack creation timeout may result in stale batch files in tmp folder
        # so, remove old .bat and .ps1 files from tmp folder
        path = tempfile.gettempdir()
        time_now = time.time()
        for file in os.listdir(path):
            if not file.endswith(".bat") and not file.endswith(".ps1"):
                continue
            try:
                file = os.path.join(path, file)
                # remove files older than 7200sec (2hours)
                if time_now - os.stat(file).st_mtime > 7200:
                    os.remove(file)
            except Exception:
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

    def _resolve_attribute(self, name):
        if name == 'privateIPv4':
            return nova_utils.get_ip(self.server, 'private', 4)

        return super(WinServer, self)._resolve_attribute(name)

try:
    import pyrax  # noqa
except ImportError:
    def resource_mapping():
        return {}
else:
    def resource_mapping():
        return {'Rackspace::Cloud::WinServer': WinServer}
