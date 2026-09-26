"""The condensed WF-015 design summary handed to Jev.

The full doc (`docs/design/WF-015-identity-and-domain-access.md`) is too long for
the bridge: it routes very long free-text inputs to `coding` and answers with
prose instead of typed results. This keeps every area the design gate asks about
(user flow, data flow, APIs, automations, tests) and drops the prose.
"""

SUMMARY = """
WF-015 Verify buyer identity and restrict by email domain.

USER FLOW (seller): pick a room -> open Access settings -> choose one of three
assurance tiers: open / identify (collect Name and/or Email, no login) /
verify_email -> optionally enable Domain Security and type approved domains
comma-separated without '@' -> optionally set the same policy on a template so
it is inherited by rooms created from it -> save.
USER FLOW (buyer): open /view/{room} -> form renders exactly the fields the tier
demands (account-login alternative offered when email is collected) -> submit ->
for verify_email a session is created pending and a verification message is
queued -> buyer opens the link -> session becomes verified -> the room is granted
and the identity is recorded in analytics. A domain that is not on the list is
refused with 403 and the attempt is recorded but attributed to nobody.

DATA FLOW: access_policy record (open JSON data: mode, collect_name,
collect_email, domain_security, allowed_domains[], inherit, template_id) ->
effective policy resolved room -> template -> built-in default open, with the
resolving level reported. access_session record holds status
identified|pending_verification|verified|refused, method
identified|email_link|account_login, name, email, email_domain, issued_at,
verified_at, viewed_at, expires_at, attempts, refusal_reason, likely_bot.
THE SESSION RECORD ID IS THE OPAQUE TOKEN the buyer carries, so a session is
revocable by soft delete, expirable, and every state change is audited.
verification_outbox record holds to/subject/body/link/delivered_via/status.
activity record emitted exactly once, on the first grant after verification,
carrying the identity - that is the analytics surface the research describes.

APIS, each specified with its request and response body:
GET /api/rooms/{id}/access returns {policy, level ("room"|"template"|"default"),
source (the record that answered), fields, and errors populated only when a
stored policy no longer validates - a policy that fails validation resolves to
open with errors, because a seller who tightened a rule into an impossible state
must still see the room and be told what is wrong, and the alternative turns a
configuration mistake into an outage.
PUT /api/rooms/{id}/access and PUT /api/templates/{id}/access take the whole
policy in one call (mode, collect_name, collect_email, domain_security,
allowed_domains, inherit, template_id) and return {policy, record_id, revision};
a repeated PUT updates the existing record and bumps its revision instead of
accumulating rows; inherit and template_id ride in the same payload so the
room-to-template relationship is one form submission.
DELETE /api/rooms/{id}/access and DELETE /api/templates/{id}/access return
{cleared: true, record_id} or {cleared: false, reason: "no policy to clear"};
soft delete, so recoverable and audited.
GET /api/rooms/{id}/access/requirements returns {room_id, mode, fields,
domain_security (a BOOLEAN, never the list), requires_verification,
allows_account_login, level} - the buyer learns a domain rule exists, never
which domains pass it.
GET /api/rooms/{id}/access/sessions returns {room_id, count, excluded_bots,
by_status, verified, sessions[]} with query parameters include_bots and
include_refused both defaulting to excluding; bot exclusion is the researched
requirement made real, because a flag that does not change what the seller sees
is a note in a log file.
GET /api/rooms/{id}/access/outbox returns {room_id, count, messages[]} newest
first, limit bounded at 50.
GET /api/rooms/{id}/access/verify?token= returns 200 {status: "verified", token,
session_id, identity, already_verified} on first and repeat presentation, 403
{error: domain_not_allowed | session_expired | too_many_attempts |
unknown_token}, or 200 {error, session_id} for an already-refused session.
GET /api/rooms/{id}/access/session?token= takes an OPTIONAL token - omitting it
asks what the room needs, which is how the buyer page decides whether to render
a form at all - and returns one of: granted with mode open; required with owed
[name, email] and requires_verification; granted with identity, verified and
viewed_at; pending with owed ["verification"] and expires_at; refused or expired
with refusal_reason. `owed` is a LIST rather than a single enum so a tier that
grows a step does not need a new status value.
POST /api/rooms/{id}/access/sessions takes {name, email, method} where method
defaults to identified and is account_login when the buyer used an existing
account, and returns one of four bodies: granted (open, token null), granted
(identify, with token and identity), pending_verification (with delivered_via,
message_id, the outbox link echoed ONLY while the transport is the default
outbox, and expires_at), or 403 {error: domain_not_allowed, detail,
session_id}.
The policy is also readable and writable through the generic
/api/records/access_policy endpoints because it is just a record.
Status codes: 200 for every read, the verify and check status endpoints, and the
seller views; 201 for a created session; 400 policy_invalid with an errors map
of field to reason so the UI can put the message next to the input that caused
it; 403 for domain_not_allowed, likely_bot, unknown_token, missing_token,
session_expired, too_many_attempts; 404 not_found.
BUYER PAGE CALL ORDER, which matters because the content must not be fetched
before the gate answers - the page is a separate route, so "hidden" means "never
requested": GET .../access/session?token= first; granted then fetch the room's
documents and render; pending shows "check your email" with the outbox link when
delivered_via is outbox; required fetches .../access/requirements and renders
the form; refused or expired shows the reason and renders nothing. Then POST
.../access/sessions on submit, and GET .../access/verify?token= for the email
link. The token is held in sessionStorage under dsr.access.<room_id> and sent as
a query parameter, not a cookie: the project has no session middleware to hook,
and a query parameter is inspectable in the audit log, which this project
already treats as privileged.

AUTOMATIONS (16 rows, each with trigger, condition, effect, and whether it is
sourced or inferred; no scheduler and no background worker, so nothing can fail
to run):
A1 POST sessions + tier open -> grant, create nothing (sourced).
A2 POST sessions + tier identify -> session identified, grant, send nothing
(sourced).
A3 POST sessions + tier verify_email -> session pending_verification, queue one
outbox message, room stays closed (sourced).
A4 POST sessions + domain_security on and domain not listed -> session refused
with refusal_reason domain_not_allowed, NO message queued, 403 (refusal is
sourced, refusing before sending is inferred).
A5 POST sessions + headers look like a scanner -> session refused with
likely_bot, 403 (caveat sourced, detection inferred).
A6 GET verify + pending, unexpired, allowlist still satisfied -> flip to
verified, stamp verified_at, extend expires_at to the grant window, mark the
outbox message sent (sourced).
A7 GET verify + already verified -> no-op, same identity returned (inferred).
A8 GET verify + 11th presentation of the token -> flip to refused,
too_many_attempts, 403 (inferred).
A9 GET verify + past expires_at -> flip to refused, session_expired, 403
(inferred).
A10 GET verify + allowlist tightened since the email was sent -> flip to refused,
domain_not_allowed, 403 (inferred; a link that was legitimate when sent must not
still work, and the door check reads the policy in force now, never a copy
stored in the session).
A11 GET session + granted and viewed_at empty -> stamp viewed_at, bump views,
emit exactly one activity row carrying the identity (sourced).
A12 GET session + granted and viewed_at set -> return the grant, emit nothing,
so a refresh cannot inflate analytics (inferred).
A13 GET /access + room has no policy -> resolve room then template then open,
reporting which level answered (sourced).
A14 PUT /templates/{id}/access -> inheriting rooms pick the new rule up on their
next read; rooms with their own policy do not (sourced that it applies to pages
created from the template; resolving rather than copying is inferred).
A15 DELETE /rooms/{id}/access -> the room's own policy is soft-deleted and the
room falls back to its template (inferred).
A16 every one of A1-A15 -> exactly one audit row per write, with before/after
and a diff (project rule).
The table also names, for each automation, the method in backend/dsr/access.py
that implements it (open_session, _queue_verification, verify,
_mark_outbox_sent, check, _record_view, resolve, set_policy, clear_policy) and
the exact number of audited writes it performs (A1 zero, A2 one insert, A3 two
inserts, A4 and A5 one insert each, A6 two updates, A7 zero, A8/A9/A10 one
update each, A11 one update plus one insert, A12 zero, A13 zero reads, A14 one
insert or update, A15 one delete), so a reviewer knows exactly how many rows a
given click should leave in the audit log.

VALIDATION RULES: domain_security requires mode verify_email; domain_security
requires at least one domain; verify_email requires collect_email; identify
requires name or email. Domains are normalised: trim, lowercase, strip a leading
'@', de-duplicate, preserve order.

BOT CAVEAT (sourced): scanners and previewers pollute analytics, and the vendor
has no IP blocklist. A heuristic over User-Agent and Sec-Purpose marks
likely_bot on the session, which is stored for review and excludes the attempt
from the identity list. It is recorded, not silently blocked. There is no IP
allowlist.

STORAGE: four collections, all records rows with open JSON data. No new table, no
migration, no typed column, so a team can add its own field to a policy without
coordinating. Every write goes through the audited wrapper, so a policy change,
a session state change, and a refusal are each audited with a diff. Unknown keys
in a policy payload are preserved untouched.

DESIGN INFERENCES (not in the research, stated as such): the server-side session
record, refusing the domain before sending mail, the pluggable outbox delivery
seam as the default transport, the bot detection mechanism, and resolving rather
than copying a template policy. The research is explicit that the vendor has no
public REST endpoint for security settings, so this is the project's own surface.

UI: seller-side AccessSettings page (tier radio group, identification
checkboxes, Domain Security disabled with an explanation unless verify_email is
on, comma-separated domain input showing the parsed list, inherit toggle naming
the template, the session list, the outbox) and a buyer-side Gate page at
/view/{room} that never fetches room content until the API grants it. 44px
targets, visible focus, SVG icons, no emoji, prefers-reduced-motion, 4.5:1.

TESTS: two files, backend/tests/test_access.py for the pure helpers with no
database, and backend/tests/test_access_api.py for behaviour through HTTP using
a temp DSR_DB_PATH and make_room/set_policy/submit helpers. The API is what
other teams build against, so the contract under test is the endpoints.
test_access.py, 15 cases with input and expected output: email_domain extracts
the lowercase domain and returns None for "alex@localhost", "alex", "a@b@c";
is_valid_email accepts "a.b+tag@x.co" and rejects "a b@c.co"; normalize_domains
strips a leading @ and folds case, dedupes while preserving order, rejects
"not a domain" with a PolicyError naming the field, and accepts a list;
looks_like_bot flags Outlook/Googlebot/curl user agents and Sec-Purpose prefetch
and leaves a normal browser alone; validate_policy rejects domain_security
without verify_email, an empty allowlist, verify_email without collect_email,
and identify with neither field; validate_policy preserves an unknown key.
test_access_api.py, 28 cases, each mapped to the automations above: open creates
no session and no message (A1); identify records identity and sends nothing
(A2); verify queues exactly one outbox message and leaves the room closed (A3);
THE ROUND TRIP PARSES THE TOKEN OUT OF THE OUTBOX RECORD'S LINK RATHER THAN
BUILDING ONE, so the delivery seam cannot drift from the flow (A3,A6,A11);
verification is idempotent, returning already_verified with a second audit row
and no state change (A7); two submissions from one address create two sessions
so repeat visits are not merged; a disallowed domain gives 403
domain_not_allowed with a refused session and zero messages (A4); the refused
attempt is recorded for the seller but contributes no verified identity; a
second listed domain is accepted; a scanner gives 403 likely_bot with
likely_bot stored and excluded_bots == 1 unless include_bots (A5); tightening
the allowlist after submit makes the pending link fail (A10); an expires_at in
the past gives 403 session_expired and flips the status to refused (A9); ten
verifies succeed and the eleventh gives 403 too_many_attempts with
attempts == 11 (A8); the first grant emits exactly one activity row with the
identity and identity_verified true, and three further gate checks leave it at
one (A11,A12); an identify grant emits one row with identity_verified false;
inheritance from a template, a room policy overriding it, a template change
reaching an inheriting room with no write to the room, DELETE falling back to
the template and being audited, and a room with no policy anywhere resolving to
open at level default (A13,A14,A15); the requirements body never contains
allowed_domains or any domain string, and reflects fields,
requires_verification and allows_account_login per tier; account_login is
recorded as its own method; one full round trip's audit log holds an insert for
the policy, session, outbox and activity plus an update per session state change
with diff populated (A16); an unknown policy field survives PUT then GET; the
policy is reachable through GET /api/records/access_policy and filtered by
?where=subject_kind=room; an unknown room is 404; a valid token presented
against a different room is 403.
""".strip()
