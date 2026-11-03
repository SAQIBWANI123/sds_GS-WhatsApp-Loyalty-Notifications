import json
import secrets
from datetime import timedelta

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError

OTP_TTL_MINUTES = 5
SAFE_PARTNER_CREATE_FIELDS = {
    'name', 'mobile', 'phone', 'email', 'street', 'street2', 'zip', 'city',
    'country_id', 'state_id', 'lang', 'dob', 'opted_sms_service',
    'nma_pos_registered', 'company_id', 'type', 'parent_id',
}


class GsWhatsappPendingOtp(models.Model):
    """Holds registration OTP before the contact is created."""

    _name = 'gs.whatsapp.pending.otp'
    _description = 'Pending WhatsApp Registration OTP'
    _order = 'id desc'

    name = fields.Char(required=True)
    mobile = fields.Char(required=True, index=True)
    country_id = fields.Many2one('res.country')
    partner_vals_json = fields.Text()
    otp_code = fields.Char(required=True)
    otp_expiry = fields.Datetime(required=True)
    purpose = fields.Selection([
        ('registration', 'Registration'),
        ('redeem', 'Redeem'),
    ], default='registration', required=True)

    @api.model
    def _generate_code(self):
        return str(secrets.randbelow(900000) + 100000)

    @api.model
    def _sanitize_partner_create_vals(self, vals):
        """Keep only safe scalar / many2one values for res.partner.create.

        OWL form dumps can include x2many datapoint ids (e.g. child_ids:
        'datapoint_12') which crash ORM create.
        """
        Partner = self.env['res.partner']
        clean = {}
        for key, value in (vals or {}).items():
            if value is None or key in (
                'id', 'display_name', 'avatar_128',
                'gs_otp_code', 'gs_otp_expiry', 'gs_otp_purpose',
                'gs_otp_pending', 'gs_welcome_sent', 'mobile_local_number',
                'same_vat_partner_id', 'same_company_registry_partner_id',
            ):
                continue
            if key not in SAFE_PARTNER_CREATE_FIELDS:
                continue
            field = Partner._fields.get(key)
            if not field or field.compute or field.related:
                continue
            if field.type in ('one2many', 'many2many', 'binary', 'reference', 'properties'):
                continue
            if field.type == 'many2one':
                if isinstance(value, int) and not isinstance(value, bool):
                    clean[key] = value
                elif isinstance(value, (list, tuple)) and value and isinstance(value[0], int):
                    clean[key] = value[0]
                continue
            if isinstance(value, (dict, list, tuple)):
                continue
            # Reject OWL datapoint / virtual ids accidentally stored as strings
            if isinstance(value, str) and value.startswith('datapoint_'):
                continue
            clean[key] = value
        return clean

    @api.model
    def _prepare_registration_partner_vals(self, name, mobile, country_id, partner_vals=None):
        """Sanitize + defaults + create dry-run before any OTP is sent."""
        Partner = self.env['res.partner']
        if not country_id:
            raise UserError(_("Country is required before sending the OTP."))
        country = self.env['res.country'].browse(country_id)
        if not country.exists() or not country.phone_code:
            raise UserError(_(
                "A valid country with a phone country code is required."
            ))
        if not mobile:
            raise UserError(_("Phone number with country code is required."))

        normalized = Partner._gs_normalize_mobile_value(mobile, country=country)
        existing = Partner.sudo().search([
            '|', ('mobile', '=', normalized), ('phone', '=', normalized),
        ], limit=1)
        if existing and existing.gs_welcome_sent:
            raise UserError(_("This mobile number is already registered."))

        vals = self._sanitize_partner_create_vals(partner_vals)
        # Apply the same defaults create() would (receivable/payable accounts, etc.)
        default_fields = [
            fname for fname, field in Partner._fields.items()
            if field.required and not field.compute and fname not in (
                'id', 'create_uid', 'create_date', 'write_uid', 'write_date',
            )
        ]
        defaults = Partner.default_get(default_fields)
        vals = {**defaults, **vals}
        vals.update({
            'name': name or vals.get('name') or normalized,
            'mobile': normalized,
            'country_id': country.id,
        })

        missing = []
        for fname, field in Partner._fields.items():
            if not field.required or field.compute:
                continue
            if fname in (
                'id', 'create_uid', 'create_date', 'write_uid', 'write_date',
            ):
                continue
            value = vals.get(fname)
            if field.type == 'boolean':
                continue
            if value in (False, None, '', []):
                missing.append(field.string or fname)
        if missing:
            raise UserError(_(
                "Please fill in all required fields before sending the OTP:\n- %s"
            ) % '\n- '.join(missing))

        # Dry-run create so other constraints fail before OTP is sent
        class _DryRunRollback(Exception):
            pass

        try:
            with self.env.cr.savepoint():
                Partner.with_user(SUPERUSER_ID).sudo().with_context(
                    gs_skip_registration_otp=True,
                    gs_registration_verified=True,
                    gs_require_mobile=True,
                    tracking_disable=True,
                    mail_create_nolog=True,
                    mail_notrack=True,
                ).create(vals)
                raise _DryRunRollback()
        except _DryRunRollback:
            pass
        except UserError:
            raise
        except Exception as error:
            raise UserError(_(
                "Cannot create this contact yet: %s"
            ) % (error,)) from error

        return normalized, country, vals

    @api.model
    def _send_registration_otp(self, name, mobile, otp_code):
        Event = self.env['gs.whatsapp.event'].sudo()
        sent = Event.send_event(
            'otp_registration',
            False,
            {
                'otp_code': otp_code,
                'expires_in_minutes': OTP_TTL_MINUTES,
                'customer_name': name,
                'customer_phone': mobile,
            },
        )
        if not sent:
            raise UserError(_(
                "WhatsApp OTP could not be sent. "
                "Check the webhook URL in Settings → WhatsApp Loyalty and try again."
            ))
        return True

    @api.model
    def create_registration_challenge(self, name, mobile, country_id=False, partner_vals=None):
        """Validate contact vals, create pending OTP, send WhatsApp once."""
        normalized, country, partner_vals = self._prepare_registration_partner_vals(
            name, mobile, country_id, partner_vals=partner_vals,
        )

        existing = self.sudo().search([
            ('mobile', '=', normalized),
            ('purpose', '=', 'registration'),
            ('otp_expiry', '>', fields.Datetime.now()),
        ], limit=1, order='id desc')
        # Reuse a freshly sent OTP instead of firing another webhook on double-click
        if existing:
            sent_at = existing.otp_expiry - timedelta(minutes=OTP_TTL_MINUTES)
            if fields.Datetime.now() < sent_at + timedelta(seconds=60):
                if partner_vals:
                    existing.write({
                        'name': partner_vals.get('name') or existing.name,
                        'partner_vals_json': json.dumps(partner_vals),
                        'country_id': country.id,
                    })
                return existing

        # Drop older challenges for the same phone
        self.sudo().search([
            ('mobile', '=', normalized),
            ('purpose', '=', 'registration'),
        ]).unlink()

        otp = self._generate_code()
        pending = self.sudo().create({
            'name': partner_vals['name'],
            'mobile': normalized,
            'country_id': country.id,
            'partner_vals_json': json.dumps(partner_vals),
            'otp_code': otp,
            'otp_expiry': fields.Datetime.now() + timedelta(minutes=OTP_TTL_MINUTES),
            'purpose': 'registration',
        })
        try:
            self._send_registration_otp(pending.name, normalized, otp)
        except UserError:
            pending.unlink()
            raise
        return pending

    def resend(self):
        """Regenerate OTP on the same pending record and send once."""
        self.ensure_one()
        otp = self._generate_code()
        self.sudo().write({
            'otp_code': otp,
            'otp_expiry': fields.Datetime.now() + timedelta(minutes=OTP_TTL_MINUTES),
        })
        self._send_registration_otp(self.name, self.mobile, otp)
        return True

    def verify_and_create_partner(self, otp_input, welcome_points=0):
        self.ensure_one()
        code = str(otp_input or '').strip()
        if (not self.otp_code or self.otp_code != code
                or not self.otp_expiry
                or self.otp_expiry < fields.Datetime.now()):
            raise UserError(_("Invalid or expired OTP."))

        vals = {}
        if self.partner_vals_json:
            vals.update(json.loads(self.partner_vals_json))
        vals = self._sanitize_partner_create_vals(vals)
        # Re-apply defaults in case CoA/properties changed since OTP was sent
        Partner = self.env['res.partner']
        default_fields = [
            fname for fname, field in Partner._fields.items()
            if field.required and not field.compute and fname not in (
                'id', 'create_uid', 'create_date', 'write_uid', 'write_date',
            )
        ]
        vals = {**Partner.default_get(default_fields), **vals}
        vals.update({
            'name': vals.get('name') or self.name,
            'mobile': self.mobile,
        })
        if self.country_id:
            vals['country_id'] = self.country_id.id

        partner = Partner.with_user(SUPERUSER_ID).sudo().with_context(
            gs_skip_registration_otp=True,
            gs_registration_verified=True,
            gs_require_mobile=True,
        ).create(vals)
        partner.action_send_welcome(welcome_points=welcome_points)
        self.sudo().unlink()
        return partner
