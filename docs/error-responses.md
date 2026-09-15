# What the zone does when a request fails

The registry is static objects behind a CDN, so failures are answered by the
zone rather than by anything in this repository. This is what it is configured
to do, what its plan prevents, and what an upgrade would change. The contract a
client should rely on is in [`docs/api.md`](api.md#when-something-goes-wrong);
this file is the operational record behind it.

Measured on 15 September 2026 by `scripts/audit_api2.py`, cases S1 to S6.

| Case | Status | Body | CORS |
|---|---|---|---|
| Missing object | 404 | 27,150 bytes of HTML | present |
| Path outside a release root | 404 | 27,150 bytes of HTML | present |
| No `User-Agent` | 403 | 17 bytes of plain text (`error code: 1020`) | absent |
| A write (any method but `GET`, `HEAD`, `OPTIONS`) | 403 | 4,552 bytes of HTML | absent |
| Bare domain `/` | 302 to `versions.json` | none | n/a |
| `OPTIONS` preflight | 204 | none, correct headers | present |

## What is configured

**Front door redirect.** Rules → Redirects → Single Redirect, named *API front
door to discovery*. When `http.host eq "api.kairosarchive.net" and
http.request.uri.path eq "/"`, 302 to `https://api.kairosarchive.net/versions.json`.
A person pasting the domain into a browser reaches the discovery document
instead of a 404.

This is a second rule alongside the `/v3/` alias. The release workflow rewrites
that alias by rule id on every release, so the two must stay separate: editing
the alias rule to do both jobs would be undone by the next release.

**Read-only archive.** Security rules → Custom rules, named *read-only
archive*, ordered first. When `http.host eq "api.kairosarchive.net" and
http.request.method ne "GET" and http.request.method ne "HEAD" and
http.request.method ne "OPTIONS"`, block.

Ordered first on purpose: a write that also lacks a `User-Agent` should be told
about the method, which is the thing it must change, rather than being told to
identify itself and then refused anyway.

This rule cannot lock out the pipeline. The release and image workflows upload
through `https://<account>.r2.cloudflarestorage.com` with credentials, never
through the public hostname, so a rule on that hostname never sees them.

**Anonymous clients.** The pre-existing custom rule blocking requests with no
`User-Agent`, unchanged.

## What the plan prevents

Three things were specified and could not be built:

- **Custom Errors** is not available: the dashboard offers only an upgrade
  prompt. This is what would let a 404, a 429 or a 5xx answer with JSON.
- **A custom response on a block** is not available either. A custom rule's
  action panel offers the action, the execution order and the status, and
  nothing else, so a block returns the CDN's own page and its own status.
- **A response code on a block** follows from the same limitation, which is why
  a write is refused with 403 rather than 405.

The result is that every status is correct and no body is machine-readable. For
a client that checks `response.ok` before parsing, which it should do anyway,
the difference is cosmetic.

## What an upgrade would change

With Custom Errors, each status below gets a small JSON body and the whole set
takes about ten minutes. Bodies worth using, kept under a kilobyte:

```json
{"error":"not_found","status":404,"message":"No object at this path.","docs":"https://github.com/sadkinglabs/sorcery-registry/blob/main/docs/api.md","discovery":"https://api.kairosarchive.net/versions.json"}
```

```json
{"error":"no_user_agent","status":403,"message":"Send a User-Agent naming your project and a contact, for example: my-deck-tool/1.2 (me@example.com).","terms":"https://github.com/sadkinglabs/sorcery-registry/blob/main/docs/usage.md"}
```

```json
{"error":"read_only","status":403,"message":"The archive is read-only. Use GET, HEAD or OPTIONS.","docs":"https://github.com/sadkinglabs/sorcery-registry/blob/main/docs/api.md"}
```

```json
{"error":"rate_limited","status":429,"message":"Too many requests from this address. Fetch registry.json or an index/ file once instead of crawling objects.","terms":"https://github.com/sadkinglabs/sorcery-registry/blob/main/docs/usage.md"}
```

```json
{"error":"unavailable","status":503,"message":"Temporarily unavailable. Retry with backoff, or read the same release from the GitHub mirror.","mirror":"https://raw.githubusercontent.com/sadkinglabs/sorcery-registry"}
```

Each would also want `Access-Control-Allow-Origin: *` so a browser can read the
status, `Cache-Control: public, max-age=60` on the 404 so a crawler's repeated
misses are absorbed at the edge, and `Retry-After` on the 429.

A Worker in front of the bucket could do the same without an upgrade, and is
deliberately not used: it would run on every request, not just the failures,
and would add a hop and a failure mode to a service whose whole virtue is being
static bytes on a CDN. Three cosmetic bodies do not pay for that.

## Proving any of it

Run the audit (`workflow_dispatch` on `api-audit`, or push any `audit/**`
branch) and read cases S1 to S6. Each names the status, the content type, the
body size and whether the response is readable cross-origin. The error cases
report as notes while the bodies are the CDN's, and become assertions the
moment a status answers with JSON, so configuring custom errors later turns
them into guards with no code change.
