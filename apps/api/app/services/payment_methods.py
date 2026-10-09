"""Single place that turns a POS payment into Alegra's `paymentMethod` (DIAN
"medio de pago"). paymentForm stays CASH (contado): POS sales are paid on the spot.

Configurable without code: each PaymentChannel carries `alegra_payment_method`
(edited in Medios de pago). Card channels (payment_type CARD, e.g. Datáfono) use
the card subtype the cashier picked at checkout instead — the cashier only ever
sees "Débito"/"Crédito", never these codes.
"""
from app.domain.enums import PaymentMethod

# Documented Alegra catalog values (developer.alegra.com/docs/colombia) that fit a POS.
ALEGRA_PAYMENT_METHODS: dict[str, str] = {
    "CASH": "Efectivo",
    "CREDIT_TRANSFER": "Transferencia crédito",
    "DEBIT_CARD": "Tarjeta débito",
    "CREDIT_CARD": "Tarjeta crédito",
    "BANK_DEPOSIT": "Consignación bancaria",
    "CHECK": "Cheque",
    "ACH_CREDIT": "Crédito ACH",
    "ACH_DEBIT": "Débito ACH",
}
CARD_TYPES = ("DEBIT", "CREDIT")
CARD_TYPE_METHODS = {"DEBIT": "DEBIT_CARD", "CREDIT": "CREDIT_CARD"}
CARD_TYPE_LABELS = {"DEBIT": "Débito", "CREDIT": "Crédito"}


def requires_card_type(payment_type: PaymentMethod | str | None) -> bool:
    return str(getattr(payment_type, "value", payment_type)) == PaymentMethod.CARD.value


def method_for_payment(payment, channel) -> tuple[str | None, str | None]:
    """(alegra paymentMethod, None) or (None, why it can't be determined). `channel` = the payment's
    EFFECTIVE channel (services/effective_payments), never the historical one."""
    if requires_card_type(channel.payment_type):
        if payment.card_type not in CARD_TYPE_METHODS:
            return None, f"{channel.name}: falta indicar si fue tarjeta Débito o Crédito"
        return CARD_TYPE_METHODS[payment.card_type], None
    if not channel.alegra_payment_method:
        return None, f"{channel.name}: el canal no tiene medio de pago DIAN configurado (Medios de pago)"
    return channel.alegra_payment_method, None
