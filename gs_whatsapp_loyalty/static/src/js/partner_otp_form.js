/** @odoo-module **/

import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";
import { _t } from "@web/core/l10n/translation";

const SAFE_FIELD_TYPES = new Set([
    "char",
    "text",
    "html",
    "boolean",
    "integer",
    "float",
    "monetary",
    "selection",
    "date",
    "datetime",
    "many2one",
]);

function _m2oId(value) {
    if (!value) {
        return false;
    }

    if (Array.isArray(value)) {
        return value[0] || false;
    }

    if (typeof value === "object") {
        const id = value.id;
        return typeof id === "number" ? id : false;
    }

    if (typeof value === "number") {
        return value;
    }

    return false;
}

/**
 * New contacts are NOT created until WhatsApp OTP is confirmed.
 * Save on a new res.partner opens the OTP wizard with the form values.
 */
patch(FormController.prototype, {
    async saveButtonClicked(params = {}) {
        if (
            this.props.resModel !== "res.partner"
            || !this.model.root.isNew
        ) {
            return super.saveButtonClicked(...arguments);
        }

        const data = this.model.root.data || {};

        if (
            data.parent_id
            || (data.type && data.type !== "contact")
        ) {
            return super.saveButtonClicked(...arguments);
        }

        // Vendors / appointment suppliers are not loyalty customers.
        //
        // Odoo vendor actions use:
        // res_partner_search_mode: 'supplier'
        //
        // supplier_rank and is_appointment_supplier are kept as
        // fallback checks for other entry points.
        const context =
            this.props.context
            || this.model.root.context
            || {};

        const supplierRank = Number(data.supplier_rank || 0);

        if (
            context.res_partner_search_mode === "supplier"
            || supplierRank > 0
            || data.is_appointment_supplier
        ) {
            return super.saveButtonClicked(...arguments);
        }

        let mobile =
            data.mobile
            || data.phone
            || data.mobile_local_number;

        if (
            data.is_company
            && !mobile
            && !data.mobile_local_number
        ) {
            return super.saveButtonClicked(...arguments);
        }

        // Check required fields before sending OTP.
        const isValid = await this.model.root.checkValidity();

        if (!isValid) {
            this.model.notify();

            this.env.services.notification.add(
                _t(
                    "Please fill in all required fields before sending the OTP."
                ),
                {
                    type: "danger",
                }
            );

            return false;
        }

        const name = data.name;

        const countryId = _m2oId(data.country_id);
        const mobileCountryId = _m2oId(data.mobile_country_id);

        const effectiveCountryId =
            mobileCountryId || countryId;

        mobile =
            data.mobile
            || data.phone
            || data.mobile_local_number;

        if (
            !name
            || !effectiveCountryId
            || !mobile
        ) {
            this.env.services.notification.add(
                _t(
                    "Name, country and mobile are required before sending the OTP."
                ),
                {
                    type: "danger",
                }
            );

            return false;
        }

        const fields =
            this.model.root.fields
            || this.props.fields
            || {};

        const partnerVals = {};

        for (const [key, value] of Object.entries(data)) {
            const field = fields[key];

            if (
                !field
                || !SAFE_FIELD_TYPES.has(field.type)
            ) {
                continue;
            }

            if (
                value === undefined
                || value === null
            ) {
                continue;
            }

            if (field.type === "many2one") {
                const id = _m2oId(value);

                if (id) {
                    partnerVals[key] = id;
                }

                continue;
            }

            if (typeof value === "object") {
                continue;
            }

            partnerVals[key] = value;
        }

        partnerVals.name = name;
        partnerVals.mobile = mobile;
        partnerVals.country_id = effectiveCountryId;

        if (mobileCountryId) {
            partnerVals.mobile_country_id =
                mobileCountryId;
        }

        // Send OTP once on the server, then open the
        // wizard already linked to that pending OTP.
        try {
            const action = await this.env.services.orm.call(
                "gs.whatsapp.otp.wizard",
                "action_start_registration",
                [
                    name,
                    mobile,
                    effectiveCountryId,
                    JSON.stringify(partnerVals),
                ]
            );

            await this.env.services.action.doAction(action);
        } catch (error) {
            const message =
                error?.data?.message
                || error?.message
                || _t("Could not send WhatsApp OTP.");

            this.env.services.notification.add(
                message,
                {
                    type: "danger",
                }
            );

            return false;
        }

        return false;
    },
});