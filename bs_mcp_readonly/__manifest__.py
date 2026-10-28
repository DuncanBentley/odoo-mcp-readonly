# -*- coding: utf-8 -*-
{
    'name': 'MCP Connector Read-only',
    'version': '19.0.1.0.0',
    'summary': 'Free read-only MCP server for Odoo: connect Claude, ChatGPT, Cursor or any AI assistant to search '
               'and read your data with an API key. Odoo access rights apply. Model whitelist and record limit.',
    'description': 'Free read-only Model Context Protocol (MCP) connector for Odoo.',
    'category': 'Productivity',
    'author': 'Bentley Studios',
    'license': 'LGPL-3',
    'depends': ['base', 'web'],
    'data': [
        'security/ir.model.access.csv',
        'data/config_data.xml',
        'views/config_views.xml',
    ],
    'images': ['static/description/main_screenshot.png'],
    'support': 'duncanmarcbentley@gmail.com',
    'installable': True,
    'application': False,
}
