# Copyright 2018 Open Source Integrators (http://www.opensourceintegrators.com)
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountPaymentRegister(models.TransientModel):
    _inherit = "account.payment.register"
    # override to make it read only as we're forcing amount changes on a per-invoice basis
    amount = fields.Monetary(currency_field='currency_id', store=True, readonly=True,
                             compute='_compute_amount')

    payment_line_ids = fields.One2many("account.payment.register.line", "register_id", string="Invoices to be paid.",
                                       readonly=False, compute="_compute_payment_lines")

    @api.depends('source_amount', 'source_amount_currency', 'source_currency_id', 'company_id', 'currency_id',
                 'payment_date', 'payment_line_ids.payment_amt')
    def _compute_amount(self):
        # TODO: fix for multi-currency invoice payments
        for wizard in self:
            wizard.amount = sum(line.payment_amt for line in wizard.payment_line_ids)

    @api.depends("line_ids")
    def _compute_payment_lines(self):
        for wizard in self:
            invoices = set()
            wizard.payment_line_ids = []
            for line in wizard.line_ids:
                invoices.add(line.move_id)
            wizard.payment_line_ids = \
                (wizard.env['account.payment.register.line'].
                 create({'invoice_id': inv.id,
                         'register_id': wizard.id,
                         'currency_id': wizard.currency_id,
                         'payment_amt': inv.amount_residual - inv.discount_amt,
                         } for inv in invoices))

    @api.depends("payment_date")
    def onchange_payment_date(self):
        for wizard in self:
            wizard.payment_difference_handling = "open"
            wizard.writeoff_account_id = False
            wizard.writeoff_label = False

            payment_amt = 0
            amount_difference = 0
            writeoff_account_id = False
            writeoff_label = False
            for line in wizard.payment_line_ids:
                line.onchange_payment_date()
                payment_amt += line.payment_amt
                amount_difference += line.discount_amt
                if line.discount_amt:
                    writeoff_label = "Payment Discount"
                    for term_line in line.invoice_id.invoice_payment_term_id.line_ids:
                        # TODO: allow for terms with different writeoff accounts
                        writeoff_account_id = term_line.discount_expense_account_id
            wizard.payment_difference = amount_difference
            amount_residual = sum({line.invoice_id.amount_residual for line in wizard.payment_line_ids})
            wizard.amount = amount_residual - wizard.payment_difference
            wizard.writeoff_account_id = writeoff_account_id
            wizard.writeoff_label = writeoff_label

    def action_create_payments(self):
        active_id = self.env.context.get("active_ids", [])

        if (not isinstance(active_id, int)) and len(active_id) != 1:
            # For multiple invoices, there is account.register.payments wizard
            raise UserError(
                _(
                    "This method should only be called to process a "
                    "single invoice's payment."
                )
            )
        res = super().action_create_payments()
        for payment in self:
            if payment.payment_difference_handling == "reconcile":
                payment.invoice_id.write(
                    {
                        "discount_taken": abs(payment.payment_difference),
                        "discount_amt": 0,
                    }
                )
        return res


class AccountPaymentRegisterLine(models.TransientModel):
    _name = 'account.payment.register.line'
    invoice_id = fields.Many2one(comodel_name="account.move", string="Invoice")
    register_id = fields.Many2one(comodel_name='account.payment.register', string="Payment Register Wizard")
    currency_id = fields.Many2one('res.currency', related="register_id.currency_id")
    payment_amt = fields.Monetary(store=True, string='Payment')
    discount_amt = fields.Monetary(store=True, compute="_compute_discount", inverse='_inverse_discount_amt',
                                   string='Discount')
    discount_pct = fields.Float(store=True, compute="_compute_discount", inverse='_inverse_discount_pct',
                                string='Discount %')

    ref = fields.Char(related="invoice_id.ref", string="Invoice")
    invoice_date = fields.Date(related="invoice_id.invoice_date", string="Invoice Date")
    amount_total = fields.Monetary(related="invoice_id.amount_total", string="Invoice Total")

    # @api.onchange("payment_amt")
    # def _onchange_payment_amt(self):
    #     for line in self:
    #         if line.payment_amt:
    #             if line.payment_amt < 0:
    #                 raise UserError(_("Cannot set a negative payment amount."))
    #             line.discount_amt = line.amount_total - line.payment_amt
    #             line.discount_pct = line.discount_amt / line.amount_total
    #             line.register_id._compute_amount()
    #     pass
    #
    # @api.onchange("discount_amt")
    #
    # def _onchange_discount_amt(self):
    #     for line in self:
    #         if line.discount_amt:
    #             if line.discount_amt < 0 or line.discount_amt > line.amount_total:
    #                 raise UserError(_("Discount amount must be between 0 and the invoice total."))
    #             line.payment_amt = line.amount_total - line.discount_amt
    #             line.discount_pct = line.discount_amt / line.amount_total
    #             line.register_id._compute_amount()
    #     pass
    #
    # @api.onchange("discount_pct")
    # @_semaphore
    # def _onchange_discount_pct(self):
    #     for line in self:
    #         if line.discount_pct:
    #             if line.discount_pct < 0 or line.discount_pct > 1:
    #                 raise UserError(_("Discount % must be between 0 and 100."))
    #             line.discount_amt = line.amount_total * line.discount_pct
    #             line.payment_amt = line.amount_total - line.discount_amt
    #             line.register_id._compute_amount()
    #     pass


    @api.depends('payment_amt')
    def _compute_discount(self):
        print("In _compute_discount")
        for line in self:
            line.discount_amt = line.amount_total - line.payment_amt
            line.discount_pct = 100 * line.discount_amt / line.amount_total

    @api.onchange('discount_pct')
    def _inverse_discount_pct(self):
        print("In _inverse_discount_pct")
        for line in self:
            if line.discount_pct:
                if line.discount_pct < 0 or line.discount_pct > 100:
                    raise UserError(_("Discount percentage must be between 0% and 100%."))
                line.payment_amt = line.amount_total * (1 - line.discount_pct / 100)
    @api.onchange('discount_amt')
    def _inverse_discount_amt(self):
        print("In _invserse_discount_amt")
        for line in self:
            if line.discount_amt:
                if line.discount_amt < 0 or line.discount_amt > line.amount_total:
                    raise UserError(_("Discount must be between 0 and the invoice total."))
                line.payment_amt = line.amount_total - line.discount_amt

    def onchange_payment_date(self):
        for line in self:
            payment_date = fields.Date.from_string(line.register_id.payment_date)
            line.discount_amt = line.invoice_id.invoice_payment_term_id._check_payment_term_discount(line.invoice_id,
                                                                                                     payment_date)[0]


