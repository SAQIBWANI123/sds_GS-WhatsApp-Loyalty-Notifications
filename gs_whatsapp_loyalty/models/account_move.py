# -*- coding: utf-8 -*-
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    gs_post_purchase_sent = fields.Boolean(copy=False, readonly=True)

    def _gs_portal_url(self):
        """Absolute portal URL carrying the access token, so a customer can
        open the invoice from WhatsApp without logging in."""
        self.ensure_one()
        try:
            return '%s%s' % (self.get_base_url(), self.get_portal_url())
        except Exception:
            _logger.exception(
                "gs_whatsapp_loyalty: cannot build portal URL for move %s", self.id
            )
            return ''

    def _gs_defer_post_purchase(self):
        """Whether the loyalty balance is still moving when this move is posted.

        Deferred moves are notified later by whoever settles the balance.
        """
        self.ensure_one()
        return False

    def _gs_post_purchase_candidates(self):
        return self.filtered(
            lambda m: m.move_type == 'out_invoice'
            and m.partner_id
            and not m.gs_post_purchase_sent
        )

    # ----------------------------------------------------------
    # Case 3: بعد الشراء - إشعار الفاتورة + النقاط المتاحة
    # ----------------------------------------------------------
    def _gs_send_post_purchase(self):
        Event = self.env['gs.whatsapp.event']
        for move in self._gs_post_purchase_candidates():
            partner = move.partner_id
            queued = Event.queue_event('post_purchase', partner, {
                'invoice_number': move.name,
                'invoice_date': str(move.invoice_date or ''),
                'invoice_url': move._gs_portal_url(),
                'amount_total': move.amount_total,
                'currency': move.currency_id.name,
                'points_balance': partner._gs_loyalty_balance(),
            })
            # Left unset on failure so a retry can pick the move up again.
            if queued:
                move.sudo().gs_post_purchase_sent = True
        return True

    def _post(self, soft=True):
        posted = super()._post(soft=soft)
        for move in posted._gs_post_purchase_candidates():
            if not move._gs_defer_post_purchase():
                move._gs_send_post_purchase()
        return posted
