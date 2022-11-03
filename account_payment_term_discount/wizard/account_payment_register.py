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
            if len(wizard.line_ids) > 1:
                self.group_payment = True
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
        print("in onchange payment date")
        for wizard in self:
            wizard.payment_difference_handling = "reconcile" if any(wizard.payment_line_ids.reconcile) else "open"
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


    def _init_payments(self, to_process, edit_mode=False):
        """ Create the payments.

        :param to_process:  A list of python dictionary, one for each payment to create, containing:
                            * create_vals:  The values used for the 'create' method.
                            * to_reconcile: The journal items to perform the reconciliation.
                            * batch:        A python dict containing everything you want about the source journal items
                                            to which a payment will be created (see '_get_batches').
        :param edit_mode:   Is the wizard in edition mode.
        """
        # remove any lines that are marked as reconcile = false from the list of lines to use for reconciliation
        to_remove = [line for line in self.payment_line_ids if not line.reconcile]
        for x in to_remove:
            to_process['to_reconcile'].remove(x)
        # TODO: Fix what is probably a blank amount_difference field
        return super()._init_payments(to_process, edit_mode)



    def action_create_payments(self):
        res = super().action_create_payments()

        for payment in self.payment_line_ids:
            if payment.reconcile:
                payment.invoice_id.write(
                    {
                        "discount_taken": abs(payment.discount_amt),
                        "discount_amt": 0,
                    }
                )
        return res

    @api.depends('line_ids')
    def _compute_from_lines(self):
        super()._compute_from_lines()
        # We will deactivate the editing of the global amount field but want the calculations taking place later
        # to consider that the amounts were editable (since we make the amounts editable on each line of a multi-line
        # payment.
        for wizard in self:
            wizard.can_edit_wizard = True


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
    reconcile = fields.Boolean(store=True, default=True)

    @api.depends('payment_amt')
    def _compute_discount(self):
        for line in self:
            line.discount_amt = line.amount_total - line.payment_amt
            line.discount_pct = 100 * line.discount_amt / line.amount_total

    @api.onchange('discount_pct')
    def _inverse_discount_pct(self):
        for line in self:
            if line.discount_pct:
                if line.discount_pct < 0 or line.discount_pct > 100:
                    raise UserError(_("Discount percentage must be between 0% and 100%."))
                line.payment_amt = line.amount_total * (1 - line.discount_pct / 100)
    @api.onchange('discount_amt')
    def _inverse_discount_amt(self):
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
    @api.onchange("reconcile")
    def onchange_reconcile(self):
        for line in self:
            line.register_id.onchange_payment_date()

