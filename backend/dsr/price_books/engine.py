"""The one module in this package that touches the store.

Everything above it is a pure function over plain values, so the arithmetic can be
read and tested without a database. This module is the boundary: it reads and
writes through :class:`~dsr.store.RecordStore`, and every write it makes is
audited by the same transaction that makes the change.

The audit rule
--------------

Every writing method takes ``source`` as a keyword-only argument with no default,
and the router builds that value from ``router.prefix`` and the concrete path of
the request. Two consequences, both deliberate:

* Omitting ``source`` is a ``TypeError`` at the call site rather than an audit row
  with no route on it, so the defect is caught by the first test rather than by a
  person reading the log.
* The recorded source substitutes the real ids, so the audit row reads
  ``POST /api/wf-087/products/wf087_product_ab12`` and can be traced back to the
  request that caused it rather than naming a template nobody can call.

A test in ``test_wf087_http.py`` reads every audit row this module writes and
matches it against the routes the feature host actually mounted.

The clock
---------

``now`` is injectable so a test can freeze it. The seeder passes the time the
core dataset was built with, so a seeded catalogue and the quotes that read it
share one date rather than disagreeing by the length of a seed run.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from dsr.price_books import errors as errors_mod, rules, vocabulary as vocab
from dsr.price_books.errors import CatalogueNotFound, refuse
from dsr.store import RecordStore


def utcnow() -> datetime:
    """The current instant, in UTC.

    Module level and named, not inlined at each call site, so a test can point the
    engine at a fixed clock and get the same answer the seeder does.
    """
    return datetime.now(timezone.utc)


class CatalogueEngine:
    """Products, price books and price-book rows over one store.

    Built per request from ``StoreDep`` rather than cached on ``app.state``.
    Caching it would mean the engine holds a store from the first request that
    built it, and swapping the store for a test would have to reach into the app
    as well as into this object.
    """

    def __init__(self, store: RecordStore, *, now: Callable[[], datetime] | None = None) -> None:
        self.store = store
        self._clock = now or utcnow

    def now(self) -> datetime:
        """The engine's current instant, which is the clock the test gave it."""
        return self._clock()

    def stamp(self) -> str:
        """The current instant as the ISO string the catalogue stores."""
        return self.now().isoformat(timespec="milliseconds")

    # -- reads ------------------------------------------------------------- #

    def list_products(
        self,
        *,
        term: str = "",
        active_only: bool = False,
        limit: int = 200,
        room_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """The product library, newest first, with each row's pricing resolved.

        ``term`` matches the name or the SKU, case-insensitively, in process
        rather than through ``store.find``. The dynamic index can match one JSON
        path at a time and cannot do a case-insensitive contains across two, so a
        query parameter would either be exact or would need a separate code path.
        The library is a human-sized list, so reading it and filtering is cheaper
        to reason about than a second search path that can disagree with this one.

        The whole list is read and then trimmed here for the same reason: ``store``
        owns paging, and a search that pages after filtering returns a short page
        with no indication that more matched.
        """
        rows = self.store.list(vocab.PRODUCTS, limit=1000, room_id=room_id)
        found = [row for row in rows if _matches(row.get("data") or {}, term)]
        if active_only:
            found = [
                row for row in found if (row.get("data") or {}).get("active", True) is not False
            ]
        found.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        return [self.product_view(row) for row in found[: max(1, int(limit))]]

    def get_product(self, product_id: str) -> dict[str, Any]:
        """One product, or a refusal naming the id that did not resolve."""
        return self.product_view(
            self._require(vocab.PRODUCTS, product_id, "not_a_product", "Product")
        )

    def list_price_books(
        self, *, limit: int = 200, room_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Every price book, each with the currencies it supports and its gaps.

        The gap list is computed on read rather than stored, because it is
        derived from the rows and a stored copy would be stale the moment one row
        is edited.
        """
        rows = self.store.list(vocab.PRICE_BOOKS, limit=1000, room_id=room_id)
        return [self.price_book_view(row) for row in rows[: max(1, int(limit))]]

    def get_price_book(self, book_id: str) -> dict[str, Any]:
        """One price book, or a refusal naming the id that did not resolve."""
        return self.price_book_view(
            self._require(vocab.PRICE_BOOKS, book_id, "not_a_price_book", "Price book")
        )

    def list_book_items(
        self,
        book_id: str = "",
        *,
        product_id: str = "",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """The price-book rows, filtered by book or by product.

        Both filters go through ``find`` rather than a post-filter, because the
        dynamic index resolves a dotted JSON path and this is exactly the shape it
        was built for. An empty ``where`` reads the collection, so one code path
        covers both filters and the unfiltered case.
        """
        where: dict[str, Any] = {}
        if book_id:
            where["price_book_id"] = book_id
        if product_id:
            where["product_id"] = product_id
        rows = self.store.find(vocab.PRICE_BOOK_ITEMS, where, limit=max(1, int(limit)))
        return [self.book_item_view(row) for row in rows]

    def find_book_item(self, product_id: str, book_id: str) -> dict[str, Any] | None:
        """The row for one product in one book, or ``None`` when there is none.

        Used by the pricing route rather than by a write, because an absent row is
        not an error there: the product's own price stands in its place.
        """
        rows = self.store.find(
            vocab.PRICE_BOOK_ITEMS,
            {"product_id": product_id, "price_book_id": book_id},
            limit=1,
        )
        return rows[0] if rows else None

    # -- writes ------------------------------------------------------------ #

    def create_product(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Validate and save one product.

        Validation runs before the write rather than in a request validator, so
        the seeder and a route get the same refusals: a seeded product that the
        API would refuse is a demo row a person can see failing.
        """
        data = self._product_payload(payload)
        record = self.store.create(
            vocab.PRODUCTS, data, room_id=room_id, actor=actor, source=source
        )
        return self.product_view(record)

    def edit_product(
        self,
        product_id: str,
        changes: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Merge a patch into a product, revalidating the whole result.

        Revalidating the merged record rather than the patch is what makes a patch
        of ``{"pricing_method": "graduated"}`` on a flat product a refusal rather
        than a record with a graduated method and no tiers.
        """
        current = self._require(vocab.PRODUCTS, product_id, "not_a_product", "Product")
        merged = {**(current.get("data") or {}), **dict(changes)}
        data = self._product_payload(merged)
        record = self.store.update(product_id, data, actor=actor, source=source)
        return self.product_view(record)

    def retire_product(
        self,
        product_id: str,
        *,
        active: bool,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Switch a product's Active toggle.

        The research's rule: "toggle Active off to retire a product from deals,
        quotes and subscriptions without deleting it". So the record stays, its
        history stays, and it simply stops being offered for new work. Chosen at
        confidence 0.97, audit ``jev-20261013T111944-8144-84487``.

        This is not the delete. Delete is a soft delete through the audited store,
        which removes the row from the catalogue and leaves the audit trail
        intact, and the two are separate calls because a retired product is still
        a product that exists.
        """
        self._require(vocab.PRODUCTS, product_id, "not_a_product", "Product")
        record = self.store.update(product_id, {"active": bool(active)}, actor=actor, source=source)
        return self.product_view(record)

    def remove_product(
        self,
        product_id: str,
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Remove a product from the catalogue, keeping it in the audit trail.

        The price-book rows go first, in one transaction, then the product. That
        order is the whole point: ``AuditedWriter`` exposes only ``create`` and
        ``update``, so a delete cannot join a transaction opened through the
        writer, and the two calls cannot be one commit. Removing the rows first
        means the only reachable state is "the rows are gone and the product is
        still there", which is recoverable. The other order would leave a price
        book naming a product that does not exist, which is not.
        """
        self._require(vocab.PRODUCTS, product_id, "not_a_product", "Product")
        rows = self.store.find(vocab.PRICE_BOOK_ITEMS, {"product_id": product_id}, limit=1000)
        if rows:
            self.store.bulk_delete(
                [row["id"] for row in rows], actor=actor, source=source, hard=False
            )
        self.store.delete(product_id, actor=actor, source=source)
        return {
            "id": product_id,
            "collection": vocab.PRODUCTS,
            "removed_rows": len(rows),
            "status": "removed",
        }

    def create_price_book(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Save one price book, with its supported currencies validated."""
        data = self._price_book_payload(payload)
        record = self.store.create(
            vocab.PRICE_BOOKS, data, room_id=room_id, actor=actor, source=source
        )
        return self.price_book_view(record)

    def edit_price_book(
        self,
        book_id: str,
        changes: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Merge a patch into a price book, revalidating the whole result."""
        current = self._require(vocab.PRICE_BOOKS, book_id, "not_a_price_book", "Price book")
        merged = {**(current.get("data") or {}), **dict(changes)}
        data = self._price_book_payload(merged)
        record = self.store.update(book_id, data, actor=actor, source=source)
        return self.price_book_view(record)

    def add_book_item(
        self,
        payload: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
        room_id: str | None = None,
    ) -> dict[str, Any]:
        """Add one product to one price book, refusing the second row.

        "Only one instance of each product can appear in a price book at a time",
        error ``DUPLICATE_PRODUCTS``, 409 (developers.hubspot.com/docs/api-reference/
        latest/revenue/price-books/guide). The check is here rather than in the
        router so the seeder and the route agree.

        A bundle is refused outright, also from the vendor: ``BUNDLE_PRODUCTS_
        NOT_ALLOWED``, 400.

        A row may be saved with a price in only some of the book's currencies.
        The vendor does not refuse the save: it blocks activation with
        ``PRICE_BOOK_PRODUCTS_MISSING_PRICES``. That split is reproduced here, so
        an administrator can build a book row by row and switch it on at the end.
        """
        product_id = str(dict(payload).get("product_id") or "").strip()
        book_id = str(dict(payload).get("price_book_id") or "").strip()
        if not product_id:
            raise refuse("price_book_row_needs_a_product")
        if not book_id:
            raise refuse("price_book_row_needs_a_book")

        product = self._require(vocab.PRODUCTS, product_id, "not_a_product", "Product")
        book = self._require(vocab.PRICE_BOOKS, book_id, "not_a_price_book", "Price book")

        if rules.is_bundle(product.get("data") or {}):
            raise refuse(
                "bundle_products_are_not_in_a_price_book",
                **{"product_id": "A bundle cannot be priced by a book."},
            )

        if self.store.find(
            vocab.PRICE_BOOK_ITEMS,
            {"product_id": product_id, "price_book_id": book_id},
            limit=1,
        ):
            raise refuse("one_row_per_product_and_book")

        data = self._book_item_payload(payload, book.get("data") or {}, product.get("data") or {})
        record = self.store.create(
            vocab.PRICE_BOOK_ITEMS, data, room_id=room_id, actor=actor, source=source
        )
        return self.book_item_view(record)

    def edit_book_item(
        self,
        item_id: str,
        changes: Mapping[str, Any],
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Merge a patch into one price-book row."""
        current = self._require(
            vocab.PRICE_BOOK_ITEMS, item_id, "not_a_price_book_item", "Price book row"
        )
        book = self.store.get(str((current.get("data") or {}).get("price_book_id") or ""))
        product = self.store.get(str((current.get("data") or {}).get("product_id") or ""))
        merged = {**(current.get("data") or {}), **dict(changes)}
        data = self._book_item_payload(
            merged,
            (book or {}).get("data") or {},
            (product or {}).get("data") or {},
        )
        record = self.store.update(item_id, data, actor=actor, source=source)
        return self.book_item_view(record)

    def remove_book_item(
        self,
        item_id: str,
        *,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Remove one price-book row, keeping it in the audit trail."""
        record = self.store.delete(
            self._require(
                vocab.PRICE_BOOK_ITEMS, item_id, "not_a_price_book_item", "Price book row"
            )["id"],
            actor=actor,
            source=source,
        )
        return {"id": record["id"], "collection": record["collection"], "status": "removed"}

    def activate_price_book(
        self,
        book_id: str,
        *,
        active: bool,
        actor: str | None,
        source: str,
    ) -> dict[str, Any]:
        """Switch a price book on or off, refusing to switch on an incomplete one.

        The vendor's validate endpoint checks that the book contains at least one
        product, that every product has a price defined for its pricing model, and
        that "every product has a price for each of the book's supported
        currencies". The error for the last is ``PRICE_BOOK_PRODUCTS_MISSING_
        PRICES``, 400. Reproduced here so the book cannot go live naming a product
        it cannot price.
        """
        book = self._require(vocab.PRICE_BOOKS, book_id, "not_a_price_book", "Price book")
        if active:
            rows = self.store.find(vocab.PRICE_BOOK_ITEMS, {"price_book_id": book_id}, limit=1000)
            if not rows:
                raise refuse(
                    "price_book_is_not_complete",
                    **{"price_book_id": "Add at least one product before activating the book."},
                )
            gaps = rules.missing_currencies(book.get("data") or {}, rows)
            if gaps:
                detail = "; ".join(
                    f"{product_id}: {', '.join(codes)}"
                    for product_id, codes in sorted(gaps.items())
                )
                raise refuse("price_book_is_not_complete", price_book_id=detail)
        record = self.store.update(book_id, {"active": bool(active)}, actor=actor, source=source)
        return self.price_book_view(record)

    # -- pricing ----------------------------------------------------------- #

    def price(
        self,
        product_id: str,
        quantity: Any,
        *,
        price_book_id: str = "",
        currency: str = "",
        actor: str | None = None,
        source: str = "",
    ) -> dict[str, Any]:
        """Price one line, and say which layer produced the figure.

        This is the moment the research describes: "at quote-line creation the tier
        table is evaluated against quantity to select the effective unit price;
        the price-book layer then overrides the product default price". It reads
        and writes nothing, so it takes no actor and writes no audit row: a price
        lookup is a question, not a change.
        """
        product = self._require(vocab.PRODUCTS, product_id, "not_a_product", "Product")
        data = product.get("data") or {}

        book: dict[str, Any] = {}
        item: dict[str, Any] = {}
        if price_book_id:
            book_record = self._require(
                vocab.PRICE_BOOKS, price_book_id, "not_a_price_book", "Price book"
            )
            book = book_record.get("data") or {}
            found = self.find_book_item(product_id, price_book_id)
            item = (found or {}).get("data") or {}

        answered = rules.effective_unit_price(
            data,
            quantity,
            price_book=book,
            price_book_item=item,
            currency=currency,
        )
        return {
            "product_id": product_id,
            "sku": data.get("sku"),
            "name": data.get("name"),
            "retired": data.get("active", True) is False,
            "price_book_id": price_book_id or None,
            "price_book_applied": bool(item)
            and str(item.get("state") or vocab.BOOK_ITEM_ACTIVE) == vocab.BOOK_ITEM_ACTIVE,
            **answered,
        }

    def summary(self) -> dict[str, Any]:
        """What the catalogue holds, for the page header.

        Read on every render of the page, so it is four counts and a currency list
        rather than a figure computed from every row: a header that took a
        second to draw would be a header nobody reads.
        """
        products = self.store.list(vocab.PRODUCTS, limit=1000)
        books = self.store.list(vocab.PRICE_BOOKS, limit=1000)
        rows = self.store.list(vocab.PRICE_BOOK_ITEMS, limit=1000)

        tiered = 0
        active = 0
        currencies: set[str] = set()
        for record in products:
            data = record.get("data") or {}
            if data.get("active", True) is not False:
                active += 1
            if rules.is_tiered(data.get("pricing_method")) or rules.read_tier_table(data):
                tiered += 1
            currencies.update(rules.price_map(data))
        for record in books:
            currencies.update(rules.book_currencies(record.get("data") or {}))

        return {
            "products": len(products),
            "active_products": active,
            "retired_products": len(products) - active,
            "tiered_products": tiered,
            "price_books": len(books),
            "active_price_books": sum(
                1 for record in books if (record.get("data") or {}).get("active", True) is not False
            ),
            "price_book_rows": len(rows),
            "currencies": sorted(currencies),
            "generated_at": self.stamp(),
        }

    # -- validation -------------------------------------------------------- #

    def _product_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """The product record for a save, with every field checked.

        A product must end up with one of the two pricing shapes the research
        names: a flat price in a currency, or a tier table. Anything else is a
        product a quote cannot price, and the refusal says which field is missing.
        """
        body = dict(payload)
        errors: dict[str, str] = {}

        name = str(body.get("name") or "").strip()
        if not name:
            errors["name"] = "Give the product a name."
        sku = str(body.get("sku") or "").strip()

        raw_tiers = rules.read_tier_table(body)
        table = rules.tiers(raw_tiers) if raw_tiers else []
        method_text = body.get("pricing_method")
        if not table and method_text is None:
            method = vocab.METHOD_FLAT
        else:
            method = rules.normalise_pricing_method(
                method_text or (vocab.METHOD_GRADUATED if table else vocab.METHOD_FLAT)
            )

        prices: dict[str, float] = {}
        for key in vocab.PRICE_MAP_KEYS:
            raw = body.get(key)
            if isinstance(raw, Mapping):
                for code, value in raw.items():
                    text = str(code).strip().upper()
                    if text:
                        prices[text] = rules.require_money(value, f"{key}.{code}")
        if not prices:
            single = None
            for key in vocab.PRODUCT_PRICE_KEYS:
                if body.get(key) is not None:
                    single = body[key]
                    break
            if single is not None:
                code = str(body.get(vocab.CURRENCY_FIELD) or rules.DEFAULT_CURRENCY).strip().upper()
                prices[code or rules.DEFAULT_CURRENCY] = rules.require_money(
                    single, vocab.CURRENCY_KEY_FOR_PRICE
                )

        if not table and not prices:
            errors["prices"] = "Give the product a price in a currency, or a tier table."
        if method in vocab.TIERED_PRICING_METHODS and not table:
            # A graduated product with no tiers prices from its flat price, which
            # is a different method wearing this one's name. Refusing it means the
            # method on a product always describes the arithmetic that will run.
            errors["tiers"] = (
                f"This product is priced {method}, which reads a tier table. Add the "
                "ranges, or set the method to flat."
            )
        if errors:
            raise errors_mod.CatalogueRefusal("product_needs_a_price", errors=errors)

        currency = str(body.get(vocab.CURRENCY_FIELD) or "").strip().upper()
        if table and currency and len(currency) != 3:
            errors["currency"] = "Use a three-letter currency code."
        if table and not currency and prices:
            currency = next(iter(sorted(prices)))

        data: dict[str, Any] = {
            "name": name,
            "sku": sku,
            "description": str(body.get("description") or "").strip(),
            "tax_category": str(body.get("tax_category") or "").strip(),
            "billing_frequency": rules.normalise_billing(body.get("billing_frequency")),
            "product_type": str(body.get("product_type") or vocab.PRODUCT_TYPE_REGULAR)
            .strip()
            .lower(),
            "active": body.get("active", True) is not False,
            "pricing_method": method,
            "tiers": table,
            "prices": prices,
            "currency": currency,
            "created_at": body.get("created_at") or self.stamp(),
            "updated_at": self.stamp(),
        }
        image = str(body.get("image_url") or "").strip()
        if image:
            data["image_url"] = image
        return data

    def _price_book_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """The price-book record for a save.

        The supported currencies are validated here rather than in the router, and
        at least one is required. The vendor's API types ``supportedCurrencies`` as
        an array and lists ``CANNOT_REMOVE_LAST_CURRENCY``, so the plural is the
        shape; the research quotes a single-currency dropdown, which is read as
        well so a mirror of that shape resolves. See ``vocabulary.CURRENCIES``.
        """
        body = dict(payload)
        errors: dict[str, str] = {}

        name = str(body.get("name") or "").strip()
        if not name:
            errors["name"] = "Give the price book a name."

        codes: list[str] = []
        raw: Any = None
        for key in vocab.BOOK_CURRENCY_LIST_KEYS:
            if body.get(key):
                raw = body[key]
                break
        if raw is None:
            errors[vocab.SUPPORTED_CURRENCIES] = (
                "Choose at least one currency. A price book names the currencies it prices in."
            )
            raise errors_mod.CatalogueRefusal("price_book_needs_a_currency", errors=errors)
        codes = rules.normalise_book_currencies(raw)
        if errors:
            raise errors_mod.CatalogueRefusal("price_book_needs_a_name", errors=errors)

        return {
            "name": name,
            "description": str(body.get("description") or "").strip(),
            vocab.SUPPORTED_CURRENCIES: codes,
            "segment": str(body.get("segment") or "").strip(),
            "region": str(body.get("region") or "").strip(),
            # A new book starts switched off, because the researched flow is
            # create, add products, then toggle Inactive off to activate. A book
            # that is on the moment it exists is offering prices for products
            # nobody has put in it yet.
            "active": body.get("active", False) is True,
            "created_at": body.get("created_at") or self.stamp(),
            "updated_at": self.stamp(),
        }

    def _book_item_payload(
        self,
        payload: Mapping[str, Any],
        book: Mapping[str, Any],
        product: Mapping[str, Any],
    ) -> dict[str, Any]:
        """One price-book row for a save, checked against the book it joins.

        A row that carries no price is a gap rather than a refusal, because the
        vendor blocks activation rather than the save, and an administrator
        building a book row by row needs the incomplete rows to exist to be able
        to complete them.
        """
        body = dict(payload)
        errors: dict[str, str] = {}

        prices: dict[str, float] = {}
        for key in vocab.PRICE_MAP_KEYS:
            raw = body.get(key)
            if isinstance(raw, Mapping):
                for code, value in raw.items():
                    text = str(code).strip().upper()
                    if text:
                        prices[text] = rules.require_money(value, f"{key}.{code}")
        if not prices:
            single = None
            for key in vocab.PRODUCT_PRICE_KEYS:
                if body.get(key) is not None:
                    body[key] = rules.require_money(body[key], "price")
                    single = body[key]
                    break
            if single is not None:
                codes = rules.book_currencies(book) or [
                    str(product.get(vocab.CURRENCY_FIELD) or rules.DEFAULT_CURRENCY).upper()
                ]
                if len(codes) == 1:
                    prices[codes[0]] = rules.money(single)
                else:
                    errors["price"] = (
                        "This price book prices in "
                        f"{', '.join(codes)}. Give the row a price in each of them."
                    )

        raw_tiers = rules.read_tier_table(body)
        table = rules.tiers(raw_tiers) if raw_tiers else []
        method_text = body.get("pricing_method")
        method = (
            rules.normalise_pricing_method(
                method_text or (vocab.METHOD_GRADUATED if table else vocab.METHOD_FLAT)
            )
            if (table or method_text)
            else ""
        )

        state = str(body.get("state") or vocab.BOOK_ITEM_ACTIVE).strip().lower()
        if state not in vocab.BOOK_ITEM_STATES:
            state = vocab.BOOK_ITEM_ACTIVE

        if errors:
            raise errors_mod.CatalogueRefusal("price_book_row_missing_a_price", errors=errors)

        data: dict[str, Any] = {
            "product_id": str(body.get("product_id") or "").strip(),
            "price_book_id": str(body.get("price_book_id") or "").strip(),
            "prices": prices,
            "billing_frequency": rules.normalise_billing(
                body.get("billing_frequency") or product.get("billing_frequency")
            ),
            "term": str(body.get("term") or "").strip(),
            "tiers": table,
            "state": state,
            "created_at": body.get("created_at") or self.stamp(),
            "updated_at": self.stamp(),
        }
        if method:
            data["pricing_method"] = method
        return data

    # -- views ------------------------------------------------------------- #

    def product_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A product as the API returns it, with its pricing summarised.

        The summary is computed rather than stored so a page cannot show a tier
        count that disagrees with the tiers it is about to render.
        """
        data = dict(record.get("data") or {})
        view = {
            "id": record.get("id"),
            "collection": record.get("collection"),
            "revision": record.get("revision"),
        }
        view.update(data)
        view.update(rules.summary_row(data))
        importable, why = rules.is_importable(data)
        view["is_importable"] = importable
        view["import_refusal"] = why
        view["is_bundle"] = rules.is_bundle(data)
        return view

    def price_book_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """A price book with its currencies, its rows and its gaps."""
        data = dict(record.get("data") or {})
        rows = self.store.find(
            vocab.PRICE_BOOK_ITEMS, {"price_book_id": str(record.get("id") or "")}, limit=1000
        )
        gaps = rules.missing_currencies(data, rows)
        view = {
            "id": record.get("id"),
            "collection": record.get("collection"),
            "revision": record.get("revision"),
        }
        view.update(data)
        view["currencies"] = rules.book_currencies(data)
        view["row_count"] = len(rows)
        view["missing_prices"] = gaps
        view["is_complete"] = not gaps and bool(rows)
        return view

    def book_item_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """One price-book row, with the product and book it names, when present.

        The two names are resolved on read so a page listing a book does not have
        to make one request per row to show what the row prices.
        """
        data = dict(record.get("data") or {})
        view = {
            "id": record.get("id"),
            "collection": record.get("collection"),
            "revision": record.get("revision"),
        }
        view.update(data)
        product = self.store.get(str(data.get("product_id") or ""))
        book = self.store.get(str(data.get("price_book_id") or ""))
        view["product_name"] = (product or {}).get("data", {}).get("name")
        view["product_sku"] = (product or {}).get("data", {}).get("sku")
        view["price_book_name"] = (book or {}).get("data", {}).get("name")
        view["missing_currencies"] = [
            code
            for code in rules.book_currencies((book or {}).get("data") or {})
            if rules.book_item_price(data, code) is None
        ]
        return view

    # -- internals --------------------------------------------------------- #

    def _require(self, collection: str, record_id: str, code: str, label: str) -> dict[str, Any]:
        """Fetch a live record or raise the refusal this collection answers with.

        The message names the id rather than saying "not found", because the caller
        passed the id and repeating it is the one piece of information that makes
        the refusal actionable.
        """
        record = self.store.get(record_id) if record_id else None
        if record is None or record.get("collection") != collection:
            raise CatalogueNotFound(code, label, record_id)
        return record


def _matches(data: Mapping[str, Any], term: str) -> bool:
    """Whether a product row matches a free-text term on its name or SKU.

    An empty term matches everything, which is what a page wants before anybody
    types. Compared in lower case on both sides so "north" finds "Northwind" and a
    SKU in either case is found.
    """
    needle = str(term or "").strip().lower()
    if not needle:
        return True
    for key in ("name", "sku", "description"):
        if needle in str(data.get(key) or "").lower():
            return True
    return False
