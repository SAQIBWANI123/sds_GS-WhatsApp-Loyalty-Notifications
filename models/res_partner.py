import re
import secrets
from datetime import timedelta

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError

OTP_TTL_MINUTES = 5


class ResPartner(models.Model):
    _inherit = 'res.partner'

    mobile = fields.Char(
        string='Mobile',
        index=True,
    )
    gs_otp_code = fields.Char(copy=False)
    gs_otp_expiry = fields.Datetime(copy=False)
    gs_otp_purpose = fields.Selection([
        ('registration', 'Registration'),
        ('redeem', 'Redeem'),
    ], copy=False)
    gs_welcome_sent = fields.Boolean(copy=False)
    gs_otp_pending = fields.Boolean(
        compute='_compute_gs_otp_pending',
        help='True while a WhatsApp OTP is waiting for confirmation.',
    )
    mobile_country_id = fields.Many2one(
        'res.country',
        string='Mobile Country Code',
        compute='_compute_mobile_parts',
        inverse='_inverse_mobile_parts',
        readonly=False,
        store=False,
        domain=[('phone_code', '!=', False)],
    )
    mobile_local_number = fields.Char(
        string='Mobile Number',
        compute='_compute_mobile_parts',
        inverse='_inverse_mobile_parts',
        readonly=False,
        store=False,
    )
    phone_country_id = fields.Many2one(
        'res.country',
        string='Phone Country Code',
        compute='_compute_phone_parts',
        inverse='_inverse_phone_parts',
        readonly=False,
        store=False,
        domain=[('phone_code', '!=', False)],
    )
    phone_local_number = fields.Char(
        string='Phone Number',
        compute='_compute_phone_parts',
        inverse='_inverse_phone_parts',
        readonly=False,
        store=False,
    )

    @api.depends('gs_otp_code', 'gs_otp_purpose', 'gs_otp_expiry')
    def _compute_gs_otp_pending(self):
        now = fields.Datetime.now()
        for partner in self:
            partner.gs_otp_pending = bool(
                partner.gs_otp_code
                and partner.gs_otp_purpose
                and partner.gs_otp_expiry
                and partner.gs_otp_expiry >= now
            )

    @api.depends('mobile', 'country_id')
    def _compute_mobile_parts(self):
        countries = self.env['res.country'].search([
            ('phone_code', '!=', False),
            ('phone_code', '!=', 0),
        ])
        countries = countries.sorted(
            key=lambda country: len(str(country.phone_code)),
            reverse=True,
        )
        for partner in self:
            digits = re.sub(r'\D', '', partner.mobile or '')
            international = (partner.mobile or '').strip().startswith(('+', '00'))
            if digits.startswith('00'):
                digits = digits[2:]

            country = False
            local_number = digits
            if international:
                country = next(
                    (
                        item for item in countries
                        if digits.startswith(str(item.phone_code))
                    ),
                    False,
                )
                if country:
                    local_number = digits[len(str(country.phone_code)):]
            else:
                country = partner.country_id

            partner.mobile_country_id = country
            partner.mobile_local_number = local_number

    def _inverse_mobile_parts(self):
        for partner in self:
            country = partner.mobile_country_id
            local_number = re.sub(r'\D', '', partner.mobile_local_number or '')
            if country and country.phone_code and local_number:
                partner.mobile = '+%s%s' % (
                    country.phone_code,
                    local_number.lstrip('0'),
                )
            else:
                partner.mobile = local_number or False

    @api.depends('phone', 'country_id')
    def _compute_phone_parts(self):
        countries = self.env['res.country'].search([
            ('phone_code', '!=', False),
            ('phone_code', '!=', 0),
        ])
        countries = countries.sorted(
            key=lambda country: len(str(country.phone_code)),
            reverse=True,
        )
        for partner in self:
            digits = re.sub(r'\D', '', partner.phone or '')
            international = (partner.phone or '').strip().startswith(('+', '00'))
            if digits.startswith('00'):
                digits = digits[2:]

            country = False
            local_number = digits
            if international:
                country = next(
                    (
                        item for item in countries
                        if digits.startswith(str(item.phone_code))
                    ),
                    False,
                )
                if country:
                    local_number = digits[len(str(country.phone_code)):]
            else:
                country = partner.country_id

            partner.phone_country_id = country
            partner.phone_local_number = local_number

    def _inverse_phone_parts(self):
        for partner in self:
            country = partner.phone_country_id
            local_number = re.sub(r'\D', '', partner.phone_local_number or '')
            if country and country.phone_code and local_number:
                partner.phone = '+%s%s' % (
                    country.phone_code,
                    local_number.lstrip('0'),
                )
            else:
                partner.phone = local_number or False

    def _gs_apply_country_code_to_phones(self):
        """Prefix local phone/mobile with the contact's country dial code."""
        for partner in self:
            for fname, country_field in (
                ('phone', 'phone_country_id'),
                ('mobile', 'mobile_country_id'),
            ):
                country = partner[country_field] or partner.country_id
                if not country or not country.phone_code:
                    continue
                value = (partner[fname] or '').strip()
                if not value or value.startswith('+') or value.startswith('00'):
                    continue
                digits = re.sub(r'\D', '', value)
                local = digits.lstrip('0')
                if local:
                    partner[fname] = '+%s%s' % (country.phone_code, local)

    @api.onchange('country_id')
    def _onchange_gs_country_phone_code(self):
        if self.country_id and self.country_id.phone_code:
            self.phone_country_id = self.country_id
            self.mobile_country_id = self.country_id
        self._gs_apply_country_code_to_phones()

    @api.onchange('phone', 'mobile')
    def _onchange_gs_phone_add_country_code(self):
        self._gs_apply_country_code_to_phones()

    @api.model
    def _gs_normalize_mobile_value(self, mobile, country=None):
        """Return E.164 mobile (+<code><number>) or raise UserError."""
        mobile = (mobile or '').strip()
        if not mobile:
            raise UserError(_(
                "Phone number with country code is required."
            ))

        has_prefix = mobile.startswith('+') or mobile.startswith('00')
        digits = re.sub(r'\D', '', mobile)
        if digits.startswith('00'):
            digits = digits[2:]

        if has_prefix:
            if len(digits) < 8:
                raise UserError(_(
                    "Phone number with country code is required."
                ))
            return '+%s' % digits

        if not country or not country.phone_code:
            raise UserError(_(
                "Country is required and phone country code is required."
            ))
        local = digits.lstrip('0')
        if not local:
            raise UserError(_(
                "Phone number with country code is required."
            ))
        return '+%s%s' % (country.phone_code, local)

    @api.model
    def _gs_prepare_partner_vals(self, vals):
        """Normalize mobile when provided; do not block XML/system creates.

        Phone is required for loyalty OTP registration via the form/wizard/
        portal — not for every res.partner create (module data, cash client,
        companies, etc.).
        """
        vals = dict(vals)
        if self.env.context.get('gs_skip_mobile_required'):
            return vals

        parent_id = vals.get('parent_id')
        contact_type = vals.get('type', 'contact')
        if parent_id or contact_type != 'contact':
            return vals

        mobile = vals.get('mobile')
        phone = vals.get('phone')
        raw = mobile if mobile is not None else phone
        # No phone fields in vals, or empty: leave as-is unless OTP flow requires it
        if raw is None or raw is False or (isinstance(raw, str) and not raw.strip()):
            if self.env.context.get('gs_require_mobile'):
                raise UserError(_(
                    "Phone number with country code is required."
                ))
            return vals

        country = False
        country_id = vals.get('mobile_country_id') or vals.get('country_id')
        if country_id:
            country = self.env['res.country'].browse(country_id)

        # Generic Odoo/module data can contain local-format phone numbers
        # without a country. Only OTP registration must enforce E.164.
        has_prefix = isinstance(raw, str) and (
            raw.strip().startswith('+') or raw.strip().startswith('00')
        )
        if not country and not has_prefix and not self.env.context.get('gs_require_mobile'):
            return vals

        normalized = self._gs_normalize_mobile_value(raw, country=country)
        if vals.get('mobile') is not None or not vals.get('phone'):
            vals['mobile'] = normalized
        else:
            vals['phone'] = normalized
            vals.setdefault('mobile', normalized)
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        prepared = [self._gs_prepare_partner_vals(vals) for vals in vals_list]
        partners = super().create(prepared)
        # Registration OTP is confirmed BEFORE create. After verified create,
        # optionally send welcome when explicitly marked.
        if self.env.context.get('gs_registration_verified'):
            for partner in partners.filtered(lambda p: not p.gs_welcome_sent):
                # Welcome is usually sent by the wizard/portal after create;
                # keep this as a safety net only when requested.
                if self.env.context.get('gs_send_welcome_on_create'):
                    partner.action_send_welcome()
        return partners

    def write(self, vals):
        if 'mobile' in vals or 'phone' in vals:
            for partner in self:
                if partner.parent_id or partner.type != 'contact':
                    continue
                merged = {
                    'mobile': vals['mobile'] if 'mobile' in vals else partner.mobile,
                    'phone': vals['phone'] if 'phone' in vals else partner.phone,
                    'country_id': vals.get('country_id', partner.country_id.id),
                    'parent_id': False,
                    'type': 'contact',
                }
                if 'mobile_country_id' in vals:
                    merged['mobile_country_id'] = vals['mobile_country_id']
                elif 'mobile_country_id' in partner._fields and partner.mobile_country_id:
                    merged['mobile_country_id'] = partner.mobile_country_id.id
                normalized = self._gs_prepare_partner_vals(merged)
                if 'mobile' in vals:
                    vals = dict(vals, mobile=normalized.get('mobile'))
                elif 'phone' in vals:
                    vals = dict(vals, phone=normalized.get('phone') or normalized.get('mobile'))
            # Changing the verified channel must invalidate any prior "recent
            # OTP" state used to authorize POS point redemption (see
            # nma_pos_loyalty_points). Otherwise a cashier could retarget an
            # existing customer's mobile number to their own, verify a fresh
            # OTP against it, and redeem that customer's banked points.
            if 'nma_redeem_otp_verified_at' in self._fields:
                vals = dict(vals, nma_redeem_otp_verified_at=False)
        return super().write(vals)

    def _gs_generate_otp(self, purpose):
        self.ensure_one()
        otp = str(secrets.randbelow(900000) + 100000)
        # Public portal (auth=none) has an empty env.user; write as superuser.
        self.with_user(SUPERUSER_ID).sudo().write({
            'gs_otp_code': otp,
            'gs_otp_expiry': fields.Datetime.now() + timedelta(minutes=OTP_TTL_MINUTES),
            'gs_otp_purpose': purpose,
        })
        return otp

    def _gs_otp_sent_recently(self, purpose='registration', cooldown_seconds=60):
        """True if a still-valid OTP was generated within the cooldown window."""
        self.ensure_one()
        if (
            self.gs_otp_purpose != purpose
            or not self.gs_otp_code
            or not self.gs_otp_expiry
            or self.gs_otp_expiry < fields.Datetime.now()
        ):
            return False
        sent_at = self.gs_otp_expiry - timedelta(minutes=OTP_TTL_MINUTES)
        return fields.Datetime.now() < sent_at + timedelta(seconds=cooldown_seconds)

    def action_send_registration_otp(self):
        Event = self.env['gs.whatsapp.event']
        for partner in self:
            if not partner.mobile and not partner.phone:
                raise UserError(_(
                    "Phone number with country code is required."
                ))
            # Ignore rapid double-clicks / concurrent portal posts
            if partner._gs_otp_sent_recently('registration'):
                continue
            otp = partner._gs_generate_otp('registration')
            Event.send_event('otp_registration', partner, {
                'otp_code': otp,
                'expires_in_minutes': OTP_TTL_MINUTES,
            })
        return True

    def action_send_redeem_otp(self, points_to_redeem=0):
        """Returns whether every recipient was reached, so the POS can tell the
        cashier that no code is coming instead of asking for one."""
        Event = self.env['gs.whatsapp.event']
        sent = True
        for partner in self:
            otp = partner._gs_generate_otp('redeem')
            sent &= bool(Event.send_event('otp_redeem', partner, {
                'otp_code': otp,
                'points_to_redeem': points_to_redeem,
                'expires_in_minutes': OTP_TTL_MINUTES,
            }))
        return sent

    def gs_verify_otp(self, code, purpose):
        """Returns True and clears the OTP if valid; raises otherwise."""
        self.ensure_one()
        rec = self.with_user(SUPERUSER_ID).sudo()
        code = str(code or '').strip()
        if (not rec.gs_otp_code or rec.gs_otp_code != code
                or rec.gs_otp_purpose != purpose
                or not rec.gs_otp_expiry
                or rec.gs_otp_expiry < fields.Datetime.now()):
            raise UserError(_("Invalid or expired OTP."))
        rec.write({
            'gs_otp_code': False,
            'gs_otp_expiry': False,
            'gs_otp_purpose': False,
        })
        return True

    def _gs_resolve_welcome_points(self, welcome_points=0):
        """Prefer explicit points; otherwise use POS registration grant setting."""
        points = float(welcome_points or 0)
        if points > 0:
            return points
        return float(
            self.env['ir.config_parameter'].sudo().get_param(
                'nma_pos_loyalty_points.grant_points', '0'
            ) or 0
        )

    def _gs_grant_welcome_points(self, points):
        """Create a loyalty card with welcome points if the partner has none yet."""
        self.ensure_one()
        points = float(points or 0)
        if points <= 0:
            return self.env['loyalty.card']
        program = self.env['loyalty.program'].sudo().search(
            [('program_type', '=', 'loyalty')], limit=1
        )
        if not program:
            return self.env['loyalty.card']
        Card = self.env['loyalty.card'].sudo()
        card = Card.search([
            ('partner_id', '=', self.id),
            ('program_id', '=', program.id),
        ], limit=1)
        if card:
            return card
        return Card.with_context(gs_skip_redeem_notify=True).create({
            'points': points,
            'program_id': program.id,
            'partner_id': self.id,
            'expiration_date': fields.Date.today() + timedelta(days=365),
        })

    def action_send_welcome(self, welcome_points=0):
        """Grant welcome points first, then notify with the real balance."""
        Event = self.env['gs.whatsapp.event']
        for partner in self.filtered(lambda p: not p.gs_welcome_sent):
            points = partner._gs_resolve_welcome_points(welcome_points)
            partner._gs_grant_welcome_points(points)
            balance = partner._gs_loyalty_balance()
            Event.send_event('welcome', partner, {
                'welcome_points': points or balance,
                'points_balance': balance,
            })
            partner.with_user(SUPERUSER_ID).sudo().write({'gs_welcome_sent': True})
        return True

    def action_confirm_registration_otp(self, code, welcome_points=0):
        """Verify registration OTP then send welcome."""
        self.ensure_one()
        self.gs_verify_otp(code, 'registration')
        self.action_send_welcome(welcome_points=welcome_points)
        return True

    def action_open_gs_otp_wizard(self):
        self.ensure_one()
        return {
            'name': _('Confirm WhatsApp OTP'),
            'type': 'ir.actions.act_window',
            'res_model': 'gs.whatsapp.otp.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_partner_id': self.id,
                'default_purpose': self.gs_otp_purpose or 'registration',
            },
        }

    def _gs_loyalty_balance(self):
        self.ensure_one()
        cards = self.env['loyalty.card'].sudo().search([
            ('partner_id', '=', self.id),
            ('program_id.program_type', '=', 'loyalty'),
        ])
        return sum(cards.mapped('points'))

    @api.model
    def gs_validate_registration_mobile(self, mobile, country_id):
        """Validate phone + country before opening OTP wizard / sending OTP."""
        if not country_id:
            raise UserError(_("Country is required before sending the OTP."))
        country = self.env['res.country'].browse(int(country_id))
        if not country.exists() or not country.phone_code:
            raise UserError(_(
                "A valid country with a phone country code is required."
            ))
        return self._gs_normalize_mobile_value(mobile, country=country)

    @api.model
    def gs_pos_start_registration(self, vals):
        """Send registration OTP without creating the partner yet."""
        try:
            vals = dict(vals or {})
            name = vals.get('name') or vals.get('display_name')
            mobile = vals.get('mobile') or vals.get('phone')
            country_id = vals.get('country_id') or vals.get('mobile_country_id') or False
            if isinstance(country_id, (list, tuple)):
                country_id = country_id[0] if country_id else False
            pending = self.env['gs.whatsapp.pending.otp'].create_registration_challenge(
                name=name,
                mobile=mobile,
                country_id=country_id,
                partner_vals=vals,
            )
            return {
                'ResponseStatus': 'success',
                'pending_id': pending.id,
                'mobile': pending.mobile,
            }
        except Exception as error:
            return {
                'ResponseStatus': 'fail',
                'Error': {
                    'MessageEn': str(error),
                    'MessageAr': str(error),
                },
            }

    @api.model
    def gs_pos_confirm_registration(self, pending_id, otp_code, welcome_points=0):
        """Verify OTP then create the partner and send welcome."""
        try:
            pending = self.env['gs.whatsapp.pending.otp'].browse(int(pending_id)).exists()
            if not pending:
                return {
                    'ResponseStatus': 'fail',
                    'Error': {
                        'MessageEn': 'OTP session expired. Please resend OTP.',
                        'MessageAr': 'انتهت جلسة رمز التحقق. أعد الإرسال.',
                    },
                }
            partner = pending.verify_and_create_partner(
                otp_code,
                welcome_points=welcome_points,
            )
            return {
                'ResponseStatus': 'success',
                'partner_id': partner.id,
            }
        except Exception as error:
            return {
                'ResponseStatus': 'fail',
                'otp_verified': 'fail',
                'Error': {
                    'MessageEn': str(error),
                    'MessageAr': str(error),
                },
            }

    @api.model
    def gs_pos_send_redeem_otp(self, partner_id, points_to_redeem=0):
        partner = self.browse(int(partner_id)).exists()
        if not partner:
            return {
                'ResponseStatus': 'fail',
                'Error': {
                    'MessageEn': 'Partner not found',
                    'MessageAr': 'العميل غير موجود',
                },
            }
        try:
            if not partner.action_send_redeem_otp(points_to_redeem=points_to_redeem):
                return {
                    'ResponseStatus': 'fail',
                    'Error': {
                        'MessageEn': 'WhatsApp OTP could not be delivered.',
                        'MessageAr': 'تعذر إرسال رمز التحقق عبر واتساب.',
                    },
                }
            return {'ResponseStatus': 'success'}
        except Exception as error:
            return {
                'ResponseStatus': 'fail',
                'Error': {
                    'MessageEn': str(error),
                    'MessageAr': str(error),
                },
            }

    @api.model
    def gs_pos_verify_otp(self, partner_id, code, purpose='redeem'):
        partner = self.browse(int(partner_id)).exists()
        if not partner:
            return {
                'ResponseStatus': 'fail',
                'Error': {
                    'MessageEn': 'Partner not found',
                    'MessageAr': 'العميل غير موجود',
                },
            }
        try:
            if purpose == 'registration':
                partner.action_confirm_registration_otp(code)
            else:
                partner.gs_verify_otp(code, purpose)
                if purpose == 'redeem' and 'nma_redeem_otp_verified_at' in partner._fields:
                    partner.sudo().write({
                        'nma_redeem_otp_verified_at': fields.Datetime.now(),
                    })
            return {'ResponseStatus': 'success'}
        except Exception as error:
            return {
                'ResponseStatus': 'fail',
                'otp_verified': 'fail',
                'Error': {
                    'MessageEn': str(error),
                    'MessageAr': str(error),
                },
            }
