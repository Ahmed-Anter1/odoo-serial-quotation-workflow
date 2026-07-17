from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    quotation_lot_ids = fields.Many2many(
        "stock.lot",
        "portfolio_sale_line_lot_rel",
        "sale_line_id",
        "lot_id",
        string="Quotation Serials",
        copy=False,
        domain="[('product_id', '=', product_id)]",
    )
    requires_serial = fields.Boolean(compute="_compute_requires_serial")

    @api.depends("product_id.tracking")
    def _compute_requires_serial(self):
        for line in self:
            line.requires_serial = line.product_id.tracking == "serial"

    @api.constrains("quotation_lot_ids", "product_uom_qty", "product_id")
    def _check_quotation_serials(self):
        for line in self:
            wrong_product = line.quotation_lot_ids.filtered(
                lambda lot: lot.product_id != line.product_id
            )
            if wrong_product:
                raise ValidationError(_("Every serial must belong to the selected product."))
            if line.requires_serial and line.quotation_lot_ids:
                required = int(line.product_uom_qty)
                if len(line.quotation_lot_ids) != required:
                    raise ValidationError(
                        _("Select exactly %(count)s serial number(s) for %(product)s.")
                        % {"count": required, "product": line.product_id.display_name}
                    )

    @api.constrains("quotation_lot_ids")
    def _check_duplicate_serials_in_order(self):
        for line in self.filtered("quotation_lot_ids"):
            duplicates = line.order_id.order_line.filtered(
                lambda other: other != line
                and bool(other.quotation_lot_ids & line.quotation_lot_ids)
            )
            if duplicates:
                raise ValidationError(_("A serial number cannot be selected twice in one order."))


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def action_confirm(self):
        for order in self:
            tracked = order.order_line.filtered(
                lambda line: line.requires_serial and line.product_uom_qty
            )
            missing = tracked.filtered(
                lambda line: len(line.quotation_lot_ids) != int(line.product_uom_qty)
            )
            if missing:
                raise ValidationError(
                    _("Complete serial selection for: %s")
                    % ", ".join(missing.mapped("product_id.display_name"))
                )
        result = super().action_confirm()
        self._apply_quotation_serials()
        return result

    def _apply_quotation_serials(self):
        for order in self:
            for picking in order.picking_ids.filtered(
                lambda record: record.state not in ("done", "cancel")
            ):
                picking.action_assign()
                for move in picking.move_ids.filtered("sale_line_id"):
                    lots = move.sale_line_id.quotation_lot_ids
                    if not lots:
                        continue
                    available_lines = move.move_line_ids.sorted("id")
                    if len(available_lines) < len(lots):
                        for _index in range(len(lots) - len(available_lines)):
                            self.env["stock.move.line"].create({
                                "move_id": move.id,
                                "picking_id": picking.id,
                                "product_id": move.product_id.id,
                                "product_uom_id": move.product_uom.id,
                                "location_id": move.location_id.id,
                                "location_dest_id": move.location_dest_id.id,
                                "quantity": 0.0,
                            })
                        available_lines = move.move_line_ids.sorted("id")
                    for move_line, lot in zip(available_lines, lots):
                        move_line.lot_id = lot
