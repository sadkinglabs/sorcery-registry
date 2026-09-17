"""Pacing and retry for the two audit scripts.

The API host answers 429 to more than 30 requests in 10 seconds from
one address (docs/usage.md): an audit that fires its forty-odd checks as
fast as the runner can loses the race with its own rate rule and then
tests the 429 page instead of the documentation. So every request is
paced to stay under the rule, and a 429 that still arrives on a request
that identified itself is retried after the edge's Retry-After (or the
documented ten seconds), a bounded number of times, and noted. A 429 on
a request that sent no User-Agent is the answer being tested and is
returned as is."""
import collections
import time
import urllib.error

WINDOW_S = 10.0
PER_WINDOW = 25            # under the 30 the edge allows, with room for redirects
RETRIES = 3
MAX_WAIT_S = 20.0

_recent = collections.deque()
NOTES = []


def pace(now=time.monotonic, sleep=time.sleep):
    """Block until one more request fits in the window."""
    t = now()
    while _recent and t - _recent[0] > WINDOW_S:
        _recent.popleft()
    if len(_recent) >= PER_WINDOW:
        sleep(WINDOW_S - (t - _recent[0]) + 0.05)
    _recent.append(now())


def retry_after(headers, default=WINDOW_S):
    try:
        return min(float(headers.get("Retry-After", default)), MAX_WAIT_S)
    except (TypeError, ValueError):
        return default


def opened(opener, request, identified, sleep=time.sleep):
    """`opener.open(request)`, paced, with 429 retried when the request
    carries a User-Agent. Raises HTTPError for any other error status
    (the callers read the body from it), or a 429 that outlasted the
    retries."""
    attempt = 0
    while True:
        pace(sleep=sleep)
        try:
            return opener.open(request, timeout=60)
        except urllib.error.HTTPError as e:
            if e.code != 429 or not identified or attempt >= RETRIES:
                raise
            wait = retry_after(e.headers)
            NOTES.append(f"429 on {request.full_url}; waiting {wait:.0f}s (attempt {attempt + 1}/{RETRIES})")
            e.read()
            sleep(wait)
            attempt += 1
