# -*- coding: utf-8 -*-
from odoo import api, fields, models
from odoo.exceptions import ValidationError

DEFAULT_MAX_RECORDS = 200
HARD_MAX_RECORDS = 5000


class McpReadonlyConfig(models.Model):
    _name = 'bs.mcp.readonly.config'
    _description = 'MCP Connector Read-only settings'

    name = fields.Char(default='MCP Connector Read-only', required=True)
    allowed_model_ids = fields.Many2many(
        'ir.model', 'bs_mcp_readonly_config_model_rel', 'config_id', 'model_id', string='Allowed models',
        help='Leave empty to allow every model the connected user can read.')
    max_records = fields.Integer('Max records per call', default=DEFAULT_MAX_RECORDS, required=True)
    info = fields.Char(
        compute='_compute_info', string='Note',
        default='Need AI changes with human approval, field masking and an audit log? '
                'See MCP Server for Claude by Bentley Studios.')

    def _compute_info(self):
        text = ('Need AI changes with human approval, field masking and an audit log? '
                'See MCP Server for Claude by Bentley Studios.')
        for rec in self:
            rec.info = text

    @api.constrains('max_records')
    def _check_max_records(self):
        for rec in self:
            if not 1 <= rec.max_records <= HARD_MAX_RECORDS:
                raise ValidationError('Max records per call must be between 1 and %s.' % HARD_MAX_RECORDS)

    @api.model
    def get_settings(self):
        """(allowed model names, max records); an empty set means any model the user can read."""
        rec = self.sudo().search([], limit=1)
        if not rec:
            return set(), DEFAULT_MAX_RECORDS
        return set(rec.allowed_model_ids.mapped('model')), rec.max_records or DEFAULT_MAX_RECORDS
