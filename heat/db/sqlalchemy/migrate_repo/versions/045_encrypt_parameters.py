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


from migrate.versioning import util as migrate_util
from sqlalchemy.orm import sessionmaker

from heat.common import crypt
from heat.db.sqlalchemy import models
from heat.engine.template import Template
from heat.openstack.common.gettextutils import _
from heat.openstack.common import strutils


def upgrade(migrate_engine):
    def encrypt_parameters(params, hidden_params):
        parameters = params['parameters']
        for hidden_param in hidden_params:
            # parameter was not encrypted
            if parameters.has_key(hidden_param) and not isinstance(
                    parameters[hidden_param], list):
                msg = _("Parameter %s was not encrypted.") % hidden_param
                migrate_util.log.warning(msg)
                encoded_val = strutils.safe_encode(parameters[hidden_param])
                parameters[hidden_param] = crypt.encrypt(encoded_val)
                continue

        return parameters

    Session = sessionmaker(bind=migrate_engine)
    session = Session()
    stacks = session.query(models.Stack).all()

    for stack in stacks:
        template = Template.load(None,
                                 stack.raw_template_id,
                                 stack.raw_template)
        hidden_params = [key for key, val in
                         template.param_schemata().iteritems() if val.hidden]
        params = stack.parameters
        params['parameters'] = encrypt_parameters(params, hidden_params)
        session.commit()


def downgrade(migrate_engine):
    migrate_util.log.warning(_('This version cannot be downgraded because '
                               'decryption stores data in plain text.'))
