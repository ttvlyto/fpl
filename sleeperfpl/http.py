"""GET JSON with retries and optional throttling. Raises FetchError, never exits."""

import http.client
import json
import time
import urllib.error
import urllib.request

USER_AGENT = "sleeper-fpl/1.0"
_last_call = 0.0


class FetchError(Exception):
    pass


def get_json(url, *, retries=3, timeout=120, min_interval=0.0):
    """Return parsed JSON from `url`.

    Retries connection errors, 429 and 5xx with 2**attempt second backoff.
    `min_interval` enforces a gap since the previous throttled call.
    """
    global _last_call
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_err = None
    for attempt in range(retries + 1):
        if min_interval:
            wait = _last_call + min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _last_call = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code} from {url}"
            if e.code != 429 and e.code < 500:
                break
        except urllib.error.URLError as e:
            last_err = f"Could not reach {url}: {e.reason}"
        except json.JSONDecodeError as e:
            last_err = f"Bad JSON from {url}: {e}"
            break
        except (OSError, http.client.HTTPException, ValueError) as e:
            # Errors while reading the body: resets, truncated reads, bad encoding.
            last_err = f"Error reading {url}: {e}"
        if attempt < retries:
            time.sleep(2 ** attempt)
    raise FetchError(last_err)
