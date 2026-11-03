# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import api, fields, models

EXPIRY_DAYS_PARAM = 'gs_whatsapp_loyalty.expiry_alert_days'


class LoyaltyCard(models.Model):
    _inherit = 'loyalty.card'

    gs_expiry_alert_sent = fields.Boolean(copy=False)

    # ------------------------------------------------------------------
    # Case 5: بعد الاسترداد - إشعار نجاح استرداد النقاط
    # Odoo 16 has no loyalty.history model (added in 17), so redemption
    # is detected as a points decrease on a loyalty-type card.
    # ------------------------------------------------------------------
    def write(self, vals):
        old_points = {}
        if 'points' in vals:
            old_points = {card.id: card.points for card in self}
        res = super().write(vals)
        if 'points' in vals and not self.env.context.get('gs_skip_redeem_notify'):
            Event = self.env['gs.whatsapp.event']
            for card in self:
                if (card.partner_id
                        and card.program_id.program_type == 'loyalty'
                        and card.points < old_points.get(card.id, 0)):
                    Event.send_event('redeem_success', card.partner_id, {
                        'points_redeemed': old_points[card.id] - card.points,
                        'points_balance': card.points,
                        'description': card.program_id.name or '',
                    })
        return res

    # ------------------------------------------------------------------
    # Case 6: قبل انتهاء الصلاحية - تنبيه أسبوع قبل الانتهاء (cron يومي)
    # Alert window configurable via ir.config_parameter (default 7 days
    # per the تعديل column: changed from 1 day to 1 week).
    # ------------------------------------------------------------------
    @api.model
    def _cron_gs_points_expiry_alert(self):
        icp = self.env['ir.config_parameter'].sudo()
        days = int(icp.get_param(EXPIRY_DAYS_PARAM, '7'))
        target_date = fields.Date.today() + timedelta(days=days)

        cards = self.search([
            ('expiration_date', '!=', False),
            ('expiration_date', '<=', target_date),
            ('expiration_date', '>=', fields.Date.today()),
            ('points', '>', 0),
            ('partner_id', '!=', False),
            ('gs_expiry_alert_sent', '=', False),
            ('program_id.program_type', '=', 'loyalty'),
        ])
        Event = self.env['gs.whatsapp.event']
        for card in cards:
            sent = Event.send_event('points_expiry', card.partner_id, {
                'expiring_points': card.points,
                'expiry_date': str(card.expiration_date),
                'days_remaining': (card.expiration_date - fields.Date.today()).days,
            })
            if sent:
                card.with_context(gs_skip_redeem_notify=True).gs_expiry_alert_sent = True
        return True
