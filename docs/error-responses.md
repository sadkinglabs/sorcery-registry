# Configuring the error responses

The contract in [`docs/api.md`](api.md#when-something-goes-wrong) is served by
the zone, not by the repository: R2 stores objects and returns Cloudflare's own
pages when it cannot. These are the rules that implement it. Everything here is
dashboard work on the `kairosarchive.net` zone; nothing in this repository
changes. The audit (`scripts/audit_api2.py`, cases S1-S6) reports each case as a
note while it is unconfigured and asserts the full contract once a status
answers with JSON, so configuring a rule turns it into a guard.

What the zone serves today, measured on 15 September 2026:

| Case | Status | Body | CORS |
|---|---|---|---|
| Missing object | 404 | 27,150 bytes of HTML | present |
| No `User-Agent` | 403 | 17 bytes of text (`error code: 1020`) | absent |
| `DELETE` (or any write) | **401** | 16,794 bytes of HTML | absent |
| Path outside a release root | 404 | 27,150 bytes of HTML | present |
| Bare domain `/` | 404 | 27,150 bytes of HTML | present |
| `OPTIONS` preflight | 204 | correct already | present |

The 401 is the one to fix first: a client that sends a write gets
"unauthorized", which invites it to go looking for credentials that do not
exist, when the honest answer is that the archive is read-only.

## 1. The front door

**Rules → Redirects → Single Redirect.** When `http.host eq
"api.kairosarchive.net" and http.request.uri.path eq "/"`, redirect (302,
preserve query string off) to `https://api.kairosarchive.net/versions.json`.

This is the same product as the `/v3/` alias rule, so it is known to work on
this zone. A person pasting the domain into a browser lands on the discovery
document instead of a 404.

## 2. The JSON error pages

**Rules → Custom Errors.** One rule per status, matching `http.host eq
"api.kairosarchive.net"`, serving a custom response with content type
`application/json`. Check availability on the current plan first; if custom
error responses are not offered, the same bodies can be served by a Worker
bound to the hostname, which is a bigger change for the same result.

Bodies, ready to paste. Keep them under a kilobyte; a 404 that costs 27 KB is
a bad deal for a crawler and for the bill.

```json
{"error":"not_found","status":404,"message":"No object at this path.","docs":"https://github.com/sadkinglabs/sorcery-registry/blob/main/docs/api.md","discovery":"https://api.kairosarchive.net/versions.json"}
```

```json
{"error":"no_user_agent","status":403,"message":"Send a User-Agent naming your project and a contact, for example: my-deck-tool/1.2 (me@example.com).","terms":"https://github.com/sadkinglabs/sorcery-registry/blob/main/docs/usage.md"}
```

```json
{"error":"method_not_allowed","status":405,"message":"The archive is read-only. Use GET, HEAD or OPTIONS.","docs":"https://github.com/sadkinglabs/sorcery-registry/blob/main/docs/api.md"}
```

```json
{"error":"rate_limited","status":429,"message":"Too many requests from this address. Fetch registry.json or an index/ file once instead of crawling objects.","terms":"https://github.com/sadkinglabs/sorcery-registry/blob/main/docs/usage.md"}
```

```json
{"error":"unavailable","status":503,"message":"Temporarily unavailable. Retry with backoff, or read the same release from the GitHub mirror.","mirror":"https://raw.githubusercontent.com/sadkinglabs/sorcery-registry"}
```

## 3. The headers each one needs

- `Access-Control-Allow-Origin: *` on every error, so a browser can read the
  status instead of seeing an opaque network failure. The 403 and the 401 lack
  it today.
- `Allow: GET, HEAD, OPTIONS` on the 405.
- `Retry-After` in seconds on the 429, matching the rate rule's block duration.
- `Cache-Control: public, max-age=60` on the 404 so a crawler's repeated misses
  are absorbed at the edge, and `no-store` on the rest.

## 4. Turning writes into 405

The 401 comes from R2 answering an unauthenticated S3 write. **Rules → WAF →
Custom rules**: when `http.host eq "api.kairosarchive.net" and
http.request.method ne "GET" and http.request.method ne "HEAD" and
http.request.method ne "OPTIONS"`, block with a custom response of 405 and the
body above. Order it before the User-Agent rule so a write with no User-Agent
still reads as a method problem.

## 5. Proving it

Push any branch named `audit/**`, or wait for the weekly run, and read cases
S1 to S6. Each one names the status, the content type, the body size and
whether the response is readable cross-origin. They stop being notes and start
being assertions the moment the bodies are JSON.
