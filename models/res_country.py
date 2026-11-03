import re

from odoo import api, models
from odoo.osv import expression


class ResCountry(models.Model):
    _inherit = 'res.country'

    def _phone_flag(self):
        self.ensure_one()
        code = (self.code or '').upper()
        if len(code) != 2 or not code.isalpha():
            return '🏳'
        return ''.join(chr(127397 + ord(char)) for char in code)

    def name_get(self):
        if not self.env.context.get('show_phone_code'):
            return super().name_get()
        return [
            (
                country.id,
                '%s +%s %s' % (
                    country._phone_flag(),
                    country.phone_code or '',
                    country.name,
                ),
            )
            for country in self
        ]

    @api.model
    def _name_search(
        self,
        name='',
        args=None,
        operator='ilike',
        limit=100,
        name_get_uid=None,
    ):
        args = list(args or [])
        if not self.env.context.get('show_phone_code') or not name:
            return super()._name_search(
                name=name,
                args=args,
                operator=operator,
                limit=limit,
                name_get_uid=name_get_uid,
            )

        digits = re.sub(r'\D', '', name)
        domains = [
            [('name', operator, name)],
            [('code', operator, name)],
        ]
        if digits:
            domains.append([('phone_code', '=', int(digits))])
        domain = expression.AND([args, expression.OR(domains)])
        return self._search(
            domain,
            limit=limit,
            access_rights_uid=name_get_uid,
        )
