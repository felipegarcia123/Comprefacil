import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.db import Base
from app.domain.enums import (CashMovementType, ElectronicInvoiceProviderName,
                              ElectronicInvoiceStatus, ExpensePaymentSource,
                              InventoryMovementType, PaymentGroup, PaymentMethod,
                              PurchaseStatus, SaleSource, SaleStatus, WithdrawalReason)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120))


class Supplier(Base):
    __tablename__ = "suppliers"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), unique=True)
    document: Mapped[str | None] = mapped_column(String(40), unique=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    email: Mapped[str | None] = mapped_column(String(180), nullable=True)
    address: Mapped[str | None] = mapped_column(String(240), nullable=True)
    contact_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentChannel(Base):
    __tablename__ = "payment_channels"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    payment_type: Mapped[PaymentMethod] = mapped_column(Enum(PaymentMethod, native_enum=False), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), index=True)
    affects_cash: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    payment_group: Mapped[PaymentGroup] = mapped_column(
        Enum(PaymentGroup, native_enum=False), default=PaymentGroup.ELECTRONIC,
        server_default=text("'ELECTRONIC'"), index=True)
    display_order: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(120), nullable=True)
    account_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Alegra paymentMethod (DIAN medio de pago) for invoices. NULL on card channels:
    # the Débito/Crédito subtype chosen at checkout decides (services/payment_methods.py).
    alegra_payment_method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Business account the money of this channel usually lands in (migration 0028): only a PRESELECTION at
    # COBRAR, the cashier can always change it. requires_destination_account = an electronic payment through
    # this channel must say which account received it (off = the money does not land in a tracked account).
    default_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business_accounts.id"), nullable=True)
    requires_destination_account: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OperationalCategory(Base):
    __tablename__ = "operational_categories"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), index=True)
    display_order: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (Index("uq_products_alegra_item_id", "alegra_item_id", unique=True,
                            postgresql_where=text("alegra_item_id IS NOT NULL"),
                            sqlite_where=text("alegra_item_id IS NOT NULL")),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # NULL = product sold without EAN (eggs, loose items): found by name, never by the
    # scanner. Unique for every real code (PostgreSQL allows many NULLs). Never a fake code.
    barcode: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(180), index=True)
    sale_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    minimum_stock: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    unit: Mapped[str] = mapped_column(String(40), default="unidad", server_default=text("'unidad'"))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), index=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("suppliers.id"), nullable=True)
    operational_category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("operational_categories.id"), nullable=True, index=True)
    operational_category: Mapped[OperationalCategory | None] = relationship(lazy="joined")
    # Electronic invoicing mapping (Alegra), set explicitly per product — never
    # inferred or bulk-synced. Null means "not mapped yet, cannot be invoiced".
    alegra_item_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    alegra_tax_id: Mapped[str | None] = mapped_column(String(40), nullable=True)

    @validates("barcode")
    def _normalize_barcode(self, _key, value):
        """"" / whitespace -> NULL everywhere (forms, imports, scripts): an empty
        string would collide under the unique index and pretend to be a code."""
        if value is None:
            return None
        value = str(value).strip()
        return value or None


class CashSession(Base):
    __tablename__ = "cash_sessions"
    __table_args__ = (Index(
        "uq_cash_sessions_one_open", text("(1)"), unique=True,
        postgresql_where=text("closed_at IS NULL"), sqlite_where=text("closed_at IS NULL")),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    opening_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    opened_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    opening_note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    closing_expected: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    closing_actual: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    difference: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    closing_note: Mapped[str | None] = mapped_column(String(500), nullable=True)


class Customer(Base):
    """Minimal fiscal contact for electronic invoicing (not a CRM). Every row is
    linked to an Alegra contact (alegra_contact_id) — the POS never keeps a
    customer that Alegra doesn't know about. Unique by (identification_type,
    identification_number); never by name. is_final_consumer marks Alegra's
    CONSUMIDOR FINAL contact: kept for resolution, hidden from the cashier's search."""
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("identification_type", "identification_number", name="uq_customers_identification"),
        Index("uq_customers_final_consumer", "is_final_consumer", unique=True,
              postgresql_where=text("is_final_consumer"), sqlite_where=text("is_final_consumer = 1")),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alegra_contact_id: Mapped[str | None] = mapped_column(String(40), unique=True, nullable=True)
    identification_type: Mapped[str] = mapped_column(String(20))
    identification_number: Mapped[str] = mapped_column(String(40), index=True)
    dv: Mapped[str | None] = mapped_column(String(2), nullable=True)
    kind_of_person: Mapped[str] = mapped_column(String(20), default="PERSON_ENTITY",
                                                server_default=text("'PERSON_ENTITY'"))
    name: Mapped[str] = mapped_column(String(200), index=True)
    first_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str | None] = mapped_column(String(180), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    address: Mapped[str | None] = mapped_column(String(240), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"), index=True)
    is_final_consumer: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Sale(Base):
    __tablename__ = "sales"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    status: Mapped[SaleStatus] = mapped_column(Enum(SaleStatus, native_enum=False), default=SaleStatus.OPEN)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    # Audit trail for OPEN sales discarded without payment (status CANCELLED). Lines are kept, never deleted.
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    source: Mapped[SaleSource] = mapped_column(
        Enum(SaleSource, native_enum=False), default=SaleSource.POS_PRODUCT,
        server_default=text("'POS_PRODUCT'"), index=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    # Who the customer is (assigned on the OPEN sale). It does NOT trigger an invoice.
    # Historical sales (before migration 0024) also used it as the invoice recipient.
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    # The cashier's "Factura electrónica" checkbox, fixed at checkout (migration 0024). Only these
    # sales get an ElectronicInvoice. invoice_customer_id = fiscal recipient, independent of
    # customer_id: NULL = Consumidor final (verified 222222222222 contact in Alegra).
    requires_electronic_invoice: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    invoice_customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    # selectin, not joined: checkout/add_item lock the sale row with FOR UPDATE, which
    # PostgreSQL rejects on the nullable side of the LEFT JOIN a joined load adds.
    # Checkout choice for the sale's GRANEL lines (migration 0026): EXTERNAL_SCALE ("Báscula / efectivo")
    # or POS ("Pago electrónico"). NULL = the sale has no granel decision (every historical sale). Set to
    # EXTERNAL_SCALE when the first GRANEL line is added; cleared at checkout if no GRANEL line remains.
    granel_settlement: Mapped[str | None] = mapped_column(String(20), nullable=True)
    customer: Mapped[Customer | None] = relationship(lazy="selectin", foreign_keys=[customer_id])
    items: Mapped[list["SaleItem"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    payments: Mapped[list["SalePayment"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    external_items: Mapped[list["SaleExternalItem"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class CashSessionScaleClosing(Base):
    """What the external scale reports at its own closing for a cash session (1:1). Reconciliation
    data ONLY: never a payment, cash movement, sale or POS income, and it does not change expected
    cash. Its cash is derived (scale_total - scale_digital), never stored, so it can't contradict."""
    __tablename__ = "cash_session_scale_closings"
    __table_args__ = (CheckConstraint("scale_total >= 0", name="ck_scale_total_non_negative"),
                      CheckConstraint("scale_digital >= 0", name="ck_scale_digital_non_negative"),
                      CheckConstraint("scale_digital <= scale_total", name="ck_scale_digital_le_total"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cash_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cash_sessions.id", ondelete="CASCADE"), unique=True)
    scale_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    scale_digital: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    @property
    def scale_cash(self) -> Decimal:
        return self.scale_total - self.scale_digital


class SaleExternalItem(Base):
    """Part of the customer's bill settled OUTSIDE the POS (comida a granel already recorded by the
    scale). Kept for audit and for the customer's total/change; it is not a SaleItem, so no POS
    sale total, payment, cash movement, report or invoice line ever includes it."""
    __tablename__ = "sale_external_items"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sale_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))                 # GRANEL
    settlement: Mapped[str] = mapped_column(String(20))           # EXTERNAL_SCALE
    description: Mapped[str] = mapped_column(String(180))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    category_code: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SaleItem(Base):
    __tablename__ = "sale_items"
    __table_args__ = (UniqueConstraint("sale_id", "product_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sale_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    operational_category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("operational_categories.id"), nullable=True, index=True)
    category_code: Mapped[str] = mapped_column(String(64), default="SIN_CLASIFICAR",
                                                server_default=text("'SIN_CLASIFICAR'"))
    product_name: Mapped[str] = mapped_column(String(180))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    # Manual line (product_id NULL) added to a normal sale: SUPER / GRANEL / BULTO (migration 0025).
    # NULL on catalog lines and on historical manual sales (those are identified by Sale.source).
    manual_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Current product row (for its live alegra_item_id/alegra_tax_id mapping).
    # product_name/unit_price/unit_cost above remain the historical snapshot.
    product: Mapped[Product | None] = relationship(lazy="joined")


class SalePayment(Base):
    __tablename__ = "payments"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sale_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales.id"), index=True)
    method: Mapped[PaymentMethod | None] = mapped_column(Enum(PaymentMethod, native_enum=False), nullable=True)
    payment_channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payment_channels.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    received_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    change_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Only for card channels (Datáfono): DEBIT | CREDIT. Needed for the DIAN payment method.
    card_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # WHERE the money ended up (migration 0028), independent of HOW it was paid (the channel). NULL for cash,
    # for channels that don't land in a tracked account and for every payment before 0028 (no backfill).
    destination_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business_accounts.id"),
                                                                      nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    channel: Mapped[PaymentChannel] = relationship(lazy="joined")

    @property
    def channel_name(self) -> str:
        return self.channel.name

    @property
    def affects_cash(self) -> bool:
        return self.channel.affects_cash


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    movement_type: Mapped[InventoryMovementType] = mapped_column(Enum(InventoryMovementType, native_enum=False))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))


class Purchase(Base):
    __tablename__ = "purchases"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), index=True)
    status: Mapped[PurchaseStatus] = mapped_column(
        Enum(PurchaseStatus, native_enum=False), default=PurchaseStatus.DRAFT, index=True)
    invoice_number: Mapped[str | None] = mapped_column(String(80), nullable=True)
    purchased_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    items: Mapped[list["PurchaseItem"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="PurchaseItem.id")
    payment: Mapped["SupplierPayment | None"] = relationship(lazy="joined", uselist=False)


class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    __table_args__ = (UniqueConstraint("purchase_id", "product_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("purchases.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))


class SupplierPayment(Base):
    __tablename__ = "supplier_payments"
    __table_args__ = (UniqueConstraint("purchase_id", name="uq_supplier_payments_purchase_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("purchases.id"), index=True)
    supplier_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("suppliers.id"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    payment_channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payment_channels.id"), index=True)
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    channel: Mapped[PaymentChannel] = relationship(lazy="joined")


class CashMovement(Base):
    __tablename__ = "cash_movements"
    __table_args__ = (Index(
        "uq_cash_movement_sale_reference", "reference_id", unique=True,
        postgresql_where=text("movement_type = 'SALE' AND reference_id IS NOT NULL"),
        sqlite_where=text("movement_type = 'SALE' AND reference_id IS NOT NULL")),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cash_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cash_sessions.id"), index=True)
    # SUPPLIER_PAYMENT is longer than legacy cash movement values.
    movement_type: Mapped[CashMovementType] = mapped_column(
        Enum(CashMovementType, native_enum=False, length=32))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Only meaningful for WITHDRAWAL; NULL means unclassified (e.g. pre-existing history).
    withdrawal_reason: Mapped[WithdrawalReason | None] = mapped_column(
        Enum(WithdrawalReason, native_enum=False, length=10), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))


class Expense(Base):
    __tablename__ = "expenses"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    category: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str] = mapped_column(String(240))
    payment_source: Mapped[ExpensePaymentSource] = mapped_column(
        Enum(ExpensePaymentSource, native_enum=False), index=True)
    payment_channel_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("payment_channels.id"), nullable=True, index=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    payment_channel: Mapped[PaymentChannel | None] = relationship(lazy="joined")

    @property
    def channel_name(self) -> str | None:
        return self.payment_channel.name if self.payment_channel else None


class InternalConsumption(Base):
    __tablename__ = "internal_consumptions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"), nullable=True, index=True)
    description: Mapped[str] = mapped_column(String(240))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(12, 3), nullable=True)
    unit_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    reference_sale_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    cost_total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    sale_value_total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    estimated_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    product: Mapped[Product | None] = relationship(lazy="joined")
    # Legacy/manual entries keep their single product fields above.  A cart-based
    # consumption is one header with immutable item snapshots below.
    items: Mapped[list["InternalConsumptionItem"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="InternalConsumptionItem.id")

    @property
    def product_name(self) -> str | None:
        return self.product.name if self.product else None


class InternalConsumptionItem(Base):
    __tablename__ = "internal_consumption_items"
    __table_args__ = (UniqueConstraint("internal_consumption_id", "product_id",
                                       name="uq_internal_consumption_item_product"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    internal_consumption_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("internal_consumptions.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    # These are snapshots: catalog edits after the withdrawal never alter its audit trail.
    product_name: Mapped[str] = mapped_column(String(240))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    reference_sale_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    cost_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    sale_value_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))


class DailyReport(Base):
    """Immutable snapshot of a closed CashSession, frozen at close time.

    report_data embeds channel/category/product NAMES (not just ids) so the
    snapshot never changes if PaymentChannel/Product/OperationalCategory are
    later renamed, reclassified, or deactivated.
    """
    __tablename__ = "daily_reports"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cash_session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cash_sessions.id"), unique=True, index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sales_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    cash_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    electronic_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    tickets: Mapped[int] = mapped_column(Integer)
    average_ticket: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    gross_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    gross_profit: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    gross_margin: Mapped[Decimal] = mapped_column(Numeric(7, 2))
    closing_expected: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    closing_actual: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    closing_difference: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    # Real JSONB on PostgreSQL; falls back to generic JSON on SQLite (tests only).
    report_data: Mapped[dict] = mapped_column(JSONB().with_variant(JSON(), "sqlite"))
    session: Mapped[CashSession] = relationship(lazy="joined")


class ElectronicInvoice(Base):
    """Tracks a Sale's external electronic-invoicing lifecycle. Deliberately
    separate from Sale/Payment/CashSession/InventoryMovement: a failure here
    must never touch any of those — see services/electronic_invoicing.py."""
    __tablename__ = "electronic_invoices"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sale_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales.id"), unique=True, index=True)
    provider: Mapped[ElectronicInvoiceProviderName] = mapped_column(
        Enum(ElectronicInvoiceProviderName, native_enum=False, length=20),
        default=ElectronicInvoiceProviderName.ALEGRA, server_default=text("'ALEGRA'"))
    status: Mapped[ElectronicInvoiceStatus] = mapped_column(
        Enum(ElectronicInvoiceStatus, native_enum=False, length=20),
        default=ElectronicInvoiceStatus.PENDING, server_default=text("'PENDING'"), index=True)

    # Alegra's own record of the invoice — never our internal status.
    provider_invoice_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    provider_number: Mapped[str | None] = mapped_column(String(60), nullable=True)
    provider_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # DIAN's legal acceptance — a third, distinct concept from both of the above.
    legal_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # DIAN's unique invoice code, as reported by Alegra (stamp.cufe).
    cufe: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Signed S3 URLs from Alegra (expire; sync refreshes them) are far longer than 500 chars.
    pdf_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    xml_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Which Alegra contact the invoice was billed to, plus the local Customer
    # (NULL = Consumidor Final) and an immutable snapshot of its fiscal identity
    # at issue time — history never depends on the mutable Customer row.
    alegra_contact_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), nullable=True, index=True)
    customer_snapshot: Mapped[dict | None] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), nullable=True)

    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Anti-duplicate guard: True from just before a POST until Alegra's answer is
    # known. A timeout/5xx leaves it True, so the next attempt must first search
    # Alegra for this sale's invoice (reconcile) instead of POSTing again.
    needs_reconciliation: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    # Never store secrets here. Sanitized request snapshot and raw provider
    # response, both plain JSON with no Authorization header or token.
    provider_payload: Mapped[dict | None] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), nullable=True)
    provider_response: Mapped[dict | None] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    @property
    def display_status(self) -> str:
        from app.services.electronic_invoicing import display_status
        return display_status(self)


class CatalogSyncOperation(Base):
    """Audit of REAL catalog writes (LINK / CREATE). Previews are never recorded here."""
    __tablename__ = "catalog_sync_operations"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    action: Mapped[str] = mapped_column(String(20))
    old_alegra_item_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    new_alegra_item_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(30))
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class CatalogSyncRun(Base):
    """One full POS<->Alegra catalog analysis (the remote scan takes minutes), kept so the
    UI can show it and link from it; links are always re-validated live before writing."""
    __tablename__ = "catalog_sync_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(20))        # RUNNING | DONE | FAILED
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    remote_items: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), nullable=True)
    rows: Mapped[list | None] = mapped_column(JSONB().with_variant(JSON(), "sqlite"), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class PhysicalAudit(Base):
    """Store walk-through ("interventoría"): which active products are physically there.
    Never writes inventory: counted quantities only reach the ledger through the regular
    physical-count flow (template -> preview -> confirm)."""
    __tablename__ = "physical_audits"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")          # OPEN | FINISHED
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    items: Mapped[list["PhysicalAuditItem"]] = relationship(
        back_populates="audit", cascade="all, delete-orphan", lazy="selectin",
        order_by="PhysicalAuditItem.last_seen_at.desc()")
    unknown_codes: Mapped[list["PhysicalAuditUnknownCode"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="PhysicalAuditUnknownCode.last_scanned_at.desc()")


class PhysicalAuditItem(Base):
    __tablename__ = "physical_audit_items"
    __table_args__ = (UniqueConstraint("audit_id", "product_id"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    audit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("physical_audits.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("products.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("1"))
    # False = only "found" (default quantity 1); True = the operator entered/scanned a real count.
    counted: Mapped[bool] = mapped_column(Boolean, default=False)
    scans: Mapped[int] = mapped_column(Integer, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    audit: Mapped[PhysicalAudit] = relationship(back_populates="items")
    product: Mapped[Product] = relationship(lazy="joined")


class PhysicalAuditUnknownCode(Base):
    """A code scanned during an audit that no Product has: physically in the store but not
    in the POS catalog yet ("productos por crear"). Never creates a Product."""
    __tablename__ = "physical_audit_unknown_codes"
    __table_args__ = (UniqueConstraint("audit_id", "barcode"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    audit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("physical_audits.id", ondelete="CASCADE"), index=True)
    barcode: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="UNKNOWN_PRODUCT")
    scan_count: Mapped[int] = mapped_column(Integer, default=1)
    first_scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_scanned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# Backwards-compatible import name while callers migrate to SalePayment.
Payment = SalePayment


# ---- Post-sale management (migration 0027): immutable history + append-only compensating operations ------------

class SaleNote(Base):
    """Append-only clarification of a sale. Never changes money, stock, invoice, sale or payment."""
    __tablename__ = "sale_notes"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sale_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales.id"), index=True)
    text: Mapped[str] = mapped_column(String(500))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    performed_by_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SaleReturn(Base):
    """A return of a COMPLETED sale: PARTIAL, TOTAL (what was left) or VOID (the whole sale; shown as
    Anulada). The sale and its lines, payments and movements are never modified."""
    __tablename__ = "sale_returns"
    __table_args__ = (Index("uq_sale_returns_one_void", "sale_id", unique=True,
                            postgresql_where=text("kind = 'VOID'"), sqlite_where=text("kind = 'VOID'")),
                      CheckConstraint("kind IN ('PARTIAL', 'TOTAL', 'VOID')", name="ck_sale_returns_kind"),
                      CheckConstraint("total_amount >= 0", name="ck_sale_returns_total"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sale_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales.id"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    reason_code: Mapped[str] = mapped_column(String(40))
    reason_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    cash_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cash_sessions.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    performed_by_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    lines: Mapped[list["SaleReturnLine"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    refunds: Mapped[list["SaleRefund"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class SaleReturnLine(Base):
    __tablename__ = "sale_return_lines"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_sale_return_lines_quantity"),
                      CheckConstraint("(sale_item_id IS NULL) <> (source_replacement_line_id IS NULL)",
                                      name="ck_sale_return_lines_source"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    return_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sale_returns.id"), index=True)
    # Provenance: the original sale line OR a unit delivered by an earlier exchange (exactly one).
    sale_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sale_items.id"), index=True, nullable=True)
    source_replacement_line_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sale_exchange_replacement_lines.id"), index=True, nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    category_code: Mapped[str] = mapped_column(String(64))
    # True = the product physically went back to stock (one InventoryMovement RETURN, created once).
    restock: Mapped[bool] = mapped_column(Boolean)
    inventory_movement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory_movements.id"), unique=True,
                                                                    nullable=True)


class SaleRefund(Base):
    """Money given back for a return, per channel. Cash: paid out at once (CashMovement REFUND),
    COMPLETED. Electronic: PENDING until the cashier confirms the external transfer (the POS only
    records it; there is no integration with Nequi / Daviplata / Bre-B / datáfono)."""
    __tablename__ = "sale_refunds"
    __table_args__ = (CheckConstraint("status IN ('PENDING', 'COMPLETED')", name="ck_sale_refunds_status"),
                      CheckConstraint("amount > 0", name="ck_sale_refunds_amount"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    return_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sale_returns.id"), index=True)
    payment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("payments.id"), nullable=True)
    channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payment_channels.id"))
    affects_cash: Mapped[bool] = mapped_column(Boolean)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    status: Mapped[str] = mapped_column(String(12))
    cash_movement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cash_movements.id"), unique=True, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    confirmed_by_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Business account the electronic refund leaves from (0028). NULL for cash and for refunds before 0028.
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business_accounts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    channel: Mapped[PaymentChannel] = relationship(lazy="joined")


# 0028: a correction changes the channel, the destination account, or both (never neither).
PAYMENT_CORRECTION_CHANGE = ("from_channel_id <> to_channel_id"
                             " OR ((from_account_id IS NULL) <> (to_account_id IS NULL))"
                             " OR (from_account_id IS NOT NULL AND to_account_id IS NOT NULL"
                             " AND from_account_id <> to_account_id)")


class PaymentCorrection(Base):
    """Append-only correction of a payment (sequence 1..n). The payment keeps its original channel and
    destination account; the effective ones are the latest correction's to_* (services/effective_payments)."""
    __tablename__ = "payment_corrections"
    __table_args__ = (UniqueConstraint("payment_id", "sequence", name="uq_payment_corrections_sequence"),
                      CheckConstraint(PAYMENT_CORRECTION_CHANGE, name="ck_payment_corrections_change"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sale_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales.id"), index=True)
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    from_channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payment_channels.id"))
    to_channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payment_channels.id"))
    # Effective destination account before / after this correction (0028). NULL = no tracked account.
    from_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business_accounts.id"), nullable=True)
    to_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business_accounts.id"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    reason: Mapped[str] = mapped_column(String(500))
    cash_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cash_sessions.id"), nullable=True)
    cash_movement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cash_movements.id"), unique=True, nullable=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    performed_by_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())



class SaleExchange(Base):
    """PRODUCT EXCHANGE of a COMPLETED sale (the business's usual post-sale case). The sale is never
    modified. Credit = the value the returned units were DELIVERED with; replacements use the current
    price. difference = replacement - credit (signed) and settlement says who pays: EVEN / CUSTOMER_PAYS /
    STORE_REFUNDS. Only that difference ever moves money (SaleExchangeSettlement)."""
    __tablename__ = "sale_exchanges"
    __table_args__ = (CheckConstraint("status IN ('COMPLETED')", name="ck_sale_exchanges_status"),
                      CheckConstraint("returned_credit > 0 AND replacement_total > 0", name="ck_sale_exchanges_amounts"),
                      CheckConstraint("difference = replacement_total - returned_credit", name="ck_sale_exchanges_difference"),
                      CheckConstraint("(settlement = 'EVEN' AND difference = 0) OR (settlement = 'CUSTOMER_PAYS' AND "
                                      "difference > 0) OR (settlement = 'STORE_REFUNDS' AND difference < 0)",
                                      name="ck_sale_exchanges_settlement"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sale_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sales.id"), index=True)
    reason_code: Mapped[str] = mapped_column(String(40))
    reason_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    returned_credit: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    replacement_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    difference: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    settlement: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(12), default="COMPLETED")
    cash_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cash_sessions.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    performed_by_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    returned: Mapped[list["SaleExchangeReturnLine"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    replacements: Mapped[list["SaleExchangeReplacementLine"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    money: Mapped["SaleExchangeSettlement | None"] = relationship(cascade="all, delete-orphan", lazy="selectin",
                                                                  uselist=False)


class SaleExchangeReplacementLine(Base):
    """What the customer takes instead: catalog product (current price, EXCHANGE_OUT movement) or a manual
    line (SUPER / BULTO / PET_OTHER: no product, no inventory). Its unit_price is the value it was
    delivered with: the credit if it is exchanged / returned later (chained exchanges)."""
    __tablename__ = "sale_exchange_replacement_lines"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_sale_exchange_replacement_lines_quantity"),
                      CheckConstraint("(product_id IS NULL) <> (manual_kind IS NULL)",
                                      name="ck_sale_exchange_replacement_lines_kind"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exchange_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sale_exchanges.id"), index=True)
    product_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("products.id"), nullable=True)
    manual_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    product_name: Mapped[str] = mapped_column(String(180))
    category_code: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    inventory_movement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory_movements.id"), unique=True,
                                                                    nullable=True)


class SaleExchangeReturnLine(Base):
    __tablename__ = "sale_exchange_return_lines"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_sale_exchange_return_lines_quantity"),
                      CheckConstraint("(sale_item_id IS NULL) <> (source_replacement_line_id IS NULL)",
                                      name="ck_sale_exchange_return_lines_source"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exchange_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sale_exchanges.id"), index=True)
    # Provenance: the original sale line OR a unit delivered by an earlier exchange (exactly one).
    sale_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sale_items.id"), index=True, nullable=True)
    source_replacement_line_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sale_exchange_replacement_lines.id"), index=True, nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    unit_credit: Mapped[Decimal] = mapped_column(Numeric(14, 2))      # the value the unit was delivered with
    line_credit: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    category_code: Mapped[str] = mapped_column(String(64))
    restock: Mapped[bool] = mapped_column(Boolean)
    inventory_movement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("inventory_movements.id"), unique=True,
                                                                    nullable=True)


class SaleExchangeSettlement(Base):
    """The money of an exchange: ONLY the difference. IN = the customer paid it (cash -> CashMovement
    EXCHANGE +). OUT = the store gave it back (cash -> CashMovement EXCHANGE_REFUND -, at once; electronic
    -> PENDING until the cashier confirms the external transfer; the POS never moves that money)."""
    __tablename__ = "sale_exchange_settlements"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_sale_exchange_settlements_amount"),
                      CheckConstraint("direction IN ('IN', 'OUT')", name="ck_sale_exchange_settlements_direction"),
                      CheckConstraint("status IN ('PENDING', 'COMPLETED')", name="ck_sale_exchange_settlements_status"),
                      CheckConstraint("status = 'COMPLETED' OR (direction = 'OUT' AND NOT affects_cash)",
                                      name="ck_sale_exchange_settlements_pending"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exchange_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sale_exchanges.id"), unique=True)
    direction: Mapped[str] = mapped_column(String(3))
    payment_channel_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payment_channels.id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    affects_cash: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(12))
    received_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    change_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    card_type: Mapped[str | None] = mapped_column(String(10), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    cash_movement_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cash_movements.id"), unique=True, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    confirmed_by_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Business account the electronic difference entered (IN) or left from (OUT) (0028). NULL for cash.
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business_accounts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    channel: Mapped[PaymentChannel] = relationship(lazy="joined")


# ---- Daily patrimony & account reconciliation (migration 0028) -------------------------------------------------

class BusinessAccount(Base):
    """An operational account of the business (Nequi, Davivienda, Nu) used ONLY for reconciliation: the
    owner types the balance they see in the app. No credentials, no account numbers, no bank API."""
    __tablename__ = "business_accounts"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    display_order: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InventoryValuationSnapshot(Base):
    """Immutable photo of the inventory REGISTERED in the POS AT COST at the close of a jornada (1 per cash
    session): stock (sum of the ledger) x Product.unit_cost at that moment. DIAGNOSTIC: while the automatic
    valuation is not the official source (`is_official` false, frozen at capture) it is "NO VERIFICADA" and is
    never shown as the store's patrimony. Never recomputed."""
    __tablename__ = "inventory_valuation_snapshots"
    __table_args__ = (CheckConstraint("inventory_value >= 0", name="ck_inventory_valuation_value"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cash_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cash_sessions.id"), unique=True)
    inventory_value: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    products_with_positive_stock: Mapped[int] = mapped_column(Integer)
    products_valued: Mapped[int] = mapped_column(Integer)
    products_missing_cost: Mapped[int] = mapped_column(Integer)
    products_negative_stock: Mapped[int] = mapped_column(Integer)
    quantity_valued: Mapped[Decimal] = mapped_column(Numeric(16, 3))
    quantity_missing_cost: Mapped[Decimal] = mapped_column(Numeric(16, 3))
    cost_source: Mapped[str] = mapped_column(String(40))
    # Coverage (diagnostic): how much of the ACTIVE catalog has registered stock, and the value split.
    active_value: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0, server_default=text("0"))
    inactive_value: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0, server_default=text("0"))
    active_catalog_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    active_with_stock: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # True only if the automatic valuation was the official patrimony source when this photo was taken.
    is_official: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PatrimonyDeclaration(Base):
    """VALOR DECLARADO POR EL DUEÑO: the total value of the store's merchandise the owner states for a jornada
    (optional, never blocks the close). Origin MANUAL. Never edited: a mistake is a PatrimonyDeclarationCorrection;
    the effective value is the latest correction's new value."""
    __tablename__ = "patrimony_declarations"
    __table_args__ = (CheckConstraint("declared_value >= 0", name="ck_patrimony_declarations_value"),
                      CheckConstraint("source IN ('MANUAL')", name="ck_patrimony_declarations_source"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cash_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cash_sessions.id"), unique=True)
    declared_value: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    source: Mapped[str] = mapped_column(String(12), default="MANUAL")
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    performed_by_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    declared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    corrections: Mapped[list["PatrimonyDeclarationCorrection"]] = relationship(
        lazy="selectin", order_by="PatrimonyDeclarationCorrection.created_at")

    @property
    def effective_value(self) -> Decimal:
        return self.corrections[-1].new_value if self.corrections else self.declared_value


class PatrimonyDeclarationCorrection(Base):
    """Audited fix of a declared value (old / new / reason / when). The declaration keeps its original value."""
    __tablename__ = "patrimony_declaration_corrections"
    __table_args__ = (CheckConstraint("new_value >= 0", name="ck_patrimony_corrections_value"),
                      CheckConstraint("new_value <> old_value", name="ck_patrimony_corrections_change"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    declaration_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patrimony_declarations.id"), index=True)
    old_value: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    new_value: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    reason: Mapped[str] = mapped_column(String(300))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PatrimonySourceChange(Base):
    """Append-only history of WHICH value is the official patrimony: MANUAL (owner's declaration, the default
    while there is no change) or AUTOMATIC (POS valuation, only after a validated physical inventory). Each
    change is explicit, with its reason; it never happens automatically. Its time marks the transition."""
    __tablename__ = "patrimony_source_changes"
    __table_args__ = (CheckConstraint("source IN ('MANUAL', 'AUTOMATIC')", name="ck_patrimony_source_changes_source"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(12))
    reason: Mapped[str] = mapped_column(String(500))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    performed_by_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BusinessAccountSnapshot(Base):
    """The balance the owner SAW in an account at the close of a jornada, plus what the POS expected,
    frozen at capture. The first one of an account is the baseline (no expected, no difference). Never
    edited: a typing mistake is fixed with a BusinessAccountSnapshotCorrection."""
    __tablename__ = "business_account_snapshots"
    __table_args__ = (UniqueConstraint("cash_session_id", "business_account_id",
                                       name="uq_business_account_snapshots_session_account"),
                      CheckConstraint("observed_balance >= 0", name="ck_business_account_snapshots_observed"),
                      CheckConstraint("(is_baseline AND expected_balance IS NULL AND difference IS NULL) OR "
                                      "(NOT is_baseline AND expected_balance IS NOT NULL AND difference IS NOT NULL "
                                      "AND previous_observed_balance IS NOT NULL)",
                                      name="ck_business_account_snapshots_baseline"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cash_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cash_sessions.id"), index=True)
    business_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business_accounts.id"), index=True)
    observed_balance: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    is_baseline: Mapped[bool] = mapped_column(Boolean)
    previous_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business_account_snapshots.id"),
                                                                   nullable=True)
    previous_observed_balance: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    pos_inflows: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    pos_outflows: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    registered_external_inflows: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    registered_external_outflows: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    expected_balance: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    difference: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    corrections: Mapped[list["BusinessAccountSnapshotCorrection"]] = relationship(
        lazy="selectin", order_by="BusinessAccountSnapshotCorrection.created_at")

    @property
    def effective_observed(self) -> Decimal:
        return self.corrections[-1].new_observed_balance if self.corrections else self.observed_balance


class BusinessAccountSnapshotCorrection(Base):
    """Audited fix of a typed balance: the snapshot keeps its original value; the effective one is the
    latest correction's new value. Only the latest snapshot of an account can be corrected."""
    __tablename__ = "business_account_snapshot_corrections"
    __table_args__ = (CheckConstraint("new_observed_balance >= 0", name="ck_business_account_corrections_value"),
                      CheckConstraint("new_observed_balance <> old_observed_balance",
                                      name="ck_business_account_corrections_change"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business_account_snapshots.id"), index=True)
    old_observed_balance: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    new_observed_balance: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    reason: Mapped[str] = mapped_column(String(300))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BusinessAccountMovement(Base):
    """EXPLICIT external movement of a business account that is not a POS sale / refund / exchange (money
    taken out of Nequi to pay something, a transfer between the accounts...). It never touches the cash
    drawer, a sale or a payment. A transfer = two rows (OUT + IN) sharing transfer_group_id, written
    atomically, so it never looks like business income. A mistake is undone with a REVERSAL row."""
    __tablename__ = "business_account_movements"
    __table_args__ = (CheckConstraint("amount > 0", name="ck_business_account_movements_amount"),
                      CheckConstraint("direction IN ('IN', 'OUT')", name="ck_business_account_movements_direction"),
                      CheckConstraint("(reason = 'OTHER_IN' AND direction = 'IN') OR (reason = 'OTHER_OUT' AND "
                                      "direction = 'OUT') OR (reason = 'TRANSFER_BETWEEN_ACCOUNTS' AND "
                                      "transfer_group_id IS NOT NULL) OR (reason = 'REVERSAL' AND "
                                      "reversal_of_id IS NOT NULL)", name="ck_business_account_movements_reason"))
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("business_accounts.id"), index=True)
    cash_session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cash_sessions.id"), index=True)
    direction: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    reason: Mapped[str] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    transfer_group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    reversal_of_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("business_account_movements.id"),
                                                             nullable=True, unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(80), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
