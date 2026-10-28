# -*- coding: utf-8 -*-
"""Read-only MCP tools. The env runs as the API key's user, so Odoo access rights and record rules apply."""
from odoo.exceptions import AccessError

NOTE = ('Need AI changes with human approval, field masking and an audit log? '
        'See MCP Server for Claude by Bentley Studios.')
DOMAIN_OPERATORS = {'=', '!=', '<', '<=', '>', '>=', 'in', 'not in', 'like', 'ilike', 'not like', 'not ilike',
                    '=like', '=ilike', 'child_of', 'parent_of', 'any', 'not any'}


class ToolDenied(Exception):
    """Refused with a clear message for the AI."""


def _schema(props, required=()):
    return {'type': 'object', 'properties': props, 'required': list(required), 'additionalProperties': False}


MODEL = {'type': 'string', 'description': 'Technical model name, e.g. "res.partner" or "sale.order".'}
DOMAIN = {'type': 'array', 'description': 'Odoo search domain, e.g. [["state","=","sale"],["amount_total",">",1000]]. '
                                          'Use [] for all records.', 'items': {}}

TOOLS = [
    {'name': 'odoo_list_models', 'title': 'List models',
     'description': 'List the Odoo models this connection may read, with labels. Call this first. Read-only connector. ' + NOTE,
     'inputSchema': _schema({}), 'annotations': {'readOnlyHint': True}},
    {'name': 'odoo_describe_model', 'title': 'Describe model',
     'description': 'List the fields of a model (name, type, label, relation, required). Read-only. ' + NOTE,
     'inputSchema': _schema({'model': MODEL}, ['model']), 'annotations': {'readOnlyHint': True}},
    {'name': 'odoo_search_read', 'title': 'Search and read records',
     'description': 'Search records and return field values (read-only). Returns at most the configured record limit. '
                    'Many2one values come back as [id, name]. ' + NOTE,
     'inputSchema': _schema({'model': MODEL, 'domain': DOMAIN,
                             'fields': {'type': 'array', 'items': {'type': 'string'},
                                        'description': 'Fields to return. Default: a few common fields.'},
                             'limit': {'type': 'integer', 'minimum': 1}, 'offset': {'type': 'integer', 'minimum': 0},
                             'order': {'type': 'string', 'description': 'e.g. "date_order desc, id desc"'}}, ['model']),
     'annotations': {'readOnlyHint': True}},
    {'name': 'odoo_read', 'title': 'Read records by id',
     'description': 'Read specific records by id (read-only). ' + NOTE,
     'inputSchema': _schema({'model': MODEL, 'ids': {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 1},
                             'fields': {'type': 'array', 'items': {'type': 'string'}}}, ['model', 'ids']),
     'annotations': {'readOnlyHint': True}},
    {'name': 'odoo_count', 'title': 'Count records',
     'description': 'Count records matching a domain (read-only). ' + NOTE,
     'inputSchema': _schema({'model': MODEL, 'domain': DOMAIN}, ['model']), 'annotations': {'readOnlyHint': True}},
]


class Toolbox:
    def __init__(self, env, allowed, max_records):
        self.env = env
        self.allowed = allowed          # set of model names; empty = any model the user can read
        self.max_records = max_records

    # ---- checks ---------------------------------------------------------------------------
    def _readable(self, name):
        if name not in self.env:
            return False
        Model = self.env[name]
        if Model._transient or Model._abstract:
            return False
        try:
            if hasattr(Model, 'check_access'):
                Model.check_access_rights('read')
                Model.check_access_rule('read')
            else:
                Model.check_access_rights('read')
        except AccessError:
            return False
        return True

    def model(self, name):
        if not isinstance(name, str) or name not in self.env:
            raise ToolDenied('Unknown model "%s". Use odoo_list_models.' % (name,))
        if self.allowed and name not in self.allowed:
            raise ToolDenied('Model "%s" is not allowed for this connection. Use odoo_list_models.' % name)
        if not self._readable(name):
            raise ToolDenied('You have no read access to model "%s".' % name)
        return self.env[name]

    def check_path(self, model, path):
        current = model
        parts = path.split('.')
        for i, part in enumerate(parts):
            field = current._fields.get(part)
            if field is None:
                raise ToolDenied('Unknown field "%s" on %s.' % (part, current._name))
            if i < len(parts) - 1:
                if not field.relational:
                    raise ToolDenied('"%s" is not a relation, cannot follow "%s".' % (part, path))
                if self.allowed and field.comodel_name not in self.allowed:
                    raise ToolDenied('Path "%s" leads into model %s, which is not allowed.' % (path, field.comodel_name))
                current = self.env[field.comodel_name]

    def check_domain(self, model, domain):
        if domain in (None, ''):
            return []
        if not isinstance(domain, list):
            raise ToolDenied('Domain must be a list.')
        for leaf in domain:
            if leaf in ('&', '|', '!'):
                continue
            if not isinstance(leaf, (list, tuple)) or len(leaf) != 3:
                raise ToolDenied('Bad domain element %r: use ["field", "operator", value] or "&", "|", "!".' % (leaf,))
            path, op, value = leaf
            if not isinstance(path, str) or op not in DOMAIN_OPERATORS:
                raise ToolDenied('Bad domain element %r.' % (leaf,))
            self.check_path(model, path)
            if op in ('any', 'not any'):
                field = model._fields[path.split('.')[0]]
                self.check_domain(self.env[field.comodel_name], value)
        return [list(x) if isinstance(x, tuple) else x for x in domain]

    def field_names(self, model, requested):
        if requested:
            for f in requested:
                if not isinstance(f, str) or f not in model._fields:
                    raise ToolDenied('Unknown field %r on %s.' % (f, model._name))
            return list(requested)
        default = [f for f in ('display_name', 'name', 'state', 'date', 'partner_id', 'amount_total', 'email', 'phone')
                   if f in model._fields]
        return default or ['display_name']

    def hide_foreign(self, model, rows):
        """Many2one to a model outside the allowed list: ids only, no names."""
        if not self.allowed:
            return rows
        for row in rows:
            for key, val in list(row.items()):
                field = model._fields.get(key)
                if field is not None and field.type == 'many2one' and field.comodel_name not in self.allowed:
                    row[key] = val[0] if val else False
        return rows

    # ---- tools ----------------------------------------------------------------------------
    def odoo_list_models(self):
        if self.allowed:
            names = sorted(n for n in self.allowed if self._readable(n))
        else:
            names = sorted(n for n in self.env['ir.model'].sudo().search([('transient', '=', False)]).mapped('model')
                           if self._readable(n))
        labels = {m.model: m.name for m in self.env['ir.model'].sudo().search([('model', 'in', names)])}
        out = [{'model': n, 'label': labels.get(n, n)} for n in names]
        return {'models': out, 'max_records': self.max_records, 'read_only': True}, len(out)

    def odoo_describe_model(self, model):
        Model = self.model(model)
        info = Model.fields_get(attributes=['string', 'type', 'relation', 'required', 'readonly', 'selection', 'help'])
        out = {}
        for name, meta in sorted(info.items()):
            if len(meta.get('help') or '') > 200:
                meta.pop('help')
            out[name] = meta
        return {'model': model, 'fields': out}, len(out)

    def odoo_search_read(self, model, domain=None, fields=None, limit=None, offset=0, order=None):
        Model = self.model(model)
        domain = self.check_domain(Model, domain)
        fnames = self.field_names(Model, fields)
        limit = min(int(limit or self.max_records), self.max_records)
        if order:
            for part in str(order).split(','):
                bits = part.split()
                if not bits or len(bits) > 2 or (len(bits) == 2 and bits[1].lower() not in ('asc', 'desc')):
                    raise ToolDenied('Bad order "%s".' % order)
                self.check_path(Model, bits[0])
        offset = int(offset or 0)
        rows = Model.search_read(domain, fnames, offset=offset, limit=limit, order=order or None)
        total = Model.search_count(domain)
        rows = self.hide_foreign(Model, rows)
        return {'model': model, 'count': len(rows), 'total': total, 'records': rows,
                'truncated': total > offset + len(rows)}, len(rows)

    def odoo_read(self, model, ids, fields=None):
        Model = self.model(model)
        if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
            raise ToolDenied('"ids" must be a list of integers.')
        if len(ids) > self.max_records:
            raise ToolDenied('Too many ids in one call (max %s).' % self.max_records)
        fnames = self.field_names(Model, fields)
        recs = Model.browse(ids).exists()
        rows = self.hide_foreign(Model, recs.read(fnames))
        return {'model': model, 'count': len(rows), 'records': rows}, len(rows)

    def odoo_count(self, model, domain=None):
        Model = self.model(model)
        n = Model.search_count(self.check_domain(Model, domain))
        return {'model': model, 'count': n}, n
