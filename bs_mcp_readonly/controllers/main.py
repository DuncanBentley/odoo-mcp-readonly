# -*- coding: utf-8 -*-
"""Read-only MCP server (Streamable HTTP transport, JSON responses, no server-sent events).

POST /mcp/readonly  with  Authorization: Bearer <Odoo API key>
"""
import json
import logging

from odoo import http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from .tools import NOTE, TOOLS, ToolDenied, Toolbox

_logger = logging.getLogger(__name__)

SERVER_NAME = 'odoo-mcp-readonly'
SERVER_VERSION = '1.0.0'
PROTOCOL_VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')
MAX_BODY = 1024 * 1024  # 1 MB

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603


def _rpc_error(msg_id, code, message):
    return {'jsonrpc': '2.0', 'id': msg_id, 'error': {'code': code, 'message': message}}


def _rpc_result(msg_id, result):
    return {'jsonrpc': '2.0', 'id': msg_id, 'result': result}


class McpReadonlyController(http.Controller):

    def _json(self, payload, status=200, extra_headers=None):
        headers = [('Content-Type', 'application/json'), ('Cache-Control', 'no-store')]
        headers += extra_headers or []
        body = json.dumps(payload, default=str) if payload is not None else ''
        return request.make_response(body, headers=headers, status=status)

    def _unauthorized(self, message):
        return self._json(_rpc_error(None, INVALID_REQUEST, message), status=401,
                          extra_headers=[('WWW-Authenticate', 'Bearer realm="odoo-mcp-readonly"')])

    def _authenticate(self):
        """Return (uid, error message) from the Bearer API key (scope rpc)."""
        auth = request.httprequest.headers.get('Authorization', '')
        if not auth.lower().startswith('bearer '):
            return None, 'Missing "Authorization: Bearer <Odoo API key>" header.'
        key = auth[7:].strip()
        if not key:
            return None, 'Empty API key.'
        uid = request.env['res.users.apikeys'].sudo()._check_credentials(scope='rpc', key=key)
        if not uid:
            return None, 'Invalid or expired API key.'
        if not request.env['res.users'].sudo().browse(uid).active:
            return None, 'User is archived.'
        return uid, ''

    # The route only reads, but the API key check may update the key's usage data, so keep it read-write on 19+.
    @http.route('/mcp/readonly', type='http', auth='none', methods=['GET', 'POST', 'DELETE', 'OPTIONS'], csrf=False,
                save_session=False, readonly=False)
    def mcp_readonly(self, **kw):
        if request.httprequest.method != 'POST':
            return request.make_response('', headers=[('Allow', 'POST')], status=405)
        if not request.db:
            return self._json(_rpc_error(None, INVALID_REQUEST, 'No database selected (add ?db=<name> to the URL).'), 400)
        raw = request.httprequest.get_data(cache=False, as_text=False)
        if len(raw) > MAX_BODY:
            return self._json(_rpc_error(None, INVALID_REQUEST, 'Request too large.'), 413)
        try:
            message = json.loads(raw or b'null')
        except ValueError:
            return self._json(_rpc_error(None, PARSE_ERROR, 'Invalid JSON.'), 400)

        uid, error = self._authenticate()
        if not uid:
            return self._unauthorized(error)
        request.update_env(user=uid)

        batch = isinstance(message, list)
        replies = [r for r in (self._handle(m) for m in (message if batch else [message])) if r is not None]
        if not replies:
            return request.make_response('', status=202)
        return self._json(replies if batch else replies[0],
                          extra_headers=[('MCP-Protocol-Version', PROTOCOL_VERSIONS[0])])

    def _handle(self, msg):
        if not isinstance(msg, dict) or msg.get('jsonrpc') != '2.0' or 'method' not in msg:
            return _rpc_error(msg.get('id') if isinstance(msg, dict) else None, INVALID_REQUEST, 'Not a JSON-RPC 2.0 request.')
        msg_id, method, params = msg.get('id'), msg['method'], msg.get('params') or {}
        if msg_id is None:
            return None
        if method == 'initialize':
            asked = params.get('protocolVersion')
            version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
            return _rpc_result(msg_id, {
                'protocolVersion': version,
                'capabilities': {'tools': {'listChanged': False}},
                'serverInfo': {'name': SERVER_NAME, 'title': 'Odoo MCP Connector Read-only', 'version': SERVER_VERSION},
                'instructions': ('You are connected to Odoo through a READ-ONLY connector: you can list models, '
                                 'describe fields, search, read and count, nothing else. Start with odoo_list_models. '
                                 + NOTE),
            })
        if method == 'ping':
            return _rpc_result(msg_id, {})
        if method == 'tools/list':
            return _rpc_result(msg_id, {'tools': TOOLS})
        if method == 'tools/call':
            return self._call_tool(msg_id, params)
        if method in ('resources/list', 'prompts/list'):
            return _rpc_result(msg_id, {method.split('/')[0]: []})
        return _rpc_error(msg_id, METHOD_NOT_FOUND, 'Method not found: %s' % method)

    def _call_tool(self, msg_id, params):
        name = params.get('name')
        args = params.get('arguments') or {}
        if not isinstance(args, dict):
            return _rpc_error(msg_id, INVALID_PARAMS, 'arguments must be an object')
        known = {t['name']: t for t in TOOLS}
        if name not in known:
            return _rpc_error(msg_id, INVALID_PARAMS, 'Unknown tool: %s. This connector is read-only.' % name)
        extra = set(args) - set(known[name]['inputSchema']['properties'])
        if extra:
            return self._tool_reply(msg_id, 'Unknown argument(s): %s' % ', '.join(sorted(extra)), error=True)
        allowed, max_records = request.env['bs.mcp.readonly.config'].get_settings()
        box = Toolbox(request.env, allowed, max_records)
        try:
            with request.env.cr.savepoint():
                result, _count = getattr(box, name)(**args)
        except ToolDenied as e:
            return self._tool_reply(msg_id, str(e), error=True)
        except (AccessError, UserError, ValidationError) as e:
            return self._tool_reply(msg_id, 'Odoo refused: %s' % e, error=True)
        except (TypeError, ValueError) as e:
            return self._tool_reply(msg_id, 'Bad arguments: %s' % e, error=True)
        except Exception as e:  # never leak a traceback to the client
            _logger.exception('MCP read-only tool %s failed', name)
            return self._tool_reply(msg_id, 'Odoo error: %s' % e, error=True)
        return self._tool_reply(msg_id, result)

    def _tool_reply(self, msg_id, payload, error=False):
        text = payload if isinstance(payload, str) else json.dumps(payload, default=str, ensure_ascii=False)
        result = {'content': [{'type': 'text', 'text': text}], 'isError': bool(error)}
        if not error and isinstance(payload, dict):
            result['structuredContent'] = json.loads(json.dumps(payload, default=str))
        return _rpc_result(msg_id, result)
