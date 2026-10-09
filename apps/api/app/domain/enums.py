from enum import Enum


class SaleStatus(str, Enum):
    OPEN = "OPEN"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class PaymentMethod(str, Enum):
    CASH = "CASH"
    CARD = "CARD"
    TRANSFER = "TRANSFER"


class PaymentGroup(str, Enum):
    CASH = "CASH"
    ELECTRONIC = "ELECTRONIC"
    OTHER = "OTHER"


class SaleSource(str, Enum):
    POS_PRODUCT = "POS_PRODUCT"
    MANUAL_BULK = "MANUAL_BULK"
    MANUAL_SACK = "MANUAL_SACK"


class GranelSettlement(str, Enum):
    """How the comida a granel of a sale was settled. EXTERNAL_SCALE ("Báscula / efectivo"): already
    recorded by the external scale -> part of the customer's bill only, never a POS payment/cash/sale.
    POS ("Pago electrónico"): an ordinary POS line paid electronically."""
    EXTERNAL_SCALE = "EXTERNAL_SCALE"
    POS = "POS"


class ManualLineKind(str, Enum):
    """A line without catalog product added to an OPEN sale ("Producto manual").
    SUPER -> category SUPER. Mascotas sin código (category MASCOTAS_SIN_CODIGO): GRANEL (food sold by
    weight, the only kind tied to the scale), BULTO (food sold as a sack, normal POS sale) and
    PET_OTHER (bags, mats, accessories, pet treats without barcode... normal POS sale, never scale)."""
    SUPER = "SUPER"
    GRANEL = "GRANEL"
    BULTO = "BULTO"
    PET_OTHER = "PET_OTHER"


class ExpensePaymentSource(str, Enum):
    CASH = "CASH"
    ELECTRONIC = "ELECTRONIC"
    OTHER = "OTHER"


class InventoryMovementType(str, Enum):
    INITIAL_STOCK = "INITIAL_STOCK"
    PURCHASE = "PURCHASE"
    SALE = "SALE"
    RETURN = "RETURN"
    DAMAGE = "DAMAGE"
    ADJUSTMENT = "ADJUSTMENT"
    INTERNAL_CONSUMPTION = "INTERNAL_CONSUMPTION"
    # A product delivered as the replacement in a product exchange (never counted as a new SALE).
    EXCHANGE_OUT = "EXCHANGE_OUT"


class PurchaseStatus(str, Enum):
    DRAFT = "DRAFT"
    RECEIVED = "RECEIVED"
    CANCELLED = "CANCELLED"


class CashMovementType(str, Enum):
    SALE = "SALE"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    REFUND = "REFUND"
    ADJUSTMENT = "ADJUSTMENT"
    EXPENSE = "EXPENSE"
    SUPPLIER_PAYMENT = "SUPPLIER_PAYMENT"
    # Cash effect of a payment channel correction (cash <-> electronic), linked to the correction.
    PAYMENT_CORRECTION = "PAYMENT_CORRECTION"
    # The positive difference of a product exchange paid in cash (only the difference ever moves).
    EXCHANGE = "EXCHANGE"
    # The difference GIVEN BACK in cash in a product exchange (negative). Never a REFUND: a REFUND is the
    # money of a sale return; this is only the remainder of an exchange.
    EXCHANGE_REFUND = "EXCHANGE_REFUND"


class WithdrawalReason(str, Enum):
    HOME = "HOME"
    OTHER = "OTHER"


class ElectronicInvoiceProviderName(str, Enum):
    ALEGRA = "ALEGRA"


class ElectronicInvoiceStatus(str, Enum):
    """Our internal lifecycle. Deliberately separate from Alegra's own
    `provider_status` and from DIAN's `legal_status` — never conflate them."""
    PENDING = "PENDING"
    # Pre-flight found something missing (unmapped product, manual line, payment
    # method...). Nothing was sent to Alegra; retryable once it is fixed.
    BLOCKED = "BLOCKED"
    PROCESSING = "PROCESSING"
    ISSUED = "ISSUED"
    REJECTED = "REJECTED"
    ERROR = "ERROR"
