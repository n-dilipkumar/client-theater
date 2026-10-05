"""The writes. A quote is created from a deal, edited, and published.

Every method that writes takes a **required keyword-only** ``source``, and the
feature module builds each one from ``router.prefix``. A domain function that
hardcoded a path would leave the audit log naming a route the app might have
stopped serving, and that class of bug has shipped here before. Required rather
than defaulted, so a caller that forgets gets a type error instead of a wrong
audit row.

Line item identity is the load-bearing rule
--------------------------------------------

"Line items on quotes have their own record IDs, separate to the deal line item
record IDs." Every write here therefore calls ``store.create`` for a quote line
and never ``store.update`` on the deal's row. The deal's id is kept on the clone
as ``source_line_item_id``, which is provenance and never an identity.

The alternative is to carry the deal's id across. It would make the clone
invisible in the audit log, it would make the replace-on-publish unable to tell
which rows it replaced, and a later edit would rewrite the deal's line through a
quote route. All three failures come from one saved field.

``source`` on every write, ``actor`` on every write, and no HTTP in this file.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any, Mapping

from dsr.quote_authoring.deals import (
    catalogue_available,
    deal_line_items,
    find_deal,
    find_product,
    read_deal,
    read_deal_line,
    search_products,
)
from dsr.quote_authoring.errors import QuoteConflict, QuoteError, QuoteNotFound
from dsr.quote_authoring.pricing import (
    as_number,
    as_text,
    line_amounts,
    money,
    resolve_unit_price,
    totals_for,
)
from dsr.quote_authoring.vocabulary import (
    BUILTIN_MODULE_KEYS,
    DEAL_LINE_ITEM_COLLECTION,
    DISCOUNT_PERCENTAGE,
    DISCOUNT_TYPES,
    KIND_BUILTIN,
    KIND_CUSTOM,
    LINE_ITEM_COLLECTION,
    MODULE_KINDS,
    PRICE_FROM_CATALOG,
    PRICE_FROM_DEAL,
    PRICE_FROM_MANUAL,
    QUOTE_COLLECTION,
    QUOTE_TEMPLATE_TYPE,
    REASON_ALREADY_PUBLISHED,
    REASON_CUSTOM_MODULE_NEEDS_UI_PROVENANCE,
    REASON_CUSTOM_MODULE_NOT_API_AUTHORABLE,
    REASON_MODULE_KEY_UNKNOWN,
    REASON_QUOTE_EXPIRED,
    REASON_QUOTE_FROZEN,
    REASON_QUOTE_IS_EMPTY,
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    TEMPLATE_COLLECTION,
    vocabulary,
)
from dsr.store import RecordStore


class QuoteEngine:
    """Quotes, their line items, their templates, and the publish write-back."""

    def __init__(
        self,
        store: RecordStore,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self._now = now or (lambda: datetime.now().astimezone())

    # -- the clock ---------------------------------------------------------- #

    def today(self) -> str:
        """Today, as the date string a record stores."""
        return self._now().date().isoformat()

    def _stamp(self) -> str:
        return self._now().isoformat(timespec="seconds")

    # -- vocabulary --------------------------------------------------------- #

    @staticmethod
    def vocabulary() -> dict[str, Any]:
        return vocabulary()

    # -- templates ---------------------------------------------------------- #

    def create_template(
        self, payload: Mapping[str, Any], *, source: str, actor: str | None
    ) -> dict[str, Any]:
        """Record a quote template.

        The CRM API offers a read and a search on quote templates and no create
        endpoint, because templates are built in the editor. So a template
        written here is stamped ``authored_via: ui`` and cannot pretend to be an
        API-authored one. The route exists because the dropdown has to be
        populated; it does not claim to be the vendor's create path.
        """
        name = as_text(payload.get("name") or payload.get("title"))
        if not name:
            raise QuoteError("A template needs a name.", {"name": "Enter a name."})

        keys = self._module_keys(payload.get("modules"))
        record = self.store.create(
            TEMPLATE_COLLECTION,
            {
                "name": name,
                "description": as_text(payload.get("description")),
                "modules": keys,
                "template_type": QUOTE_TEMPLATE_TYPE,
                "authored_via": "ui",
            },
            room_id=as_text(payload.get("room_id")) or None,
            actor=actor,
            source=source,
        )
        # Flattened, because ``list_templates`` flattens. A create that returned
        # ``{"id", "collection", "data": {...}}`` beside a list that returned
        # ``{"id", "name", ...}`` would make every caller branch on which call it
        # made.
        return {"template": record["data"] | {"id": record["id"]}}

    def list_templates(self, room_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.list(TEMPLATE_COLLECTION, room_id=room_id, limit=500)
        return sorted((row["data"] | {"id": row["id"]} for row in rows), key=lambda r: r["name"])

    def _template(self, template_id: str) -> dict[str, Any] | None:
        if not template_id:
            return None
        record = self.store.get(template_id)
        if record is None or record["collection"] != TEMPLATE_COLLECTION:
            return None
        return record["data"] | {"id": record["id"]}

    # -- modules ------------------------------------------------------------ #

    def _module_keys(self, raw: Any) -> list[str]:
        """Validate a module list against the builtin vocabulary.

        A builtin key must be one this product knows. An unknown key is a
        refusal rather than a warning, because a module the renderer has no
        description for is a module that would silently vanish from the quote.
        """
        keys: list[str] = []
        for entry in raw or []:
            key = as_text(entry.get("key") if isinstance(entry, Mapping) else entry)
            if key and key not in BUILTIN_MODULE_KEYS:
                raise QuoteError(
                    f"{key!r} is not a builtin module key.",
                    {"modules": f"Use one of: {', '.join(BUILTIN_MODULE_KEYS)}."},
                )
            if key and key not in keys:
                keys.append(key)
        return keys

    def _module_rows(self, quote: Mapping[str, Any], raw: Any) -> list[dict[str, Any]]:
        """The module list a quote stores, validated.

        Two refusals here, and both come straight from the research. A request
        that asks the API to author coded module content is refused, because HubSpot
        records that it "isn't possible to create or add custom coded modules to a
        quote using the API". A custom module is still accepted, because the
        research also records that people do build them, in the editor. It must
        say so, which is the only way a reader can tell an editor-authored module
        from one an API invented.
        """
        if raw is None:
            return list(quote.get("modules") or [])
        rows: list[dict[str, Any]] = []
        for position, entry in enumerate(raw or []):
            entry = entry if isinstance(entry, Mapping) else {"key": entry}
            key = as_text(entry.get("key"))
            kind = as_text(entry.get("kind"), KIND_BUILTIN) or KIND_BUILTIN
            if kind not in MODULE_KINDS:
                raise QuoteError(
                    f"{kind!r} is not a module kind.",
                    {"modules": f"Use one of: {', '.join(MODULE_KINDS)}."},
                )
            if entry.get("custom_coded"):
                raise QuoteConflict(
                    REASON_CUSTOM_MODULE_NOT_API_AUTHORABLE,
                    "A custom coded module cannot be added through an API.",
                )
            if kind == KIND_BUILTIN and key not in BUILTIN_MODULE_KEYS:
                raise QuoteConflict(
                    REASON_MODULE_KEY_UNKNOWN,
                    f"{key!r} is not a builtin module key.",
                )
            if kind == KIND_CUSTOM and as_text(entry.get("authored_via")) != "ui":
                raise QuoteConflict(
                    REASON_CUSTOM_MODULE_NEEDS_UI_PROVENANCE,
                    "A custom module must declare authored_via of ui.",
                )
            rows.append(
                {
                    "key": key,
                    "label": as_text(entry.get("label")) or key.replace("_", " ").title(),
                    "kind": kind,
                    "visible": bool(entry.get("visible", True)),
                    "position": position,
                    "authored_via": as_text(entry.get("authored_via")) or None,
                }
            )
        return rows

    # -- reading ------------------------------------------------------------ #

    def read_quote(self, quote_id: str) -> dict[str, Any]:
        record = self._require_quote(quote_id)
        return self._shape(record)

    def _require_quote(self, quote_id: str) -> dict[str, Any]:
        record = self.store.get(quote_id)
        if record is None or record["collection"] != QUOTE_COLLECTION:
            raise QuoteNotFound("No such quote.")
        return record

    def _shape(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(record.get("data") or {})
        lines = self.lines_of(str(record["id"]))
        totals = totals_for(lines, data.get("payment_schedule") or [], self.today())
        # The stored totals are what the last write computed. They are recomputed
        # here rather than read back so a response can never report a total that
        # disagrees with the lines it is returned beside.
        return {
            "id": record["id"],
            "room_id": record.get("room_id"),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            **data,
            "line_items": lines,
            "totals": totals,
            "expired": bool(
                as_text(data.get("expires_on")) and as_text(data["expires_on"]) < self.today()
            ),
        }

    def list_quotes(
        self, room_id: str | None = None, status: str | None = None
    ) -> list[dict[str, Any]]:
        rows = self.store.list(
            QUOTE_COLLECTION, room_id=room_id, limit=500, order_by="created_at", descending=False
        )
        shaped = [self._shape(row) for row in rows]
        if status:
            shaped = [q for q in shaped if q.get("status") == status]
        return shaped

    def lines_of(self, quote_id: str) -> list[dict[str, Any]]:
        """A quote's line items, in the order the seller put them in.

        Sorted by stored ``position``. ``find`` returns newest first, so without
        this the last line a seller added would appear at the top.
        """
        rows = self.store.find(LINE_ITEM_COLLECTION, {"quote_id": quote_id}, limit=1000)
        shaped = []
        for row in rows:
            data = dict(row["data"] or {})
            data["id"] = row["id"]
            data["room_id"] = row.get("room_id")
            shaped.append(data)
        return sorted(shaped, key=lambda line: as_number(line.get("position"), 0.0))

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        quotes = self.list_quotes(room_id)
        lines = [line for quote in quotes for line in quote["line_items"]]
        return {
            "quotes": len(quotes),
            "drafts": sum(1 for q in quotes if q.get("status") == STATUS_DRAFT),
            "published": sum(1 for q in quotes if q.get("status") == STATUS_PUBLISHED),
            "expired": sum(1 for q in quotes if q.get("expired")),
            "line_items": len(lines),
            "zero_priced_lines": sum(
                1 for line in lines if as_number(line.get("unit_price")) == 0.0
            ),
            "total_contract_value": money(
                sum(as_number(q.get("totals", {}).get("total_contract_value")) for q in quotes)
            ),
            "catalogue_available": catalogue_available(self.store),
            "templates": len(self.list_templates(room_id)),
        }

    # -- creating ----------------------------------------------------------- #

    def create_quote(
        self,
        room_id: str | None,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Create a quote from a deal and clone the deal's line items onto it.

        The clone is the whole point of the workflow. Each quote line is a new
        record carrying the deal line's id as ``source_line_item_id``, so the
        two rows are independent from that moment on. The header is prefilled
        from the deal: owner, currency, potential customer and address, because
        the research says the quote form "is prefilled with details from the
        opportunity record".
        """
        reference = as_text(payload.get("deal_id") or payload.get("opportunity_id"))
        found = find_deal(self.store, reference) if reference else None
        if found is None:
            raise QuoteError(
                "A quote must be created from a deal.",
                {
                    "deal_id": (
                        "Name a deal that exists, or add one to the CRM mirror first. "
                        "No deal record matches that reference."
                    )
                },
            )
        deal_record, _collection = found
        deal = read_deal(deal_record)

        template = self._template(as_text(payload.get("template_id")))
        modules = self._module_rows(
            {"modules": list(template.get("modules") or []) if template else {}},
            payload.get("modules")
            if payload.get("modules") is not None
            else (list(template.get("modules") or []) if template else None),
        )
        if not modules:
            modules = [
                {
                    "key": key,
                    "label": key.replace("_", " ").title(),
                    "kind": KIND_BUILTIN,
                    "visible": True,
                    "position": i,
                    "authored_via": None,
                }
                for i, key in enumerate(BUILTIN_MODULE_KEYS)
            ]

        title = as_text(payload.get("title")) or deal["name"] or f"Quote for {deal['account']}"
        expires_on = as_text(payload.get("expires_on"))
        if expires_on:
            try:
                date.fromisoformat(expires_on)
            except ValueError as exc:
                raise QuoteError(
                    "expires_on must be a date as YYYY-MM-DD.",
                    {"expires_on": "Use a date as YYYY-MM-DD."},
                ) from exc

        # Keys this method consumes. Anything else the caller sent is carried onto
        # the record untouched, because a quote's payload is ordinary JSON and a
        # team adding a field must need no coordination with anyone. Reading a
        # payload into a fixed dict instead would silently drop ``buyer_note``
        # and every field after it, and the caller would see a successful create
        # with their data missing.
        consumed = {
            "deal_id",
            "opportunity_id",
            "title",
            "template_id",
            "expires_on",
            "payment_schedule",
            "modules",
            "room_id",
        }
        carried = {key: value for key, value in payload.items() if key not in consumed}

        quote = self.store.create(
            QUOTE_COLLECTION,
            {
                **carried,
                **self._header(deal, deal_record, reference, title, expires_on),
                "status": STATUS_DRAFT,
                "template_id": as_text(payload.get("template_id")) or None,
                "template_type": QUOTE_TEMPLATE_TYPE,
                "payment_schedule": list(payload.get("payment_schedule") or []),
                "modules": modules,
                "totals": totals_for([], payload.get("payment_schedule") or [], self.today()),
                "catalogue_available": catalogue_available(self.store),
                "created_at": self._stamp(),
            },
            room_id=room_id or deal_record.get("room_id"),
            actor=actor,
            source=source,
        )

        cloned = 0
        for position, row in enumerate(deal_line_items(self.store, deal_record)):
            self._write_line(
                quote["id"],
                read_deal_line(row),
                position=position,
                price_from=PRICE_FROM_DEAL,
                source=source,
                actor=actor,
                room_id=quote.get("room_id"),
            )
            cloned += 1

        return {"quote": self._shape(quote), "cloned_line_items": cloned}

    # -- line items --------------------------------------------------------- #

    def _validate_line(self, data: Mapping[str, Any]) -> dict[str, Any]:
        errors: dict[str, str] = {}
        name = as_text(data.get("name"))
        if not name:
            errors["name"] = "Enter a name for the line."

        quantity = as_number(data.get("quantity"), 1.0)
        if quantity < 0:
            errors["quantity"] = "Quantity cannot be negative."

        unit_price = as_number(data.get("unit_price"), 0.0)
        if unit_price < 0:
            errors["unit_price"] = "Unit price cannot be negative."

        discount_type = as_text(data.get("discount_type"), DISCOUNT_PERCENTAGE)
        if discount_type not in DISCOUNT_TYPES:
            errors["discount_type"] = f"Use one of: {', '.join(DISCOUNT_TYPES)}."

        discount_value = as_number(data.get("discount_value"), 0.0)
        if discount_value < 0:
            errors["discount_value"] = "A discount cannot be negative."
        if discount_type == DISCOUNT_PERCENTAGE and discount_value > 100:
            errors["discount_value"] = "A percentage discount cannot exceed 100."

        tax_rate = as_number(data.get("tax_rate"), 0.0)
        if tax_rate < 0 or tax_rate > 100:
            errors["tax_rate"] = "A tax rate is a percentage between 0 and 100."

        if errors:
            raise QuoteError("That line item is not valid.", errors)

        return {
            "name": name,
            "sku": as_text(data.get("sku")),
            "description": as_text(data.get("description")),
            "quantity": quantity,
            "unit_price": unit_price,
            "discount_type": discount_type,
            "discount_value": discount_value,
            "tax_rate": tax_rate,
            "billing_start": as_text(data.get("billing_start")),
        }

    def _price_from_catalogue(self, data: Mapping[str, Any]) -> tuple[float, str, str | None]:
        """Resolve a unit price against the catalogue, if there is one.

        Returns the price, where it came from, and the tier label. The research
        says a quantity change re-evaluates tier boundaries, so this is called
        on every recompute rather than once at creation. An absent catalogue
        returns the deal's or the seller's price unchanged, which is what makes
        the quote work before WF-087 exists.
        """
        supplied = as_text(data.get("product_id"))
        product = None
        if supplied:
            record = self.store.get(supplied)
            product = (record or {}).get("data") if record else None
        if product is None:
            product = find_product(self.store, as_text(data.get("sku")), as_text(data.get("name")))
        if product is None:
            return as_number(data.get("unit_price")), PRICE_FROM_MANUAL, None

        price, tier_label, _kind = resolve_unit_price(product, as_number(data.get("quantity"), 1.0))
        if price <= 0:
            price = as_number(data.get("unit_price"))
            return price, PRICE_FROM_MANUAL, tier_label
        return price, PRICE_FROM_CATALOG, tier_label

    def _line_payload(
        self,
        quote_id: str,
        data: Mapping[str, Any],
        position: int,
        price_from: str,
        existing: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        clean = self._validate_line(data)
        if price_from == PRICE_FROM_CATALOG or (
            existing is not None and existing.get("price_source") == PRICE_FROM_CATALOG
        ):
            price, source, tier_label = self._price_from_catalogue(
                {**dict(existing or {}), **clean}
            )
            clean["unit_price"] = price
            price_from = source
        else:
            tier_label = as_text((existing or {}).get("tier_label")) or None

        clean.update(
            {
                "quote_id": quote_id,
                "position": position,
                "price_source": price_from,
                "tier_label": tier_label,
                "source_line_item_id": as_text(data.get("source_line_item_id"))
                or as_text((existing or {}).get("source_line_item_id"))
                or None,
            }
        )
        clean["amounts"] = line_amounts(clean)
        return clean

    def _write_line(
        self,
        quote_id: str,
        data: Mapping[str, Any],
        *,
        position: int,
        price_from: str,
        source: str,
        actor: str | None,
        room_id: str | None,
    ) -> dict[str, Any]:
        payload = self._line_payload(quote_id, data, position, price_from)
        # store.create, never update. The record id is minted here and belongs to
        # this line alone. See the module docstring.
        return self.store.create(
            LINE_ITEM_COLLECTION,
            payload,
            room_id=room_id,
            actor=actor,
            source=source,
        )

    def _recompute(self, quote_id: str, *, source: str, actor: str | None) -> dict[str, Any]:
        record = self._require_quote(quote_id)
        data = record["data"]
        lines = self.lines_of(quote_id)
        totals = totals_for(lines, data.get("payment_schedule") or [], self.today())
        updated = self.store.update(quote_id, {"totals": totals}, actor=actor, source=source)
        return self._shape(updated)

    def _require_draft(self, quote: Mapping[str, Any]) -> None:
        if as_text(quote.get("status")) == STATUS_PUBLISHED:
            raise QuoteConflict(
                REASON_QUOTE_FROZEN,
                "A published quote's line items cannot change.",
            )

    def add_line_item(
        self,
        quote_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Add one line to a quote, from the catalogue or typed by hand.

        ``product_id`` selects from the product library and carries the tier
        lookup with it. Without it the line is a custom line item, which the
        research names as the alternative to selecting from the library.
        """
        quote = self._shape(self._require_quote(quote_id))
        self._require_draft(quote)
        price_from = PRICE_FROM_CATALOG if as_text(payload.get("product_id")) else PRICE_FROM_MANUAL
        if price_from == PRICE_FROM_CATALOG:
            priced = self._price_from_catalogue(payload)
            payload = {**dict(payload), "unit_price": priced[0]}
            if priced[1] == PRICE_FROM_MANUAL:
                price_from = PRICE_FROM_MANUAL
        position = (
            max((as_number(line.get("position")) for line in quote["line_items"]), default=-1.0) + 1
        )
        self._write_line(
            quote_id,
            payload,
            position=position,
            price_from=price_from,
            source=source,
            actor=actor,
            room_id=quote.get("room_id"),
        )
        return {"quote": self._recompute(quote_id, source=source, actor=actor)}

    def update_line_item(
        self,
        line_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Change a line, and recompute the quote's totals.

        A change of quantity re-resolves the tier when the line is priced from
        the catalogue. That is the "pricing recalculates automatically whenever a
        line item quantity changes" rule, and it is why ``_line_payload`` is
        called on every update rather than only on create.
        """
        record = self.store.get(line_id)
        if record is None or record["collection"] != LINE_ITEM_COLLECTION:
            raise QuoteNotFound("No such line item.")
        quote_id = as_text((record["data"] or {}).get("quote_id"))
        quote = self._shape(self._require_quote(quote_id))
        self._require_draft(quote)

        existing = dict(record["data"] or {})
        merged = {**existing, **dict(payload)}
        clean = self._line_payload(
            quote_id,
            merged,
            as_number(existing.get("position")),
            str(existing.get("price_source") or PRICE_FROM_MANUAL),
            existing,
        )
        self.store.update(line_id, clean, actor=actor, source=source)
        return {"quote": self._recompute(quote_id, source=source, actor=actor)}

    def delete_line_item(self, line_id: str, *, source: str, actor: str | None) -> dict[str, Any]:
        """Remove a line from a draft quote.

        Soft-deleted, never hard. The audit row is the record of what the quote
        once contained, and a hard delete would leave the totals on the audit
        row referring to a line nobody can read.
        """
        record = self.store.get(line_id)
        if record is None or record["collection"] != LINE_ITEM_COLLECTION:
            raise QuoteNotFound("No such line item.")
        quote_id = as_text((record["data"] or {}).get("quote_id"))
        self._require_draft(self._shape(self._require_quote(quote_id)))
        self.store.delete(line_id, actor=actor, source=source)
        return {"quote": self._recompute(quote_id, source=source, actor=actor)}

    # -- the header --------------------------------------------------------- #

    # -- the header --------------------------------------------------------- #

    @staticmethod
    def _header(
        deal: Mapping[str, Any],
        deal_record: Mapping[str, Any],
        reference: str,
        title: str,
        expires_on: str,
    ) -> dict[str, Any]:
        """The header fields, written under both this workflow's names and a
        merged reader's.

        The research says the quote form "is prefilled with details from the
        opportunity record": owner, currency, potential customer and address.

        Each figure is written twice, once under the name this workflow uses and
        once under the name a merged reader looks for. See
        :data:`~dsr.quote_authoring.vocabulary.READER_SYNONYMS` for why the
        duplication is cheaper than the coupling. Building both here means there
        is no path that writes one spelling without the other, which is the only
        thing that stops the two drifting.
        """
        return {
            "title": title,
            "deal_id": deal_record["id"],
            "deal_reference": reference,
            "deal_collection": deal_record["collection"],
            "deal_name": as_text(deal.get("name")),
            "account": as_text(deal.get("account")),
            "company_name": as_text(deal.get("account")),
            "owner": as_text(deal.get("owner")),
            "currency": as_text(deal.get("currency")),
            "currency_label": as_text(deal.get("currency")),
            "address": deal.get("address"),
            "issue_date": None,
            "expiration_date": expires_on or None,
            "expires_on": expires_on or None,
        }

    def update_quote(
        self,
        quote_id: str,
        payload: Mapping[str, Any],
        *,
        source: str,
        actor: str | None,
    ) -> dict[str, Any]:
        """Edit the header, the module list, or the payment schedule.

        The payment schedule is editable on a published quote, because a schedule
        is a fact about when money moves and changing it does not change what the
        buyer was quoted. The title and the line items are not editable then.
        """
        record = self._require_quote(quote_id)
        existing = dict(record["data"] or {})
        patch: dict[str, Any] = {}

        if "title" in payload:
            title = as_text(payload.get("title"))
            if not title:
                raise QuoteError("A quote needs a title.", {"title": "Enter a title."})
            patch["title"] = title

        if "expires_on" in payload:
            expires_on = as_text(payload.get("expires_on"))
            if expires_on:
                try:
                    date.fromisoformat(expires_on)
                except ValueError as exc:
                    raise QuoteError(
                        "expires_on must be a date as YYYY-MM-DD.",
                        {"expires_on": "Use a date as YYYY-MM-DD."},
                    ) from exc
            patch["expires_on"] = expires_on or None
            # The same edit, under the name a merged reader looks for. Written here
            # rather than derived on read so the two cannot drift: a reader that
            # found a stale expiration_date would build a proposal that expires on a
            # date the seller has already moved.
            patch["expiration_date"] = expires_on or None

        if "payment_schedule" in payload:
            schedule = list(payload.get("payment_schedule") or [])
            for entry in schedule:
                if not isinstance(entry, Mapping):
                    raise QuoteError(
                        "A payment needs a due date and an amount.",
                        {"payment_schedule": "Each entry needs due_on and amount."},
                    )
                if not as_text(entry.get("due_on")):
                    raise QuoteError(
                        "A payment needs a due date and an amount.",
                        {"payment_schedule": "Each entry needs due_on and amount."},
                    )
            patch["payment_schedule"] = schedule

        if "modules" in payload:
            patch["modules"] = self._module_rows(existing, payload.get("modules"))

        if not patch:
            return {"quote": self._shape(self._require_quote(quote_id))}
        self.store.update(quote_id, patch, actor=actor, source=source)
        return {"quote": self._recompute(quote_id, source=source, actor=actor)}

    # -- publish ------------------------------------------------------------ #

    def publish(self, quote_id: str, *, source: str, actor: str | None) -> dict[str, Any]:
        """Publish the quote and write its contract value onto the deal.

        Three refusals, each of which would otherwise destroy something:
        an already-published quote, because the write-back would run twice and
        the deal's line items would be replaced by a second generation; an expired
        quote, because a buyer cannot be held to a date that has passed; and an
        empty quote, because its contract value is zero and publishing it would
        write zero onto the deal.

        The write-back is deliberately narrow. Only the total contract value
        reaches the deal amount. Subtotal, discount and tax are the quote's
        arithmetic and the research says only the TCV is copied back; copying the
        rest would overwrite the deal's own amount semantics with a document's.

        Then the deal's line items are replaced. Each is soft-deleted and a fresh
        deal-side row is written from the quote line, carrying the quote line's
        id and its own new record id. Fresh ids rather than a move, because the
        research says deal and quote line items get different record ids, and a
        soft delete rather than a hard one because the audit row is the record of
        what the deal used to be worth.
        """
        quote = self._shape(self._require_quote(quote_id))
        if as_text(quote.get("status")) == STATUS_PUBLISHED:
            raise QuoteConflict(REASON_ALREADY_PUBLISHED, "This quote is already published.")
        if quote.get("expired"):
            raise QuoteConflict(
                REASON_QUOTE_EXPIRED,
                "This quote's expiration date has passed.",
            )
        if not quote["line_items"]:
            raise QuoteConflict(
                REASON_QUOTE_IS_EMPTY,
                "A quote with no line items cannot be published.",
            )

        totals = quote["totals"]
        tcv = as_number(totals.get("total_contract_value"))

        found = find_deal(self.store, as_text(quote.get("deal_id")))
        deal_written = False
        replaced = 0
        if found is not None:
            deal_record, _collection = found
            self.store.update(
                str(deal_record["id"]),
                {"amount": tcv, "amount_source": "quote_tcv", "published_quote_id": quote_id},
                actor=actor,
                source=source,
            )
            deal_written = True

            old_lines = deal_line_items(self.store, deal_record)
            for row in old_lines:
                if str(row["id"]).startswith("embedded:"):
                    continue
                self.store.delete(str(row["id"]), actor=actor, source=source)
            collection = DEAL_LINE_ITEM_COLLECTION
            for line in quote["line_items"]:
                self.store.create(
                    collection,
                    {
                        "deal_id": deal_record["id"],
                        "quote_line_item_id": line["id"],
                        "name": line.get("name"),
                        "sku": line.get("sku"),
                        "quantity": as_number(line.get("quantity")),
                        "unit_price": as_number(line.get("unit_price")),
                        "discount_type": line.get("discount_type"),
                        "discount_value": as_number(line.get("discount_value")),
                        "tax_rate": as_number(line.get("tax_rate")),
                        "published_quote_id": quote_id,
                    },
                    room_id=deal_record.get("room_id"),
                    actor=actor,
                    source=source,
                )
                replaced += 1

        published = self.store.update(
            quote_id,
            {
                "status": STATUS_PUBLISHED,
                "published_at": self._stamp(),
                "deal_amount_written": tcv,
                "deal_line_items_replaced": replaced,
            },
            actor=actor,
            source=source,
        )
        return {
            "quote": self._shape(published),
            "deal_amount_written": tcv if deal_written else None,
            "replaced_deal_line_items": replaced,
        }

    # -- the product library ------------------------------------------------ #

    @staticmethod
    def search_catalogue(store: RecordStore, term: str = "", limit: int = 25) -> dict[str, Any]:
        """The product library search, and whether there is a library at all."""
        return {
            "catalogue_available": catalogue_available(store),
            "products": search_products(store, term, limit),
        }
