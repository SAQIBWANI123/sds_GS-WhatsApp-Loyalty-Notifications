{
    'name': 'GS WhatsApp Loyalty Notifications',
    'version': '19.0.1.0.0',
    'category': 'Marketing',
    'summary': 'Send loyalty lifecycle events to BusinessChat WhatsApp webhook',
    'description': """
Sends flat camelCase JSON events to BusinessChat webhook for 6 loyalty lifecycle cases:
1. otp_registration - OTP for mobile verification at signup (Contact/POS create)
2. welcome          - Welcome message after OTP confirmation
3. post_purchase    - Invoice notification + available points
4. otp_redeem       - OTP to confirm points redemption (POS)
5. redeem_success   - Points redemption success notification
6. points_expiry    - Daily cron: points expiring within 7 days
    """,
    'author': 'SmartDeskSolutions',
    'website': 'https://www.smartdesksolutions.com',
    'support': 'info@smartdesksolutions.com',
    'license': 'LGPL-3',
    'depends': ['account', 'loyalty', 'contacts'],
    'data': [
        'security/ir.model.access.csv',
        'data/config_parameter.xml',
        'data/ir_cron.xml',
        'views/whatsapp_event_views.xml',
        'views/whatsapp_event_test_views.xml',
        'views/otp_wizard_views.xml',
        'views/res_partner_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'gs_whatsapp_loyalty/static/src/js/partner_otp_form.js',
        ],
    },
    'installable': True,
    'application': False,
}
