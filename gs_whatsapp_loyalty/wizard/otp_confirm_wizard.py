import json

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class GsWhatsappOtpWizard(models.TransientModel):
    _name = 'gs.whatsapp.otp.wizard'
    _description = 'Confirm WhatsApp Loyalty OTP'

    purpose = fields.Selection([
        ('registration', 'Registration'),
        ('redeem', 'Redeem'),
    ], required=True, default='registration')
    partner_id = fields.Many2one('res.partner', readonly=True)
    pending_otp_id = fields.Many2one('gs.whatsapp.pending.otp', readonly=True)
    name = fields.Char(string='Customer Name')
    mobile = fields.Char(string='Mobile')
    country_id = fields.Many2one('res.country', string='Country')
    partner_vals_json = fields.Text()
    otp_code = fields.Char(string='OTP Code')
    welcome_points = fields.Float(
        string='Welcome Points',
        default=lambda self: float(
            self.env['ir.config_parameter'].sudo().get_param(
                'nma_pos_loyalty_points.grant_points', '0'
            ) or 0
        ),
    )
    points_to_redeem = fields.Float(string='Points to Redeem')

    @api.model
    def action_start_registration(self, name, mobile, country_id, partner_vals_json=None):
        """Send OTP once, then open the confirm wizard (no second send on create)."""
        if not name or not mobile or not country_id:
            raise UserError(_(
                "Customer name, mobile and country are required before sending the OTP."
            ))
        partner_vals = {}
        if partner_vals_json:
            partner_vals = (
                json.loads(partner_vals_json)
                if isinstance(partner_vals_json, str)
                else dict(partner_vals_json)
            )
        # Vendors / appointment suppliers must be created without loyalty OTP.
        if (
            int(partner_vals.get('supplier_rank') or 0) > 0
            or partner_vals.get('is_appointment_supplier')
        ):
            raise UserError(_(
                "WhatsApp registration OTP is only for customers. "
                "Save this supplier contact without OTP verification."
            ))
        pending = self.env['gs.whatsapp.pending.otp'].create_registration_challenge(
            name=name,
            mobile=mobile,
            country_id=int(country_id),
            partner_vals=partner_vals,
        )
        return {
            'name': _('Confirm WhatsApp OTP'),
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'views': [[False, 'form']],
            'target': 'new',
            'context': {
                'default_purpose': 'registration',
                'default_pending_otp_id': pending.id,
                'default_name': pending.name,
                'default_mobile': pending.mobile,
                'default_country_id': pending.country_id.id,
                'default_partner_vals_json': pending.partner_vals_json,
            },
        }

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        # Registration OTP is already sent in action_start_registration.
        # Only auto-send redeem OTP when opening a redeem wizard.
        for wizard in records:
            if (
                wizard.purpose == 'redeem'
                and wizard.partner_id
                and not self.env.context.get('gs_skip_auto_otp')
            ):
                wizard.partner_id.action_send_redeem_otp(
                    points_to_redeem=wizard.points_to_redeem
                )
        return records

    def _start_registration_otp(self):
        self.ensure_one()
        if not self.name or not self.mobile:
            raise UserError(_(
                "Customer name and mobile with country code are required."
            ))
        if not self.country_id:
            raise UserError(_("Country is required before sending the OTP."))
        partner_vals = {}
        if self.partner_vals_json:
            partner_vals = json.loads(self.partner_vals_json)
        pending = self.env['gs.whatsapp.pending.otp'].create_registration_challenge(
            name=self.name,
            mobile=self.mobile,
            country_id=self.country_id.id,
            partner_vals=partner_vals,
        )
        self.write({
            'pending_otp_id': pending.id,
            'mobile': pending.mobile,
            'otp_code': False,
        })
        return pending

    def action_resend_otp(self):
        """Resend a single OTP; do not reopen the wizard (avoids double send)."""
        self.ensure_one()
        if self.purpose == 'registration':
            if self.pending_otp_id:
                self.pending_otp_id.resend()
            else:
                self._start_registration_otp()
        elif self.partner_id:
            self.partner_id.action_send_redeem_otp(
                points_to_redeem=self.points_to_redeem
            )
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('WhatsApp OTP'),
                'message': _('A new OTP has been sent via WhatsApp.'),
                'type': 'success',
                'sticky': False,
            },
        }

    def action_confirm_otp(self):
        self.ensure_one()
        if not self.otp_code:
            raise UserError(_("Please enter the OTP code."))

        if self.purpose == 'registration':
            if not self.pending_otp_id:
                raise UserError(_("Please send the OTP first."))
            partner = self.pending_otp_id.verify_and_create_partner(
                self.otp_code,
                welcome_points=self.welcome_points,
            )
            return {
                'type': 'ir.actions.act_window',
                'name': _('Contact'),
                'res_model': 'res.partner',
                'res_id': partner.id,
                'view_mode': 'form',
                'target': 'current',
                'context': {'form_view_initial_mode': 'readonly'},
            }

        if not self.partner_id:
            raise UserError(_("Missing partner for redeem OTP."))
        self.partner_id.gs_verify_otp(self.otp_code, 'redeem')
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('WhatsApp OTP'),
                'message': _('OTP verified. You can complete the redemption.'),
                'type': 'success',
                'sticky': False,
            },
        }
