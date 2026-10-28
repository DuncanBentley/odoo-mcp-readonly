# -*- coding: utf-8 -*-
import json
from datetime import timedelta

from odoo import fields
from odoo.tests import HttpCase, tagged


@tagged('post_install', '-at_install')
class TestMcpReadonly(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        group_field = 'group_ids' if 'group_ids' in env['res.users']._fields else 'groups_id'
        cls.ai_user = env['res.users'].create({
            'name': 'AI Reader', 'login': 'ai_reader_test',
            group_field: [(6, 0, [env.ref('base.group_user').id, env.ref('base.group_partner_manager').id])],
        })
        Keys = env['res.users.apikeys'].with_user(cls.ai_user)
        try:
            cls.key = Keys._generate('rpc', 'mcp readonly test', fields.Datetime.now() + timedelta(days=30))
        except TypeError:  # Odoo 17: no expiration argument
            cls.key = Keys._generate('rpc', 'mcp readonly test')
        cls.config = env['bs.mcp.readonly.config'].search([], limit=1)
        cls.partners = env['res.partner'].create([
            {'name': 'RO Test Co %s' % i, 'ref': 'ROTEST', 'email': 'ro%s@example.com' % i} for i in range(5)])

    def rpc(self, method, params=None, key=None, msg_id=1):
        body = json.dumps({'jsonrpc': '2.0', 'id': msg_id, 'method': method, 'params': params or {}})
        headers = {'Content-Type': 'application/json'}
        if key is not False:
            headers['Authorization'] = 'Bearer %s' % (key or self.key)
        return self.url_open('/mcp/readonly', data=body, headers=headers)

    def call(self, tool, **arguments):
        res = self.rpc('tools/call', {'name': tool, 'arguments': arguments})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()['result']

    def test_01_auth_required(self):
        self.assertEqual(self.rpc('initialize', key=False).status_code, 401)
        self.assertEqual(self.rpc('initialize', key='wrong-key').status_code, 401)

    def test_02_initialize_ping_and_list(self):
        res = self.rpc('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                      'clientInfo': {'name': 'test', 'version': '1'}}).json()['result']
        self.assertEqual(res['protocolVersion'], '2025-06-18')
        self.assertIn('MCP Server for Claude by Bentley Studios', res['instructions'])
        self.assertEqual(self.rpc('ping').json()['result'], {})
        tools = self.rpc('tools/list').json()['result']['tools']
        self.assertEqual({t['name'] for t in tools},
                         {'odoo_list_models', 'odoo_describe_model', 'odoo_search_read', 'odoo_read', 'odoo_count'})
        for t in tools:
            self.assertTrue(t['annotations']['readOnlyHint'])
            self.assertIn('MCP Server for Claude by Bentley Studios', t['description'])
        notif = self.url_open('/mcp/readonly', data=json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized'}),
                              headers={'Authorization': 'Bearer %s' % self.key, 'Content-Type': 'application/json'})
        self.assertEqual(notif.status_code, 202)

    def test_03_settings_form_note(self):
        self.assertIn('MCP Server for Claude by Bentley Studios', self.config.info)

    def test_10_list_and_describe(self):
        res = self.call('odoo_list_models')
        names = {m['model'] for m in res['structuredContent']['models']}
        self.assertIn('res.partner', names)
        self.assertNotIn('ir.config_parameter', names)  # no read right for this user
        desc = self.call('odoo_describe_model', model='res.partner')
        self.assertIn('email', desc['structuredContent']['fields'])

    def test_11_search_read_read_count(self):
        res = self.call('odoo_search_read', model='res.partner', domain=[['ref', '=', 'ROTEST']],
                        fields=['name', 'email'], order='id asc')
        self.assertFalse(res['isError'], res)
        self.assertEqual(res['structuredContent']['total'], 5)
        self.assertEqual(res['structuredContent']['records'][0]['name'], 'RO Test Co 0')
        rd = self.call('odoo_read', model='res.partner', ids=self.partners[:2].ids, fields=['name'])
        self.assertEqual([r['name'] for r in rd['structuredContent']['records']], ['RO Test Co 0', 'RO Test Co 1'])
        cnt = self.call('odoo_count', model='res.partner', domain=[['ref', '=', 'ROTEST']])
        self.assertEqual(cnt['structuredContent']['count'], 5)

    def test_12_user_rights_apply(self):
        res = self.call('odoo_search_read', model='ir.config_parameter')
        self.assertTrue(res['isError'])
        self.assertIn('no read access', res['content'][0]['text'])

    def test_13_allowed_models_block_others(self):
        self.config.allowed_model_ids = [(6, 0, [self.env['ir.model']._get('res.partner').id])]
        ok = self.call('odoo_count', model='res.partner')
        self.assertFalse(ok['isError'])
        blocked = self.call('odoo_count', model='res.users')
        self.assertTrue(blocked['isError'])
        self.assertIn('not allowed', blocked['content'][0]['text'])
        names = {m['model'] for m in self.call('odoo_list_models')['structuredContent']['models']}
        self.assertEqual(names, {'res.partner'})
        # a path into a model outside the list is refused too
        path = self.call('odoo_count', model='res.partner', domain=[['user_ids.login', '=', 'admin']])
        self.assertTrue(path['isError'])

    def test_14_limit_enforced(self):
        self.config.max_records = 3
        res = self.call('odoo_search_read', model='res.partner', domain=[['ref', '=', 'ROTEST']], limit=100)
        self.assertEqual(res['structuredContent']['count'], 3)
        self.assertTrue(res['structuredContent']['truncated'])
        res = self.call('odoo_search_read', model='res.partner', domain=[['ref', '=', 'ROTEST']])
        self.assertEqual(res['structuredContent']['count'], 3)

    def test_20_write_and_unknown_tools_refused(self):
        for tool in ('odoo_create', 'odoo_update', 'odoo_archive', 'odoo_unlink', 'execute_kw'):
            r = self.rpc('tools/call', {'name': tool, 'arguments': {'model': 'res.partner', 'values': {'name': 'x'}}})
            self.assertEqual(r.json()['error']['code'], -32602, tool)
        self.assertFalse(self.env['res.partner'].search([('name', '=', 'x')]))
        self.assertEqual(self.rpc('resources/write').json()['error']['code'], -32601)
        # extra arguments are refused rather than ignored
        r = self.call('odoo_count', model='res.partner', values={'name': 'x'})
        self.assertTrue(r['isError'])
