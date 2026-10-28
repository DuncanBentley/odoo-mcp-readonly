# -*- coding: utf-8 -*-
"""Small helpers that behave the same on Odoo 18, 19 and 20."""


def get_param(env, key, default=''):
    """ir.config_parameter value as a string. Odoo 20 replaced get_param() with typed getters (get_str...)."""
    params = env['ir.config_parameter'].sudo()
    if hasattr(params, 'get_param'):
        return params.get_param(key, default)
    return params.get_str(key, default)
