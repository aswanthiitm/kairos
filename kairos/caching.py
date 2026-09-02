"""
Narrative cache + prompt caching.

Two distinct savings, kept distinct because they behave differently:

  SEMANTIC CACHE   the same evidence packet, for the same persona, at the same
                   narrator mode, must produce the same narrative. Key on a hash
                   of the packet, so a re-open of an unchanged case costs zero
                   tokens and returns in microseconds.

  PROMPT CACHE     the system prompt and the instruction block are identical on
                   every call and are far larger than the packet. Marked with
                   Anthropic cache_control so the provider bills them at the
                   cached rate after the first call in a window.

Both are reported honestly in telemetry: a cached run says so, and its cost is
recorded as zero rather than quietly inheriting the last real figure.
"""
import hashlib, json, os, time
from typing import Any, Dict, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(ROOT, "runtime")
CACHE_FILE = os.path.join(STORE, "narrative_cache.json")
TTL_SECONDS = 30 * 60

# Discount applied to input tokens the provider serves from its prompt cache.
PROMPT_CACHE_READ_RATE = 0.10


def packet_key(packet: Dict[str, Any], persona_key: str, mode: str) -> str:
    """A stable fingerprint of everything that can change the narrative.

    Deliberately excludes run_id and timings — two runs over identical evidence
    are the same question and deserve the same answer.
    """
    payload = json.dumps(packet, sort_keys=True, default=str)
    h = hashlib.sha256()
    h.update(payload.encode("utf8"))
    h.update(("|%s|%s" % (persona_key, mode)).encode("utf8"))
    return h.hexdigest()[:24]


def _load() -> Dict[str, Any]:
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d: Dict[str, Any]) -> None:
    os.makedirs(STORE, exist_ok=True)
    tmp = CACHE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f)
    os.replace(tmp, CACHE_FILE)


def get(key: str) -> Optional[Dict[str, Any]]:
    d = _load()
    hit = d.get(key)
    if not hit:
        return None
    if time.time() - hit.get("stored_at", 0) > TTL_SECONDS:
        d.pop(key, None)
        _save(d)
        return None
    return hit["value"]


def put(key: str, value: Dict[str, Any]) -> None:
    d = _load()
    d[key] = {"stored_at": time.time(), "value": value}
    # keep the store small and predictable
    if len(d) > 200:
        for k in sorted(d, key=lambda k: d[k]["stored_at"])[:len(d) - 200]:
            d.pop(k, None)
    _save(d)


def clear() -> Dict[str, Any]:
    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)
        return {"cleared": True}
    return {"cleared": False}


def stats() -> Dict[str, Any]:
    d = _load()
    now = time.time()
    live = [v for v in d.values() if now - v.get("stored_at", 0) <= TTL_SECONDS]
    return {"entries": len(d), "live_entries": len(live),
            "ttl_minutes": TTL_SECONDS // 60,
            "path": CACHE_FILE if os.path.exists(CACHE_FILE) else None}


def cacheable_system_block(system_text: str) -> Any:
    """The system prompt as a cache-marked block for the Messages API.

    It is byte-identical on every call and dwarfs the packet, so marking it
    ephemeral is where the real token saving is.
    """
    return [{"type": "text", "text": system_text,
             "cache_control": {"type": "ephemeral"}}]


def effective_input_tokens(in_tok: int, cache_read_tok: int) -> Tuple[int, float]:
    """Billed-equivalent input tokens once the provider's cached read is priced
    at a fraction of the full rate. Returned alongside the raw count so
    telemetry can show both."""
    billed = (in_tok - cache_read_tok) + cache_read_tok * PROMPT_CACHE_READ_RATE
    saved = in_tok - billed
    return int(round(billed)), float(saved)
