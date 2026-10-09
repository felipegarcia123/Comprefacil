"""AlegraProvider: typed wrapper exposing exactly what electronic_invoicing.py
needs. Implements the ElectronicInvoiceProvider contract (app/providers/contracts.py).
No httpx/response-shape details leak past this module.
"""
from app.services.alegra_client import AlegraClient


def _as_list(response) -> list[dict]:
    """Some Alegra list endpoints (confirmed empirically: /taxes) return a
    paginated envelope {"total": "N", "results": [...]} instead of a bare
    array — normalize both shapes here so every caller can just iterate."""
    if isinstance(response, list):
        return response
    if isinstance(response, dict) and isinstance(response.get("results"), list):
        return response["results"]
    return []


class AlegraProvider:
    def __init__(self, client: AlegraClient | None = None):
        self.client = client or AlegraClient()

    @property
    def configured(self) -> bool:
        return self.client.configured

    def get_company(self) -> dict:
        return self.client.get("/company")

    def list_number_templates(self, document_type: str = "invoice") -> list[dict]:
        return _as_list(self.client.get("/number-templates", params={"documentType": document_type, "limit": 30}))

    def list_taxes(self, limit: int = 30) -> list[dict]:
        return _as_list(self.client.get("/taxes", params={"limit": limit}))

    def search_contacts(self, query: str | None = None, limit: int = 10) -> list[dict]:
        params: dict = {"limit": limit}
        if query:
            params["query"] = query
        return _as_list(self.client.get("/contacts", params=params))

    def list_contacts_page(self, start: int = 0, limit: int = 30, contact_type: str | None = "client") -> tuple[list[dict], int | None]:
        """One page of GET /contacts (Alegra caps `limit` at 30). metadata=true
        makes Alegra wrap the page as {"data": [...], "metadata": {"total": N}}
        (verified against the real account on 2026-09-28)."""
        params: dict = {"start": start, "limit": min(limit, 30), "metadata": "true"}
        if contact_type:
            params["type"] = contact_type
        response = self.client.get("/contacts", params=params)
        if isinstance(response, dict) and isinstance(response.get("data"), list):
            total = (response.get("metadata") or {}).get("total")
            return response["data"], int(total) if total is not None else None
        return _as_list(response), None

    def find_contacts_by_identification(self, number: str) -> list[dict]:
        return _as_list(self.client.get("/contacts", params={"identification": number, "limit": 30}))

    def create_contact(self, payload: dict) -> dict:
        return self.client.post("/contacts", json=payload)

    def update_contact(self, contact_id: str, payload: dict) -> dict:
        return self.client.put(f"/contacts/{contact_id}", json=payload)

    def search_items(self, query: str | None = None, limit: int = 10) -> list[dict]:
        params: dict = {"limit": limit}
        if query:
            params["query"] = query
        return _as_list(self.client.get("/items", params=params))

    def list_invoices_page(self, *, date: str, client_id: str, start: int = 0,
                           limit: int = 30) -> tuple[list[dict], int | None]:
        """GET /invoices filtered by exact `date` and `client_id` (both documented
        filters). Alegra has no idempotency key and no search by observations, so
        reconciliation narrows by these and matches the observations marker locally."""
        response = self.client.get("/invoices", params={"date": date, "client_id": client_id, "start": start,
                                                         "limit": min(limit, 30), "metadata": "true",
                                                         "order_field": "id", "order_direction": "ASC"})
        if isinstance(response, dict) and isinstance(response.get("data"), list):
            total = (response.get("metadata") or {}).get("total")
            return response["data"], int(total) if total is not None else None
        return _as_list(response), None

    def list_items_page(self, start: int = 0, limit: int = 30) -> tuple[list[dict], int | None]:
        # Explicit stable order: without it Alegra's pages can shift between requests
        # (observed 2026-09-28: one item repeated and another skipped in a full scan).
        response = self.client.get("/items", params={"start": start, "limit": min(limit, 30), "metadata": "true",
                                                      "order_field": "id", "order_direction": "ASC"})
        if isinstance(response, dict) and isinstance(response.get("data"), list):
            total = (response.get("metadata") or {}).get("total")
            return response["data"], int(total) if total is not None else None
        return _as_list(response), None

    def list_recent_invoices(self, limit: int = 5) -> list[dict]:
        return _as_list(self.client.get("/invoices", params={"limit": limit}))

    def preview_invoice(self, payload: dict) -> dict:
        """POST /invoices/preview: returns {"pdfUrl"} of a DRAFT rendering. Documented
        and verified (2026-09-28) to create nothing and consume no numbering."""
        return self.client.post("/invoices/preview", json=payload)

    def create_item(self, payload: dict) -> dict:
        """POST /items. Only reachable through alegra_catalog_sync.create_remote, behind
        ALEGRA_CATALOG_CREATE_ENABLED + dry_run + batch limit."""
        return self.client.post("/items", json=payload)

    def create_invoice(self, payload: dict) -> dict:
        return self.client.post("/invoices", json=payload)

    def stamp_invoices(self, invoice_ids: list[str]) -> dict:
        """POST /invoices/stamp {"ids": [...]}: sends EXISTING invoices (drafts) to DIAN.
        Only reachable through electronic_invoicing.stamp_existing_invoice(confirm=True).
        Never creates a document."""
        return self.client.post("/invoices/stamp", json={"ids": [int(i) if str(i).isdigit() else i for i in invoice_ids]})

    def get_invoice(self, provider_invoice_id: str, fields: str | None = None) -> dict:
        params = {"fields": fields} if fields else None
        return self.client.get(f"/invoices/{provider_invoice_id}", params=params)
