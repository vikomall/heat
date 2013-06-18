# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2010-2011 OpenStack, LLC
# All Rights Reserved.
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


import datetime
import webob

from heat.common import exception
from heat.common import wsgi
from heat.tests.common import HeatTestCase


class RequestTest(HeatTestCase):

    def test_content_type_missing(self):
        request = wsgi.Request.blank('/tests/123')
        self.assertRaises(exception.InvalidContentType,
                          request.get_content_type, ('application/xml'))

    def test_content_type_unsupported(self):
        request = wsgi.Request.blank('/tests/123')
        request.headers["Content-Type"] = "text/html"
        self.assertRaises(exception.InvalidContentType,
                          request.get_content_type, ('application/xml'))

    def test_content_type_with_charset(self):
        request = wsgi.Request.blank('/tests/123')
        request.headers["Content-Type"] = "application/json; charset=UTF-8"
        result = request.get_content_type(('application/json'))
        self.assertEqual(result, "application/json")

    def test_content_type_from_accept_xml(self):
        request = wsgi.Request.blank('/tests/123')
        request.headers["Accept"] = "application/xml"
        result = request.best_match_content_type()
        self.assertEqual(result, "application/json")

    def test_content_type_from_accept_json(self):
        request = wsgi.Request.blank('/tests/123')
        request.headers["Accept"] = "application/json"
        result = request.best_match_content_type()
        self.assertEqual(result, "application/json")

    def test_content_type_from_accept_xml_json(self):
        request = wsgi.Request.blank('/tests/123')
        request.headers["Accept"] = "application/xml, application/json"
        result = request.best_match_content_type()
        self.assertEqual(result, "application/json")

    def test_content_type_from_accept_json_xml_quality(self):
        request = wsgi.Request.blank('/tests/123')
        request.headers["Accept"] = ("application/json; q=0.3, "
                                     "application/xml; q=0.9")
        result = request.best_match_content_type()
        self.assertEqual(result, "application/json")

    def test_content_type_accept_default(self):
        request = wsgi.Request.blank('/tests/123.unsupported')
        request.headers["Accept"] = "application/unsupported1"
        result = request.best_match_content_type()
        self.assertEqual(result, "application/json")


class ResourceTest(HeatTestCase):

    def test_get_action_args(self):
        env = {
            'wsgiorg.routing_args': [
                None,
                {
                    'controller': None,
                    'format': None,
                    'action': 'update',
                    'id': 12,
                },
            ],
        }

        expected = {'action': 'update', 'id': 12}
        actual = wsgi.Resource(None, None, None).get_action_args(env)

        self.assertEqual(actual, expected)

    def test_get_action_args_invalid_index(self):
        env = {'wsgiorg.routing_args': []}
        expected = {}
        actual = wsgi.Resource(None, None, None).get_action_args(env)
        self.assertEqual(actual, expected)

    def test_get_action_args_del_controller_error(self):
        actions = {'format': None,
                   'action': 'update',
                   'id': 12}
        env = {'wsgiorg.routing_args': [None, actions]}
        expected = {'action': 'update', 'id': 12}
        actual = wsgi.Resource(None, None, None).get_action_args(env)
        self.assertEqual(actual, expected)

    def test_get_action_args_del_format_error(self):
        actions = {'action': 'update', 'id': 12}
        env = {'wsgiorg.routing_args': [None, actions]}
        expected = {'action': 'update', 'id': 12}
        actual = wsgi.Resource(None, None, None).get_action_args(env)
        self.assertEqual(actual, expected)

    def test_dispatch(self):
        class Controller(object):
            def index(self, shirt, pants=None):
                return (shirt, pants)

        resource = wsgi.Resource(None, None, None)
        actual = resource.dispatch(Controller(), 'index', 'on', pants='off')
        expected = ('on', 'off')
        self.assertEqual(actual, expected)

    def test_dispatch_default(self):
        class Controller(object):
            def default(self, shirt, pants=None):
                return (shirt, pants)

        resource = wsgi.Resource(None, None, None)
        actual = resource.dispatch(Controller(), 'index', 'on', pants='off')
        expected = ('on', 'off')
        self.assertEqual(actual, expected)

    def test_dispatch_no_default(self):
        class Controller(object):
            def show(self, shirt, pants=None):
                return (shirt, pants)

        resource = wsgi.Resource(None, None, None)
        self.assertRaises(AttributeError, resource.dispatch, Controller(),
                          'index', 'on', pants='off')

    def test_resource_call_error_handle(self):
        class Controller(object):
            def delete(self, req, identity):
                return (req, identity)

        actions = {'action': 'delete', 'id': 12, 'body': 'data'}
        env = {'wsgiorg.routing_args': [None, actions]}
        request = wsgi.Request.blank('/tests/123', environ=env)
        request.body = '{"foo" : "value"}'
        resource = wsgi.Resource(Controller(),
                                 wsgi.JSONRequestDeserializer(),
                                 None)
        self.assertRaises(webob.exc.HTTPBadRequest, resource, request)


class JSONResponseSerializerTest(HeatTestCase):

    def test_to_json(self):
        fixture = {"key": "value"}
        expected = '{"key": "value"}'
        actual = wsgi.JSONResponseSerializer().to_json(fixture)
        self.assertEqual(actual, expected)

    def test_to_json_with_date_format_value(self):
        fixture = {"date": datetime.datetime(1, 3, 8, 2)}
        expected = '{"date": "0001-03-08T02:00:00"}'
        actual = wsgi.JSONResponseSerializer().to_json(fixture)
        self.assertEqual(actual, expected)

    def test_to_json_with_more_deep_format(self):
        fixture = {"is_public": True, "name": [{"name1": "test"}]}
        expected = '{"is_public": true, "name": [{"name1": "test"}]}'
        actual = wsgi.JSONResponseSerializer().to_json(fixture)
        self.assertEqual(actual, expected)

    def test_default(self):
        fixture = {"key": "value"}
        response = webob.Response()
        wsgi.JSONResponseSerializer().default(response, fixture)
        self.assertEqual(response.status_int, 200)
        content_types = filter(lambda h: h[0] == 'Content-Type',
                               response.headerlist)
        self.assertEqual(len(content_types), 1)
        self.assertEqual(response.content_type, 'application/json')
        self.assertEqual(response.body, '{"key": "value"}')


class JSONRequestDeserializerTest(HeatTestCase):

    def test_has_body_no_content_length(self):
        request = wsgi.Request.blank('/')
        request.method = 'POST'
        request.body = 'asdf'
        request.headers.pop('Content-Length')
        self.assertFalse(wsgi.JSONRequestDeserializer().has_body(request))

    def test_has_body_zero_content_length(self):
        request = wsgi.Request.blank('/')
        request.method = 'POST'
        request.body = 'asdf'
        request.headers['Content-Length'] = 0
        self.assertFalse(wsgi.JSONRequestDeserializer().has_body(request))

    def test_has_body_has_content_length(self):
        request = wsgi.Request.blank('/')
        request.method = 'POST'
        request.body = 'asdf'
        self.assertTrue('Content-Length' in request.headers)
        self.assertTrue(wsgi.JSONRequestDeserializer().has_body(request))

    def test_no_body_no_content_length(self):
        request = wsgi.Request.blank('/')
        self.assertFalse(wsgi.JSONRequestDeserializer().has_body(request))

    def test_from_json(self):
        fixture = '{"key": "value"}'
        expected = {"key": "value"}
        actual = wsgi.JSONRequestDeserializer().from_json(fixture)
        self.assertEqual(actual, expected)

    def test_from_json_malformed(self):
        fixture = 'kjasdklfjsklajf'
        self.assertRaises(webob.exc.HTTPBadRequest,
                          wsgi.JSONRequestDeserializer().from_json, fixture)

    def test_default_no_body(self):
        request = wsgi.Request.blank('/')
        actual = wsgi.JSONRequestDeserializer().default(request)
        expected = {}
        self.assertEqual(actual, expected)

    def test_default_with_body(self):
        request = wsgi.Request.blank('/')
        request.method = 'POST'
        request.body = '{"key": "value"}'
        actual = wsgi.JSONRequestDeserializer().default(request)
        expected = {"body": {"key": "value"}}
        self.assertEqual(actual, expected)

    def test_has_body_has_transfer_encoding(self):
        request = wsgi.Request.blank('/')
        request.method = 'POST'
        request.body = 'fake_body'
        request.headers['transfer-encoding'] = 0
        self.assertTrue('transfer-encoding' in request.headers)
        self.assertTrue(wsgi.JSONRequestDeserializer().has_body(request))
