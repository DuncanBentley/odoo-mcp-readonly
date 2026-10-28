# -*- coding: utf-8 -*-
"""Odoo 15 only (build.py copies this file into modules whose controllers use plain `type='http'` routes with JSON bodies,
`request.update_env` or `request.make_response(..., status=)`; bs_mcp_readonly is replaced by the module's name).

Odoo 15 differs from 16+ in three ways that these controllers rely on, each patched here with the 16+ behaviour:
1. Every request whose Content-Type is application/json becomes a JSON-RPC `JsonRequest`, and an `http` route then refuses it
   ("capable of handling request of type 'http' but called with a request of type 'json'"). `Root.get_request` now hands such
   a request to `HttpRequest` when the path matches one of this module's `http` routes, so the controller can read the raw body.
2. `request.update_env(user=..., context=...)` does not exist; setting `request.uid` / `request.context` does the same.
3. `request.make_response(data, headers, cookies)` has no `status` argument."""
import werkzeug.routing

from odoo import http

MODULE = 'bs_mcp_readonly'
JSON_TYPES = ('application/json', 'application/json-rpc')
_state = {}


def _http_routes():
    """werkzeug Map of this module's type='http' routes (built once, when the first JSON request arrives)."""
    if 'map' not in _state:
        from odoo.addons.base.models.ir_http import IrHttp
        converters = IrHttp._get_converters()
        routes = werkzeug.routing.Map(strict_slashes=False, converters=converters)
        # a controller that extends another module's controller is only found through its base class, so look at every module
        modules = sorted(set(http.controllers_per_module) | {MODULE})
        mine = 'odoo.addons.%s.' % MODULE
        for url, endpoint, routing in http._generate_routing_rules(modules, False, converters):
            if routing.get('type', 'http') == 'http' and getattr(endpoint.original, '__module__', '').startswith(mine):
                routes.add(werkzeug.routing.Rule(url, endpoint=endpoint, methods=routing.get('methods')))
        _state['map'] = routes
    return _state['map']


if not getattr(http.Root, '_bs_http15_' + MODULE, False):
    _orig_get_request = http.Root.get_request

    def get_request(self, httprequest):
        if httprequest.mimetype in JSON_TYPES:
            try:
                _http_routes().bind('localhost').match(httprequest.path, method=httprequest.method)
            except werkzeug.exceptions.HTTPException:
                pass
            else:
                return http.HttpRequest(httprequest)
        return _orig_get_request(self, httprequest)

    http.Root.get_request = get_request
    setattr(http.Root, '_bs_http15_' + MODULE, True)


def _update_env(self, user=None, context=None, su=None):
    if user is not None:
        self.uid = user.id if hasattr(user, 'id') else user  # the setter drops the cached env
    if context is not None:
        self.context = context


if not hasattr(http.WebRequest, 'update_env'):
    http.WebRequest.update_env = _update_env

if not getattr(http.HttpRequest.make_response, '_bs_status', False):
    _orig_make_response = http.HttpRequest.make_response

    def _make_response(self, data, headers=None, cookies=None, status=None):
        response = _orig_make_response(self, data, headers=headers, cookies=cookies)
        if status:
            response.status_code = status
        return response

    _make_response._bs_status = True
    http.HttpRequest.make_response = _make_response
