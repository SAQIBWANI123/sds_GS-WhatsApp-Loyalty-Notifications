# -*- coding: utf-8 -*-
import json
import logging
import re

import requests

from odoo import api, fields, models
from odoo.tools import html_escape

_logger = logging.getLogger(__name__)

# Legacy single webhook (fallback if per-event URL is empty)
WEBHOOK_PARAM = 'gs_whatsapp_loyalty.webhook_url'
EXPIRY_DAYS_PARAM = 'gs_whatsapp_loyalty.expiry_alert_days'

POINTS_DECIMALS = 2

WEBHOOK_PARAM_BY_EVENT = {
    'otp_registration': 'gs_whatsapp_loyalty.webhook_url_otp_registration',
    'welcome': 'gs_whatsapp_loyalty.webhook_url_welcome',
    'post_purchase': 'gs_whatsapp_loyalty.webhook_url_post_purchase',
    'otp_redeem': 'gs_whatsapp_loyalty.webhook_url_otp_redeem',
    'redeem_success': 'gs_whatsapp_loyalty.webhook_url_redeem_success',
    'points_expiry': 'gs_whatsapp_loyalty.webhook_url_points_expiry',
}


def _camel(key):
    parts = key.split('_')
    return parts[0] + ''.join(p.title() for p in parts[1:])


def _clean(value):
    """Send 450 instead of 450.0, and 33.33 instead of 33.333333333333336.

    Point balances come from float fields, so binary rounding artefacts end up
    verbatim in the customer's WhatsApp message unless they are trimmed here.
    """
    if isinstance(value, bool) or not isinstance(value, float):
        return value
    value = round(value, POINTS_DECIMALS)
    return int(value) if value.is_integer() else value


# BusinessChat automations resolve every variable of the template, so the
# webhook must always carry the full set of keys even when an event does not
# use them. Missing keys make the automation silently render nothing.
PAYLOAD_DEFAULTS = {
    'otpCode': '',
    'expiresInMinutes': 0,
    'welcomePoints': 0,
    'pointsBalance': 0,
    'invoiceNumber': '',
    'invoiceDate': '',
    'invoiceUrl': '',
    'amountTotal': 0,
    'currency': '',
    'pointsToRedeem': 0,
    'pointsRedeemed': 0,
    'description': '',
    'expiringPoints': 0,
    'expiryDate': '',
    'daysRemaining': 0,
}

MESSAGE_TEMPLATES = {
    'otp_registration': 'رمز التحقق الخاص بك هو {otpCode}. صالح لمدة {expiresInMinutes} دقائق.',
    'welcome': 'أهلاً بك {customerName}! تم إضافة {welcomePoints} نقطة ترحيبية.'
               ' رصيدك الحالي {pointsBalance} نقطة.',
    'post_purchase': 'شكراً لشرائك! فاتورتك {invoiceNumber} بمبلغ {amountTotal} {currency}.'
                     ' رصيدك الحالي {pointsBalance} نقطة.',
    'otp_redeem': 'رمز تأكيد استرداد {pointsToRedeem} نقطة هو {otpCode}.'
                  ' صالح لمدة {expiresInMinutes} دقائق.',
    'redeem_success': 'تم استرداد {pointsRedeemed} نقطة بنجاح.'
                      ' رصيدك الحالي {pointsBalance} نقطة.',
    'points_expiry': 'تنبيه: {expiringPoints} نقطة ستنتهي صلاحيتها بتاريخ {expiryDate}'
                     ' (بعد {daysRemaining} أيام).',
}


class GsWhatsappEvent(models.Model):
    _name = 'gs.whatsapp.event'
    _description = 'WhatsApp Webhook Event Log'
    _order = 'id desc'

    event_type = fields.Selection([
        ('otp_registration', 'OTP - Registration'),
        ('welcome', 'Welcome'),
        ('post_purchase', 'Post Purchase'),
        ('otp_redeem', 'OTP - Redeem'),
        ('redeem_success', 'Redeem Success'),
        ('points_expiry', 'Points Expiry Alert'),
    ], required=True, index=True)
    partner_id = fields.Many2one('res.partner', index=True)
    phone = fields.Char()
    payload = fields.Text()
    payload_html = fields.Html(
        string='Payload Preview',
        compute='_compute_payload_display',
        sanitize=True,
    )
    payload_pretty = fields.Text(
        string='Payload JSON',
        compute='_compute_payload_display',
    )
    state = fields.Selection([
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
    ], default='failed', index=True)
    response_code = fields.Integer()
    response_body = fields.Text()
    response_pretty = fields.Text(
        string='Response',
        compute='_compute_payload_display',
    )

    PAYLOAD_LABELS = {
        'eventType': 'Event Type',
        'customerName': 'Customer Name',
        'customerPhone': 'Customer Phone',
        'otpCode': 'OTP Code',
        'expiresInMinutes': 'Expires In Minutes',
        'welcomePoints': 'Welcome Points',
        'pointsBalance': 'Points Balance',
        'invoiceNumber': 'Invoice Number',
        'invoiceDate': 'Invoice Date',
        'invoiceUrl': 'Invoice URL',
        'amountTotal': 'Amount Total',
        'currency': 'Currency',
        'pointsToRedeem': 'Points To Redeem',
        'pointsRedeemed': 'Points Redeemed',
        'description': 'Description',
        'expiringPoints': 'Expiring Points',
        'expiryDate': 'Expiry Date',
        'daysRemaining': 'Days Remaining',
        'msg': 'Message',
    }

    def _redact_otp(self, data):
        """Mask the OTP code before it is shown anywhere in the UI.

        The real code stays in the stored `payload` (needed by the actual
        send/verify flow) — only display surfaces (list/form/chatter) are
        redacted, so a code doesn't sit in clear text in front of anyone with
        access to this log, admins included.
        """
        if not isinstance(data, dict) or not data.get('otpCode'):
            return data
        redacted = dict(data)
        code = str(redacted['otpCode'])
        redacted['otpCode'] = ('*' * max(len(code) - 2, 0)) + code[-2:]
        return redacted

    def _pretty_json(self, raw):
        if not raw:
            return ''
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(data, dict):
                data = self._redact_otp(data)
            return json.dumps(data, ensure_ascii=False, indent=2)
        except (TypeError, ValueError, json.JSONDecodeError):
            return raw or ''

    def _payload_table_html(self, raw):
        if not raw:
            return ''
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError, json.JSONDecodeError):
            return '<pre>%s</pre>' % html_escape(raw or '')
        if not isinstance(data, dict):
            return '<pre>%s</pre>' % html_escape(self._pretty_json(raw))
        data = self._redact_otp(data)

        def is_empty(value):
            return value in (None, '', [], {}) or value == 0

        filled = [(k, v) for k, v in data.items() if not is_empty(v)]
        empty = [(k, v) for k, v in data.items() if is_empty(v)]
        rows = filled + empty
        html = [
            '<table class="o_list_table table table-sm table-hover mb-0">',
            '<tbody>',
        ]
        for key, value in rows:
            label = self.PAYLOAD_LABELS.get(key, key)
            display = '—' if is_empty(value) else value
            html.append(
                '<tr><th class="text-muted" style="width:30%%;white-space:nowrap;">%s</th>'
                '<td style="white-space:pre-wrap;word-break:break-word;">%s</td></tr>'
                % (html_escape(str(label)), html_escape(str(display)))
            )
        html.extend(['</tbody>', '</table>'])
        return ''.join(html)

    @api.depends('payload', 'response_body')
    def _compute_payload_display(self):
        for rec in self:
            rec.payload_pretty = rec._pretty_json(rec.payload)
            rec.response_pretty = rec._pretty_json(rec.response_body)
            rec.payload_html = rec._payload_table_html(rec.payload)

    @api.model
    def _normalize_phone(self, phone, country=None):
        """Normalize to BusinessChat format: 00 + country code + number.
        Saudi defaults: 05XXXXXXXX / 5XXXXXXXX -> 00966XXXXXXXXX
        """
        if not phone:
            return False
        raw = str(phone).strip()
        has_prefix = raw.startswith('+') or raw.startswith('00')
        digits = re.sub(r'\D', '', phone)
        if digits.startswith('00'):
            digits = digits[2:]
        if not has_prefix and country and country.phone_code:
            digits = '%s%s' % (country.phone_code, digits.lstrip('0'))
        elif digits.startswith('05') and len(digits) == 10:
            digits = '966' + digits[1:]
        elif digits.startswith('5') and len(digits) == 9:
            digits = '966' + digits
        return '00' + digits

    @api.model
    def _build_message(self, event_type, payload):
        template = MESSAGE_TEMPLATES.get(event_type)
        if not template:
            return ''
        try:
            message = template.format(**payload)
        except (KeyError, IndexError, ValueError):
            _logger.exception("gs_whatsapp_loyalty: cannot render message for %s", event_type)
            return ''
        # Appended rather than templated so the message never ends with a
        # dangling label when the invoice has no shareable URL.
        if payload.get('invoiceUrl'):
            message = '%s رابط الفاتورة: %s' % (message, payload['invoiceUrl'])
        return message

    @api.model
    def _prepare_event_log_vals(self, event_type, partner, data=None):
        data = dict(data or {})
        partner = partner or self.env['res.partner']
        phone = False
        name = ''
        if partner:
            preferred_phone = partner.phone or partner.mobile
            if partner.phone:
                country = partner.phone_country_id or partner.country_id
            else:
                country = partner.mobile_country_id or partner.country_id
            phone = self._normalize_phone(preferred_phone, country=country)
            name = partner.name or ''
        if not phone:
            phone = self._normalize_phone(
                data.pop('customer_phone', None) or data.pop('customerPhone', None)
            )
        if not name:
            name = data.pop('customer_name', None) or data.pop('customerName', None) or ''
        if not phone:
            return {}
        payload = {
            'eventType': event_type,
            'customerName': name,
            'customerPhone': phone,
        }
        payload.update(PAYLOAD_DEFAULTS)
        for key, value in data.items():
            payload[_camel(key)] = _clean(value)
        payload.setdefault('msg', '')
        if not payload['msg']:
            payload['msg'] = self._build_message(event_type, payload)
        return {
            'event_type': event_type,
            'partner_id': partner.id if partner else False,
            'phone': phone,
            'payload': json.dumps(payload, ensure_ascii=False, indent=2),
        }

    @api.model
    def queue_event(self, event_type, partner, data=None):
        log_vals = self._prepare_event_log_vals(event_type, partner, data=data)
        if not log_vals:
            _logger.warning("gs_whatsapp_loyalty: no phone for queued event %s", event_type)
            return False
        log_vals['state'] = 'pending'
        self.sudo().create(log_vals)
        return True

    @api.model
    def send_event(self, event_type, partner, data=None):
        """POST a flat camelCase JSON payload to the BusinessChat webhook.

        partner may be an empty recordset when sending registration OTP
        before the contact exists; then data must include customer_name /
        customer_phone (or snake_case equivalents).
        Never raises: failures are logged, business flow is not blocked.
        """
        icp = self.env['ir.config_parameter'].sudo()
        param_key = WEBHOOK_PARAM_BY_EVENT.get(event_type, WEBHOOK_PARAM)
        url = (icp.get_param(param_key) or '').strip()
        # Backward compatible fallback to the old single webhook URL
        if not url:
            url = (icp.get_param(WEBHOOK_PARAM) or '').strip()
        if not url:
            _logger.warning(
                "gs_whatsapp_loyalty: webhook URL not configured for %s (%s)",
                event_type,
                param_key,
            )
            return False

        log_vals = self._prepare_event_log_vals(event_type, partner, data=data)
        if not log_vals:
            _logger.warning("gs_whatsapp_loyalty: no phone for event %s", event_type)
            return False
        payload = json.loads(log_vals['payload'])
        try:
            resp = requests.post(url, json=payload, timeout=10)
            log_vals.update({
                'state': 'sent' if resp.ok else 'failed',
                'response_code': resp.status_code,
                'response_body': (resp.text or '')[:2000],
            })
        except Exception as e:
            _logger.exception("gs_whatsapp_loyalty: webhook POST failed for %s", event_type)
            log_vals['response_body'] = str(e)[:2000]

        try:
            self.sudo().create(log_vals)
        except Exception:
            _logger.exception("gs_whatsapp_loyalty: failed to write event log")
        return log_vals.get('state') == 'sent'

    @api.model
    def _cron_process_pending_events(self, limit=50):
        pending = self.sudo().search([('state', '=', 'pending')], order='id', limit=max(1, int(limit or 50)))
        icp = self.env['ir.config_parameter'].sudo()
        for event in pending:
            try:
                param_key = WEBHOOK_PARAM_BY_EVENT.get(event.event_type, WEBHOOK_PARAM)
                url = (icp.get_param(param_key) or '').strip() or (icp.get_param(WEBHOOK_PARAM) or '').strip()
                if not url:
                    event.write({
                        'state': 'failed',
                        'response_body': 'Webhook URL is not configured',
                    })
                    continue
                resp = requests.post(url, json=json.loads(event.payload or '{}'), timeout=10)
                event.write({
                    'state': 'sent' if resp.ok else 'failed',
                    'response_code': resp.status_code,
                    'response_body': (resp.text or '')[:2000],
                })
            except Exception as error:
                _logger.exception(
                    "gs_whatsapp_loyalty: queued webhook POST failed for %s", event.event_type
                )
                event.write({
                    'state': 'failed',
                    'response_body': str(error)[:2000],
                })
        return True
