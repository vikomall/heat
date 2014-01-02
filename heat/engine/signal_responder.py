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

from keystoneclient.contrib.ec2 import utils as ec2_utils

from heat.db import api as db_api
from heat.common import exception
from heat.engine import clients
from heat.engine import resource

from heat.openstack.common import log as logging
from heat.openstack.common.gettextutils import _
from heat.openstack.common.py3kcompat import urlutils


logger = logging.getLogger(__name__)

SIGNAL_TYPES = (
    WAITCONDITION, SIGNAL
) = (
    '/waitcondition', '/signal'
)
SIGNAL_VERB = {WAITCONDITION: 'PUT',
               SIGNAL: 'POST'}


class SignalResponder(resource.Resource):

    # Anything which subclasses this may trigger authenticated
    # API operations as a consequence of handling a signal
    requires_deferred_auth = True

    def handle_create(self):
        # Create a keystone user so we can create a signed URL via FnGetRefId
        user_id = self.keystone().create_stack_user(
            self.physical_resource_name())
        self.resource_id_set(user_id)

        kp = self.keystone().get_ec2_keypair(user_id)
        if not kp:
            raise exception.Error(_("Error creating ec2 keypair for user %s") %
                                  user_id)
        else:
            db_api.resource_data_set(self, 'access_key', kp.access,
                                     redact=True)
            db_api.resource_data_set(self, 'secret_key', kp.secret,
                                     redact=True)

    def handle_delete(self):
        if self.resource_id is None:
            return
        try:
            self.keystone().delete_stack_user(self.resource_id)
        except clients.hkc.kc.exceptions.NotFound:
            pass
        for data_key in ('ec2_signed_url', 'access_key', 'secret_key'):
            try:
                db_api.resource_data_delete(self, data_key)
            except exception.NotFound:
                pass

    def _get_signed_url(self, signal_type=SIGNAL):
        """Create properly formatted and pre-signed URL.

        This uses the created user for the credentials.

        See boto/auth.py::QuerySignatureV2AuthHandler

        :param signal_type: either WAITCONDITION or SIGNAL.
        """
        try:
            stored = db_api.resource_data_get(self, 'ec2_signed_url')
        except exception.NotFound:
            stored = None
        if stored is not None:
            return stored

        try:
            access_key = db_api.resource_data_get(self, 'access_key')
            secret_key = db_api.resource_data_get(self, 'secret_key')
        except exception.NotFound:
            logger.warning(_('Cannot generate signed url, '
                             'no stored access/secret key'))
            return

        waitcond_url = cfg.CONF.heat_waitcondition_server_url
        signal_url = waitcond_url.replace('/waitcondition', signal_type)
        host_url = urlutils.urlparse(signal_url)

        path = self.identifier().arn_url_path()

        # Note the WSGI spec apparently means that the webob request we end up
        # prcessing in the CFN API (ec2token.py) has an unquoted path, so we
        # need to calculate the signature with the path component unquoted, but
        # ensure the actual URL contains the quoted version...
        unquoted_path = urlutils.unquote(host_url.path + path)
        request = {'host': host_url.netloc.lower(),
                   'verb': SIGNAL_VERB[signal_type],
                   'path': unquoted_path,
                   'params': {'SignatureMethod': 'HmacSHA256',
                              'SignatureVersion': '2',
                              'AWSAccessKeyId': access_key,
                              'Timestamp':
                              self.created_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                              }}
        # Sign the request
        signer = ec2_utils.Ec2Signer(secret_key)
        request['params']['Signature'] = signer.generate(request)

        qs = urlutils.urlencode(request['params'])
        url = "%s%s?%s" % (signal_url.lower(),
                           path, qs)

        db_api.resource_data_set(self, 'ec2_signed_url', url)
        return url
