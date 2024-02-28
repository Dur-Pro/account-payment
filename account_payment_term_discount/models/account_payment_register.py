from odoo import api, fields, models


class AccountPaymentRegister(models.TransientModel):
    _inherit = 'account.payment.register'

    def _get_payment_vals(self):
        res = super()._get_payment_vals()
        if self.payment_term_id:
            res['payment_term_id'] = self.payment_term_id.id
        return res