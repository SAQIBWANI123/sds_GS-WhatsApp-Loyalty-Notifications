from datetime import timedelta

from odoo import _, api, fields, models


class GsWhatsappEventTest(models.TransientModel):
    _name = 'gs.whatsapp.event.test'
    _description = 'WhatsApp Loyalty Event Test'

    partner_id = fields.Many2one(
        'res.partner',
        string='Test Customer',
        required=True,
    )
    otp_code = fields.Char(default='123456', required=True)
    expires_in_minutes = fields.Integer(default=5, required=True)
    welcome_points = fields.Float(default=100)
    points_balance = fields.Float(default=450)
    invoice_number = fields.Char(default='TEST/0001', required=True)
    invoice_date = fields.Date(default=fields.Date.context_today, required=True)
    invoice_url = fields.Char(
        string='Invoice URL',
        compute='_compute_invoice_url',
        store=False,
        readonly=False,
        help='Portal link sent with the post purchase message.',
    )
    amount_total = fields.Monetary(default=100)
    currency_id = fields.Many2one(
        'res.currency',
        required=True,
        default=lambda self: self.env.company.currency_id,
    )
    points_to_redeem = fields.Float(default=50)
    points_redeemed = fields.Float(default=50)
    description = fields.Char(default='Webhook test')
    expiring_points = fields.Float(default=100)
    expiry_date = fields.Date(
        default=lambda self: fields.Date.context_today(self) + timedelta(days=7),
        required=True,
    )
    days_remaining = fields.Integer(default=7)

    @api.depends('partner_id')
    def _compute_invoice_url(self):
        """Prefill with the customer's latest posted invoice so the test
        exercises a real portal link rather than a made up one."""
        for wizard in self:
            invoice = self.env['account.move'].search([
                ('move_type', '=', 'out_invoice'),
                ('state', '=', 'posted'),
                ('partner_id', '=', wizard.partner_id.id),
            ], limit=1, order='invoice_date desc, id desc')
            wizard.invoice_url = invoice._gs_portal_url() if invoice else ''

    def _event_data(self):
        self.ensure_one()
        return {
            'otp_registration': {
                'otp_code': self.otp_code,
                'expires_in_minutes': self.expires_in_minutes,
            },
            'welcome': {
                'welcome_points': self.welcome_points,
                'points_balance': self.points_balance,
            },
            'post_purchase': {
                'invoice_number': self.invoice_number,
                'invoice_date': str(self.invoice_date),
                'invoice_url': self.invoice_url or '',
                'amount_total': self.amount_total,
                'currency': self.currency_id.name,
                'points_balance': self.points_balance,
            },
            'otp_redeem': {
                'otp_code': self.otp_code,
                'points_to_redeem': self.points_to_redeem,
                'expires_in_minutes': self.expires_in_minutes,
            },
            'redeem_success': {
                'points_redeemed': self.points_redeemed,
                'points_balance': self.points_balance,
                'description': self.description or '',
            },
            'points_expiry': {
                'expiring_points': self.expiring_points,
                'expiry_date': str(self.expiry_date),
                'days_remaining': self.days_remaining,
            },
        }

    def _notification(self, successful_events, failed_events):
        if not failed_events:
            message = _(
                '%(count)s webhook event(s) sent successfully.',
                count=len(successful_events),
            )
            notification_type = 'success'
        else:
            message = _(
                'Sent: %(successful)s. Failed: %(failed)s. '
                'Check the webhook URL, customer phone, and event logs.',
                successful=', '.join(successful_events) or _('None'),
                failed=', '.join(failed_events),
            )
            notification_type = 'warning'
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('WhatsApp Webhook Test'),
                'message': message,
                'type': notification_type,
                'sticky': bool(failed_events),
            },
        }

    def _send_events(self, event_types):
        self.ensure_one()
        event_data = self._event_data()
        successful_events = []
        failed_events = []
        Event = self.env['gs.whatsapp.event']
        for event_type in event_types:
            sent = Event.send_event(
                event_type,
                self.partner_id,
                event_data[event_type],
            )
            target = successful_events if sent else failed_events
            target.append(event_type)
        return self._notification(successful_events, failed_events)

    def action_test_otp_registration(self):
        return self._send_events(['otp_registration'])

    def action_test_welcome(self):
        return self._send_events(['welcome'])

    def action_test_post_purchase(self):
        return self._send_events(['post_purchase'])

    def action_test_otp_redeem(self):
        return self._send_events(['otp_redeem'])

    def action_test_redeem_success(self):
        return self._send_events(['redeem_success'])

    def action_test_points_expiry(self):
        return self._send_events(['points_expiry'])

    def action_test_all(self):
        return self._send_events(list(self._event_data()))
