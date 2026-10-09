from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://pos:pos@localhost:5432/pos"
    cors_origins: str = "http://localhost:3000"
    commercial_rounding_policy: str = "round_up_500"

    # Alegra electronic invoicing. Disabled by default; credentials live only
    # here (env vars), never hardcoded, never logged, never sent to the client.
    alegra_enabled: bool = False
    alegra_api_url: str = "https://api.alegra.com/api/v1"
    alegra_email: str = ""
    alegra_token: str = ""
    alegra_number_template_id: str = ""
    # Automatic issue after checkout (Sale COMPLETED -> ElectronicInvoice ->
    # Alegra, in the background, never blocking/reverting the sale). OFF by
    # default: turning it on creates REAL invoices in Alegra. The stamp flag
    # additionally asks Alegra to send them to DIAN.
    alegra_auto_issue: bool = False
    alegra_auto_issue_stamp: bool = False
    # Administrative web issue/retry OUTSIDE auto-issue. Off by default and never
    # exposed to the cashier: before the cutover the only way to issue is the
    # controlled script (app/scripts/controlled_issue.py); after it, auto-issue.
    alegra_manual_issue_enabled: bool = False
    # Optional explicit id of Alegra's "CONSUMIDOR FINAL" contact. When empty it
    # is resolved from the synced customers or looked up in Alegra by the DIAN
    # generic identification 222222222222.
    alegra_final_consumer_contact_id: str = ""
    # Generic Alegra items for manual sales (no catalog product). MANUAL_BULK
    # (granel) -> the account's existing "GRANEL" item 372 (approved 2026-09-28).
    # MANUAL_SACK (bulto) stays empty -> BLOCKED until the BULTO item exists.
    # POS -> Alegra catalog creation. Three barriers: this flag (off), dry_run (default
    # true) and a per-batch limit. With the flag off nothing is ever POSTed to /items.
    alegra_catalog_create_enabled: bool = False
    alegra_catalog_batch_limit: int = 50
    alegra_item_manual_bulk: str = "372"
    alegra_item_manual_sack: str = ""
    # "Producto manual" SUPER lines. Empty = an invoice with such a line is BLOCKED (never guessed).
    # Candidate already in Alegra (GET 2026-10-03): item 1782 "SIN CODIFICAR" — pending approval.
    alegra_item_manual_super: str = ""
    # "Producto manual" Mascotas > Otro (bags, mats, accessories without barcode). Empty = an invoice with
    # such a line is BLOCKED until a generic Alegra item is approved for it (never guessed).
    alegra_item_manual_pet_other: str = ""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
