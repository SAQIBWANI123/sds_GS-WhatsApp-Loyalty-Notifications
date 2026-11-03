from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    gs_whatsapp_webhook_url_otp_registration = fields.Char(
        string='OTP Registration Webhook',
        config_parameter='gs_whatsapp_loyalty.webhook_url_otp_registration',
    )
    gs_whatsapp_webhook_url_welcome = fields.Char(
        string='Welcome Webhook',
        config_parameter='gs_whatsapp_loyalty.webhook_url_welcome',
    )
    gs_whatsapp_webhook_url_post_purchase = fields.Char(
        string='Post Purchase Webhook',
        config_parameter='gs_whatsapp_loyalty.webhook_url_post_purchase',
    )
    gs_whatsapp_webhook_url_otp_redeem = fields.Char(
        string='OTP Redeem Webhook',
        config_parameter='gs_whatsapp_loyalty.webhook_url_otp_redeem',
    )
    gs_whatsapp_webhook_url_redeem_success = fields.Char(
        string='Redeem Success Webhook',
        config_parameter='gs_whatsapp_loyalty.webhook_url_redeem_success',
    )
    gs_whatsapp_webhook_url_points_expiry = fields.Char(
        string='Points Expiry Webhook',
        config_parameter='gs_whatsapp_loyalty.webhook_url_points_expiry',
    )
    gs_whatsapp_expiry_alert_days = fields.Integer(
        string='Expiry Alert Days',
        config_parameter='gs_whatsapp_loyalty.expiry_alert_days',
        default=7,
    )
    gs_whatsapp_default_starting_points = fields.Float(
        string='Default Starting Points',
        config_parameter='nma_pos_loyalty_points.grant_points',
        default=0,
        help='Points granted once after a new customer confirms the registration OTP.',
    )

    @api.constrains('gs_whatsapp_default_starting_points')
    def _check_default_starting_points(self):
        for settings in self:
            if settings.gs_whatsapp_default_starting_points < 0:
                raise ValidationError(_("Default starting points cannot be negative."))
