"""The writes and the reads for WF-093.

Everything that touches the store lives here. The rules are in
:mod:`dsr.quoting_proposals.rules`, the vocabulary in
:mod:`dsr.quoting_proposals.vocabulary`, and neither knows the store exists.

Three things this engine owns
-----------------------------

**Templates.** A template is layout, module order, branding tokens and bindings. It is
never a rendered document. A template may be authored, read and listed here, and one of
its fields reports whether it carries a custom-coded module, because the evidence says an
API user "can select templates that have custom modules included on them" but cannot add
one.

**Brands.** A brand kit carries the colour tokens a template associates with. Brands are
separate from templates because the specification says a brand drives logo and brand-kit
colours while a template can override them, and collapsing the two would make the
override meaningless.

**Documents.** The merge result. One row per instantiation, stored as the quote's
presentation layer. It is a snapshot rather than a live view, which is what makes the
non-retroactive rule work: a published row holds the branding it was rendered with.

Every write goes through the :class:`~dsr.store.RecordStore` the caller hands in, so the
audit row lands in the same transaction as the change. Every ``source`` a write records
is built from the router in the feature module, never written as a literal here, so an
audit row cannot name a route the app stopped serving.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Callable

from dsr.quoting_proposals import inferences, rules, vocabulary as vocab
from dsr.store import RecordStore

#: The PandaDoc content-placeholder bound, recorded and reported rather than enforced.
#:
#: There is no content library in this product, so nothing could enforce it, and a bound
#: nothing can check is a bound nobody should rely on. It is stored on every document so
#: a reader can see the researched figure and the fact that it is not applied here.
PLACEHOLDER_BOUNDS: dict[str, Any] = {
    "min": vocab.PLACEHOLDER_MIN_ITEMS,
    "max": vocab.PLACEHOLDER_MAX_ITEMS,
    "evidence": vocab.CAP_EVIDENCE["placeholder_items"],
    "enforced": False,
    "note": (
        "This workflow has no content library, so the bound is recorded and reported "
        "rather than enforced as a refusal."
    ),
}


class ProposalEngine:
    """One merge, over the process-wide audited store.

    Built per request by the feature module, which is the shape the rest of this product
    uses: the engine holds nothing beyond the store and a clock, so building it per
    request leaves every seam overridable in a test.
    """

    def __init__(
        self,
        store: RecordStore,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self._now = now or rules.utcnow

    # -- templates ---------------------------------------------------------- #

    def save_template(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
        template_id: str | None = None,
    ) -> dict[str, Any]:
        """Create or replace one template definition.

        The write is a merge-patch when ``template_id`` names an existing template, so a
        caller that changes one binding does not have to resend the whole definition. The
        response says which of the two happened, because a create that silently overwrote
        a template would be indistinguishable from an update in the audit log's summary.
        """

        name = str(payload.get("name") or "").strip()
        if not name:
            raise rules.ProposalRefusal(
                "A template needs a name.",
                {"name": "A template needs a name."},
            )

        key = rules.normalise_key(payload.get("key") or name)
        existing = self._find_template(key, room_id)
        binding_target = template_id or (existing["id"] if existing else None)
        hidden = self._hidden(payload, existing)

        data = {
            "name": name,
            "key": key,
            "description": str(payload.get("description") or "").strip(),
            # The hidden list is computed first and handed to the order, so a module the
            # caller hid is absent from the order rather than present in it and skipped
            # later. One list, one answer.
            "modules": rules.module_order(payload.get("modules"), hidden),
            "hidden": hidden,
            "bindings": {
                module: rules.validate_bindings(payload.get("bindings", {}).get(module))
                for module in vocab.MODULES
                if isinstance(payload.get("bindings"), Mapping)
                and module in (payload.get("bindings") or {})
            },
            "branding": self._template_branding(payload, existing),
            "brand_id": str(payload.get("brand_id") or "").strip(),
            "terms": str(payload.get("terms") or "").strip(),
            "cover_letter": str(payload.get("cover_letter") or "").strip(),
            "executive_summary": str(payload.get("executive_summary") or "").strip(),
            "default_expiry_days": self._expiry_days(
                payload.get("default_expiry_days"),
                existing,
            ),
            "carry_custom_modules": bool(payload.get("carry_custom_modules", False)),
            "updated_at": rules.stamp(self._now()),
        }

        if binding_target and (existing is None or existing["id"] == binding_target):
            record = self.store.update(
                binding_target,
                data,
                actor=actor,
                source=source,
            )
            action = "updated"
        else:
            data["created_at"] = rules.stamp(self._now())
            record = self.store.create(
                vocab.TEMPLATES,
                data,
                room_id=room_id or (existing.get("room_id") if existing else None),
                actor=actor,
                source=source,
            )
            action = "created"

        return {"action": action, **self._template_view(record)}

    def _expiry_days(self, value: Any, existing: Mapping[str, Any] | None) -> int:
        """The default expiry window a template applies, from the payload or the stored row.

        A template that states no window gets 30 days. The specification names an
        expiration date as a header field and sets no figure for it, so this is a derived
        default rather than a researched one, and the document reports which level
        supplied the resulting date. It is refused when it is not a whole positive number
        rather than clamped, so a template never stores a window nobody asked for.
        """

        if value in (None, ""):
            if existing is not None and existing.get("data", {}).get("default_expiry_days"):
                return int(existing["data"]["default_expiry_days"])
            return 30
        if isinstance(value, bool):
            raise rules.ProposalRefusal(
                "default_expiry_days must be a whole number of days.",
                {"default_expiry_days": "default_expiry_days must be a whole number of days."},
            )
        try:
            days = int(value)
        except (TypeError, ValueError) as exc:
            raise rules.ProposalRefusal(
                "default_expiry_days must be a whole number of days.",
                {"default_expiry_days": "default_expiry_days must be a whole number of days."},
            ) from exc
        if days < 1:
            raise rules.ProposalRefusal(
                "default_expiry_days must be at least one day.",
                {"default_expiry_days": "default_expiry_days must be at least 1."},
            )
        return days

    def _hidden(self, payload: Mapping[str, Any], existing: Mapping[str, Any] | None) -> list[str]:
        """The modules a template hides, from the payload or from the stored template."""

        if "hidden" in payload and isinstance(payload.get("hidden"), (list, tuple)):
            return sorted({rules.normalise_module(name) for name in payload["hidden"]})
        if isinstance(payload.get("modules"), (list, tuple)):
            hidden: set[str] = set()
            for entry in payload["modules"]:
                if isinstance(entry, Mapping) and (
                    entry.get("hidden") or not entry.get("enabled", True)
                ):
                    hidden.add(rules.normalise_module(entry.get("module")))
            return sorted(hidden)
        return sorted(existing.get("data", {}).get("hidden", [])) if existing else []

    def _template_branding(
        self, payload: Mapping[str, Any], existing: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        """The template's own branding tokens, checked and normalised.

        ``override_brand_kit`` is kept out of the token map and read separately by
        :func:`~dsr.quoting_proposals.rules.resolve_branding`, because it is a toggle
        rather than a token and folding it into the map would make it look like one.
        """

        stored = dict(existing.get("data", {}).get("branding", {})) if existing else {}
        incoming = payload.get("branding")
        merged = {**stored, **dict(incoming or {})} if isinstance(incoming, Mapping) else stored
        tokens: dict[str, Any] = {}
        for token in vocab.BRAND_TOKENS:
            if token not in merged:
                continue
            value = merged.get(token)
            if value in (None, ""):
                continue
            tokens[token] = (
                rules.normalise_colour(value, token)
                if token in ("accent", "accent_soft")
                else value
            )
        if vocab.OVERRIDE_BRAND_KIT in merged:
            tokens[vocab.OVERRIDE_BRAND_KIT] = bool(merged[vocab.OVERRIDE_BRAND_KIT])
        if vocab.FALLBACK_NAME_WHEN_LOGO_ABSENT in merged:
            tokens[vocab.FALLBACK_NAME_WHEN_LOGO_ABSENT] = bool(
                merged[vocab.FALLBACK_NAME_WHEN_LOGO_ABSENT]
            )
        return tokens

    def _find_template(self, key: str, room_id: str | None) -> dict[str, Any] | None:
        found = self.store.find(vocab.TEMPLATES, {"key": key})
        if room_id:
            same_room = [row for row in found if row.get("room_id") == room_id]
            if same_room:
                return same_room[0]
        return found[0] if found else None

    def templates(self, room_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.list(vocab.TEMPLATES, room_id=room_id, limit=200)
        return [self._template_view(row) for row in rows]

    def template(self, template_id: str) -> dict[str, Any] | None:
        record = self._require_template(template_id)
        return self._template_view(record)

    def _require_template(self, template_id: str) -> dict[str, Any]:
        record = self.store.get(template_id)
        if record is None or record.get("collection") != vocab.TEMPLATES:
            raise rules.ProposalNotFound(f"No template {template_id!r}.")
        return record

    def _template_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """One template as the page reads it, with its module order and its advisory.

        ``carry_custom_modules`` is surfaced because the evidence says an API user can
        select a template that has custom modules but cannot add one. A list that hid the
        flag would leave a caller unable to find such a template, and a caller that could
        add one would be doing the thing the evidence forbids.
        """

        data = record.get("data", {})
        modules = data.get("modules") or list(vocab.MODULES)
        hidden = set(data.get("hidden") or ())
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "revision": record.get("revision"),
            "key": data.get("key"),
            "name": data.get("name"),
            "description": data.get("description"),
            "modules": list(modules),
            "hidden": sorted(hidden),
            "rendered": [name for name in modules if name not in hidden],
            "bindings": data.get("bindings") or {},
            "branding": data.get("branding") or {},
            "brand_id": data.get("brand_id") or "",
            "terms": data.get("terms") or "",
            "cover_letter": data.get("cover_letter") or "",
            "executive_summary": data.get("executive_summary") or "",
            "default_expiry_days": data.get("default_expiry_days"),
            "carry_custom_modules": bool(data.get("carry_custom_modules")),
            "custom_module_advisory": rules.custom_module_advisory(),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
        }

    # -- brands ------------------------------------------------------------- #

    def save_brand(
        self,
        payload: Mapping[str, Any],
        *,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
        brand_id: str | None = None,
    ) -> dict[str, Any]:
        """Create or replace one brand kit."""

        name = str(payload.get("name") or "").strip()
        if not name:
            raise rules.ProposalRefusal(
                "A brand needs a name.",
                {"name": "A brand needs a name."},
            )
        key = rules.normalise_key(payload.get("key") or name)
        existing = self.store.get(brand_id) if brand_id else None
        if existing is not None and existing.get("collection") != vocab.BRANDS:
            existing = None

        incoming = payload.get("tokens") if isinstance(payload.get("tokens"), Mapping) else payload
        tokens: dict[str, Any] = {}
        for token in vocab.BRAND_TOKENS:
            if token not in incoming:
                continue
            value = incoming.get(token)
            if value in (None, ""):
                continue
            tokens[token] = (
                rules.normalise_colour(value, token)
                if token in ("accent", "accent_soft")
                else value
            )
        if vocab.FALLBACK_NAME_WHEN_LOGO_ABSENT in incoming:
            tokens[vocab.FALLBACK_NAME_WHEN_LOGO_ABSENT] = bool(
                incoming[vocab.FALLBACK_NAME_WHEN_LOGO_ABSENT]
            )

        data = {
            "name": name,
            "key": key,
            "tokens": tokens,
            "updated_at": rules.stamp(self._now()),
        }
        if existing is not None:
            record = self.store.update(existing["id"], data, actor=actor, source=source)
            action = "updated"
        else:
            data["created_at"] = rules.stamp(self._now())
            record = self.store.create(
                vocab.BRANDS, data, room_id=room_id, actor=actor, source=source
            )
            action = "created"
        return {"action": action, **self.brand_view(record)}

    def brands(self, room_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.list(vocab.BRANDS, room_id=room_id, limit=200)
        return [self.brand_view(row) for row in rows]

    def brand_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = record.get("data", {})
        tokens = data.get("tokens") or {}
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "name": data.get("name"),
            "key": data.get("key"),
            "tokens": {
                token: {
                    "label": vocab.BRAND_TOKEN_LABELS[token],
                    "value": tokens.get(token),
                }
                for token in vocab.BRAND_TOKENS
            },
            "show_company_name_when_no_logo": bool(
                tokens.get(vocab.FALLBACK_NAME_WHEN_LOGO_ABSENT, False)
            ),
            "updated_at": record.get("updated_at"),
        }

    def _resolve_brand(self, brand_id: Any) -> dict[str, Any] | None:
        if not brand_id:
            return None
        record = self.store.get(str(brand_id))
        if record is None or record.get("collection") != vocab.BRANDS:
            return None
        return record

    # -- the quote, read as data -------------------------------------------- #
    #
    # WF-086 provisions these rows. This workflow reads them and never creates one
    # through its own routes. See DERIVED_QUOTE_IS_READ_AS_DATA.

    def quotes(self, room_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.list(vocab.QUOTES, room_id=room_id, limit=200)
        return [self.quote_view(row) for row in rows]

    def quote_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        data = record.get("data", {})
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "title": data.get("title") or data.get("name") or "",
            "deal_name": data.get("deal_name") or data.get("deal") or "",
            "company_name": data.get("company_name") or data.get("company") or "",
            "currency_label": data.get("currency_label") or data.get("currency") or "",
            "template_id": data.get(vocab.TEMPLATE_ASSOC_ID_FIELD) or data.get("template_id"),
            "association_type_id": data.get(vocab.TEMPLATE_ASSOC_TYPE_FIELD)
            or vocab.ASSOCIATION_TYPE_ID,
            "issue_date": data.get("issue_date"),
            "expiration_date": data.get("expiration_date"),
            "po_number": data.get("po_number") or "",
            # A quote may carry a caller-supplied executive summary, which overrides the
            # template's own text under the evidence's precedence rule. Carried on the
            # view so the merge can read it without reaching into the raw record again.
            "executive_summary": data.get("executive_summary") or "",
            "discount": data.get("discount"),
            "tax": data.get("tax"),
            "branding": data.get("branding") if isinstance(data.get("branding"), Mapping) else {},
            "revision": record.get("revision"),
            "updated_at": record.get("updated_at"),
            "read_only": True,
            "collection": record.get("collection"),
        }

    def _quote_record(self, quote_id: str) -> dict[str, Any]:
        """The quote record itself, for a merge that binds to a dotted path.

        The scope a template binds against is the record's own JSON, not the flattened
        view :meth:`quote_view` returns. A template binds to paths such as
        ``company.name``, and the view carries only the fields this workflow chose to
        surface, so binding against the view would report every nested field of the
        record as unresolved.

        The raw payload is read here and never travels to a client: the view is the
        response shape, and this is the merge's input shape.
        """

        record = self.store.get(quote_id)
        if record is None or record.get("collection") != vocab.QUOTES:
            raise rules.ProposalNotFound(
                f"No quote {quote_id!r} in {vocab.QUOTES}. "
                f"{vocab.QUOTES} is provisioned by WF-086 and read by this workflow."
            )
        return record

    def quote_for_merge(self, quote_id: str) -> dict[str, Any]:
        """One quote in the shape :meth:`render` expects, ready to merge.

        The public way to get a merge input. It carries both the flattened view and the
        record's own JSON under ``_raw``, and :meth:`render` binds against ``_raw`` and
        strips it before it stores anything, so a caller never has to know that the two
        shapes differ.

        Exposed so the preview route can merge without writing, rather than reaching for
        the private reader underneath it.
        """

        record = self._quote_record(quote_id)
        return {**self.quote_view(record), "_raw": record.get("data", {})}

    def quote(self, quote_id: str) -> dict[str, Any]:
        record = self.store.get(quote_id)
        if record is None or record.get("collection") != vocab.QUOTES:
            raise rules.ProposalNotFound(
                f"No quote {quote_id!r} in {vocab.QUOTES}. "
                f"{vocab.QUOTES} is provisioned by WF-086 and read by this workflow."
            )
        return self.quote_view(record)

    def line_items(self, quote_id: str) -> list[dict[str, Any]]:
        """The line items one quote holds, as this workflow can read them.

        Matched on the quote reference every shape of these rows carries, rather than on
        a required column. WF-086 is not merged, so no column of its rows is guaranteed,
        and a workflow that required one would render an empty module on every quote
        until the other ticket landed.
        """

        rows = self.store.list(vocab.LINE_ITEMS, limit=1000)
        matched: list[dict[str, Any]] = []
        for row in rows:
            data = row.get("data", {})
            if self._belongs_to(data, quote_id):
                matched.append(data)
        matched.sort(key=lambda item: (item.get("position") or 0, str(item.get("name") or "")))
        return matched

    @staticmethod
    def _belongs_to(data: Mapping[str, Any], quote_id: str) -> bool:
        for key in ("quote_id", "quote", "parent_id", "quote_record_id"):
            if data.get(key) not in (None, "") and str(data[key]) == quote_id:
                return True
        return False

    # -- the merge ---------------------------------------------------------- #

    def render(
        self,
        template: Mapping[str, Any],
        quote: Mapping[str, Any],
        *,
        line_items: Sequence[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Merge a template and a quote into a document model. No store access.

        Split out from :meth:`instantiate` because the merge is pure and a reviewer should
        be able to read exactly what the merge produces without a database in the way.
        Every rendered module carries its own bindings, so a reader can see which record
        path answered for each field rather than being handed a string.
        """

        items = list(
            line_items if line_items is not None else self.line_items(str(quote.get("id")))
        )
        rendered_items, dropped = rules.cap_line_items(items)
        total_block = rules.totals(rendered_items, quote)

        brand_record = self._resolve_brand(template.get("brand_id"))
        branding = rules.resolve_branding(
            {**(template.get("branding") or {}), **dict(template)},
            (brand_record.get("data") or {}).get("tokens") if brand_record else None,
            quote.get("branding") if isinstance(quote.get("branding"), Mapping) else {},
        )
        # The brand id is the template's choice and travels beside the tokens it resolved
        # to, so a reader can see which brand kit produced the colours even after the
        # brand has since been edited.
        if brand_record is not None:
            branding["brand_id"] = brand_record["id"]

        # The scope is the quote record's own JSON, not the flattened view. A template
        # binds to a dotted path such as `company.name`, and the flattened view carries
        # only the fields this workflow chose to surface, so binding against it would
        # report every nested field of the record as unresolved.
        raw = quote.get("_raw") if isinstance(quote.get("_raw"), Mapping) else quote
        record_id = quote.get("id")
        # The scope exposes the record twice, under two roots, because both are legal
        # binding roots and a template author should not have to know which one to pick.
        # `quote.po_number` reads the record's own field and `company.name` reads the
        # nested object inside it. Both resolve against the same JSON.
        record_scope = {**dict(raw), "id": record_id}
        scopes = {
            "quote": {"quote": record_scope, **record_scope},
            "record": {"quote": record_scope, **record_scope},
        }
        # The merge reads `_raw` as its scope but never stores it, so the saved document
        # holds the rendered result and not a second copy of the quote record.
        quote = {key: value for key, value in quote.items() if key != "_raw"}

        hidden = set(template.get("hidden") or ())
        order = [name for name in (template.get("modules") or vocab.MODULES)]
        modules: list[dict[str, Any]] = []
        unresolved: list[dict[str, str]] = []

        for name in order:
            if name in hidden or name not in vocab.MODULES:
                continue
            module = self._render_module(
                name,
                template=template,
                quote=quote,
                scopes=scopes,
                items=rendered_items,
                totals=total_block,
                branding=branding,
                unresolved=unresolved,
            )
            modules.append(module)

        return {
            "document_model": vocab.DOCUMENT_MODEL_HTML_JSON,
            "template_id": template.get("id"),
            "template_key": template.get("key"),
            "quote_id": quote.get("id"),
            "branding": branding,
            vocab.REPORT_MODULES: modules,
            vocab.REPORT_TOTALS: total_block,
            vocab.REPORT_UNRESOLVED: unresolved,
            vocab.REPORT_TRUNCATED: {
                "dropped": dropped,
                "cap": vocab.RELATED_RECORD_CAP,
                "evidence": vocab.CAP_EVIDENCE["related_records"],
            },
            vocab.REPORT_PRECEDENCE: vocab.PRECEDENCE_QUOTE_OVER_TEMPLATE,
            "line_item_count": len(rendered_items),
            "placeholder_bounds": PLACEHOLDER_BOUNDS,
            "custom_module_advisory": rules.custom_module_advisory(),
        }

    def _render_module(
        self,
        name: str,
        *,
        template: Mapping[str, Any],
        quote: Mapping[str, Any],
        scopes: Mapping[str, Mapping[str, Any]],
        items: Sequence[Mapping[str, Any]],
        totals: Mapping[str, Any],
        branding: Mapping[str, Any],
        unresolved: list[dict[str, str]],
    ) -> dict[str, Any]:
        """One module of the document, with its resolved bindings beside it."""

        bindings = (template.get("bindings") or {}).get(name) or {}
        resolved = rules.resolve(bindings, scopes)
        for field, entry in resolved.items():
            if entry["status"] == vocab.UNRESOLVED:
                unresolved.append({"module": name, "field": field, "path": entry["path"]})

        module: dict[str, Any] = {
            "module": name,
            "label": vocab.MODULE_LABELS[name],
            "fields": resolved,
        }

        if name == vocab.MODULE_HEADER:
            module.update(self._render_header(template, quote, resolved, branding))
        elif name == vocab.MODULE_PARTIES:
            module["parties"] = [
                {"role": role, "label": vocab.PARTY_LABELS[role], **resolved.get(role, {})}
                for role in vocab.PARTY_ROLES
            ]
        elif name == vocab.MODULE_LINE_ITEMS:
            module["line_items"] = list(items)
            module["count"] = len(items)
        elif name == vocab.MODULE_TOTALS:
            module["totals"] = totals["ordered"]
        elif name == vocab.MODULE_COVER_LETTER:
            module["body"] = template.get("cover_letter") or ""
            module["source"] = vocab.GENERATION_SOURCE_TEMPLATE
        elif name == vocab.MODULE_EXECUTIVE_SUMMARY:
            # The evidence says the summary "can be generated by HubSpot's AI using data
            # from line items, deal activities, meeting transcripts, notes, and emails".
            # This repository generates no prose, so a caller-supplied string on the quote
            # wins over the template's own text and the module says which one it rendered.
            module["body"] = (
                quote.get("executive_summary") or template.get("executive_summary") or ""
            )
            module["source"] = (
                vocab.GENERATION_SOURCE_CALLER
                if quote.get("executive_summary")
                else vocab.GENERATION_SOURCE_TEMPLATE
            )
            module["inputs"] = list(vocab.GENERATION_INPUTS)
            module["generated_by_this_product"] = False
        elif name == vocab.MODULE_TERMS:
            module["body"] = template.get("terms") or ""
        elif name == vocab.MODULE_ACCEPTANCE:
            module["requires_signature"] = True
            module["state"] = vocab.STATE_DRAFT

        return module

    def _render_header(
        self,
        template: Mapping[str, Any],
        quote: Mapping[str, Any],
        resolved: Mapping[str, Any],
        branding: Mapping[str, Any],
    ) -> dict[str, Any]:
        """The header module, with its two date fields and where each came from.

        A date the quote does not state is derived from the issue date and reported as
        derived, per DERIVED_DEFAULT_EXPIRY_IS_DERIVED_AND_REPORTED. The header field
        binding wins over the derived value, because the evidence's precedence rule makes
        a property set on the quote override the template's.
        """

        issued_raw = quote.get("issue_date")
        issued = rules.coerce_instant(issued_raw, "issue_date") or self._now()
        expiry_bound = resolved.get("expiration_date")
        expiry_raw = quote.get("expiration_date")
        if expiry_bound and expiry_bound.get("value"):
            expiry_raw = expiry_bound["value"]
        expiry = rules.coerce_instant(expiry_raw, "expiration_date")
        derived = expiry is None
        if derived:
            days = int(template.get("default_expiry_days") or 30)
            expiry = rules.default_expiry(issued, days)

        return {
            "quote_reference": quote.get("quote_reference") or quote.get("id"),
            "issue_date": rules.stamp(issued),
            "expiration_date": rules.stamp(expiry),
            "expiration_derived": derived,
            "expiration_source": "derived_default" if derived else "quote_property",
            "currency_label": quote.get("currency_label")
            or quote.get("currency")
            or template.get("currency_label")
            or "USD",
            "po_number": quote.get("po_number") or "",
            "logo_url": branding.get("logo_url"),
            vocab.REPORT_LOGO_SOURCE: branding.get(vocab.REPORT_LOGO_SOURCE),
            "logo_fallback_applied": branding.get("logo_fallback_applied", False),
            "brand_id": branding.get("brand_id"),
        }

    def instantiate(
        self,
        template_id: str,
        quote_id: str,
        *,
        overrides: Mapping[str, Any] | None = None,
        room_id: str | None = None,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Merge, store the document, and return it.

        The stored document is a snapshot: it holds the branding it was rendered with, so
        a later brand change cannot alter a published proposal. That is what makes the
        non-retroactive rule true of this store rather than only true of the vendor's.
        """

        template_record = self._require_template(template_id)
        template = self._template_view(template_record)
        quote = self.quote_for_merge(quote_id)

        merged = self.render(template, quote)
        issued = rules.coerce_instant(quote.get("issue_date"), "issue_date") or self._now()
        data = {
            **merged,
            "state": vocab.STATE_INSTANTIATED,
            "issued_at": rules.stamp(issued),
            "created_at": rules.stamp(self._now()),
            "actor": actor or "system",
            "overrides": dict(overrides or {}),
            vocab.TEMPLATE_ASSOC_TYPE_FIELD: quote.get(vocab.TEMPLATE_ASSOC_TYPE_FIELD)
            or vocab.ASSOCIATION_TYPE_ID,
            "rerenderable": True,
        }
        record = self.store.create(
            vocab.DOCUMENTS,
            data,
            room_id=room_id or quote.get("room_id"),
            actor=actor,
            source=source,
        )
        return self.document_view(record)

    def documents(self, room_id: str | None = None) -> list[dict[str, Any]]:
        rows = self.store.list(vocab.DOCUMENTS, room_id=room_id, limit=200)
        return [self.document_view(row) for row in rows]

    def document(self, document_id: str) -> dict[str, Any]:
        record = self.store.get(document_id)
        if record is None or record.get("collection") != vocab.DOCUMENTS:
            raise rules.ProposalNotFound(f"No rendered document {document_id!r}.")
        return self.document_view(record)

    def document_view(self, record: Mapping[str, Any]) -> dict[str, Any]:
        """One document with the non-retroactive rule computed rather than asserted."""

        data = record.get("data", {})
        return {
            "id": record.get("id"),
            "room_id": record.get("room_id"),
            "revision": record.get("revision"),
            "state": data.get("state", vocab.STATE_INSTANTIATED),
            "template_id": data.get("template_id"),
            "template_key": data.get("template_key"),
            "quote_id": data.get("quote_id"),
            "document_model": data.get("document_model"),
            "association_type_id": data.get(vocab.TEMPLATE_ASSOC_TYPE_FIELD),
            "branding": data.get("branding") or {},
            "modules": data.get(vocab.REPORT_MODULES) or [],
            vocab.REPORT_TOTALS: data.get(vocab.REPORT_TOTALS) or {},
            vocab.REPORT_UNRESOLVED: data.get(vocab.REPORT_UNRESOLVED) or [],
            vocab.REPORT_TRUNCATED: data.get(vocab.REPORT_TRUNCATED) or {},
            vocab.REPORT_PRECEDENCE: data.get(vocab.REPORT_PRECEDENCE),
            "line_item_count": data.get("line_item_count", 0),
            # The merge records the content-placeholder bound on every document. It is
            # carried through the view rather than recomputed, so the figure a reader sees
            # is the one that was stored when the document was rendered.
            "placeholder_bounds": data.get("placeholder_bounds") or PLACEHOLDER_BOUNDS,
            "issued_at": data.get("issued_at"),
            "published_at": data.get("published_at"),
            "created_at": data.get("created_at"),
            "retroactivity": rules.rerenderable(data),
            "no_retroactive_application_quote": vocab.NO_RETROACTIVE_APPLICATION_QUOTE,
            "custom_module_advisory": data.get("custom_module_advisory")
            or rules.custom_module_advisory(),
            "overrides": data.get("overrides") or {},
        }

    def transition(
        self,
        document_id: str,
        action: str,
        *,
        actor: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Move a document one step and return it.

        A re-render of a published document is refused rather than performed, and the
        refusal names the remediation. Publishing an already published document is a
        no-op that still writes an audit row, because the caller asked for an action and
        the log should record that it was asked for.
        """

        record = self.store.get(document_id)
        if record is None or record.get("collection") != vocab.DOCUMENTS:
            raise rules.ProposalNotFound(f"No rendered document {document_id!r}.")
        data = record.get("data", {})
        current = data.get("state", vocab.STATE_INSTANTIATED)

        target = str(action or "").strip().lower()
        if target in ("rerender", "re_render", "render", "rebuild"):
            rules.require_rerenderable(data)
            return self.instantiate(
                str(data.get("template_id")),
                str(data.get("quote_id")),
                room_id=record.get("room_id"),
                actor=actor,
                source=source,
            )

        state = rules.next_state(current, target)
        patch: dict[str, Any] = {
            "state": state,
            "rerenderable": state not in vocab.FROZEN_STATES,
        }
        if state == vocab.STATE_PUBLISHED:
            patch["published_at"] = rules.stamp(self._now())
        updated = self.store.update(document_id, patch, actor=actor, source=source)
        return self.document_view(updated)

    # -- the board ---------------------------------------------------------- #

    def summary(self, room_id: str | None = None) -> dict[str, Any]:
        """The headline numbers, read back from the store.

        Counts are read rather than tracked, so a number on the board cannot describe a
        state the store does not hold. The unresolved and truncated counts sit beside the
        document count rather than inside it: a proposal that rendered with two unresolved
        fields is still a proposal, and folding those two into the document count would
        report fewer documents than exist.
        """

        documents = self.documents(room_id)
        unresolved = sum(len(doc[vocab.REPORT_UNRESOLVED]) for doc in documents)
        truncated = sum(
            int((doc[vocab.REPORT_TRUNCATED] or {}).get("dropped", 0)) for doc in documents
        )
        by_state: dict[str, int] = {state: 0 for state in vocab.DOCUMENT_STATES}
        for doc in documents:
            by_state[doc["state"]] = by_state.get(doc["state"], 0) + 1
        return {
            "templates": len(self.templates(room_id)),
            "brands": len(self.brands(room_id)),
            "documents": len(documents),
            "by_state": by_state,
            "quotes_read": len(self.quotes(room_id)),
            "line_items_read": len(self.store.list(vocab.LINE_ITEMS, room_id=room_id, limit=1000)),
            "unresolved_bindings": unresolved,
            "truncated_line_items": truncated,
            "document_model": vocab.DOCUMENT_MODEL_HTML_JSON,
            "custom_modules": rules.custom_module_advisory(),
            "collections": {
                "templates": vocab.TEMPLATES,
                "brands": vocab.BRANDS,
                "documents": vocab.DOCUMENTS,
                "quotes_read_only": vocab.QUOTES,
                "line_items_read_only": vocab.LINE_ITEMS,
            },
        }

    def decisions(self) -> dict[str, Any]:
        return {"count": inferences.count(), "decisions": inferences.describe()}
