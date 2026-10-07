"""agent/pricing.py is a byte copy of automation/modeler/agentic/pricing.py at main 7e25722 (161 lines, astra only),
and the frozen tariff prices exactly as plan/WP2_DESIGN.md section 8 says: per million tokens input $10, cached $1,
cache write $12.5, output $50; above 272,000 input tokens x2 input/cache and x1.5 output; integer micro-USD with every
division rounding up; reserve_micro = all input at the cache-write rate + the full output cap."""
import hashlib
import shutil
import subprocess

import pytest

from glasses3d.agent import pricing
from glasses3d.agent.pricing import (LONG_CONTEXT_INPUT_TOKENS, MICRO, MODEL, PricingError, Tariff, usd_text,
                                     usd_to_micro, validate_usage)
from glasses3d.tests.agent.support import REPO_ROOT, require_data, usage

PRICING_SHA256 = "82c120e2bfd73c8f21fdb86bce8d9a485ca1802f41bd7ce28ee6b4b07032e3b4"   # main 7e25722 blob
SOURCE_COMMIT = "7e2572271862d626f150c83b3441999ec03bf46d"
SOURCE_PATH = "automation/modeler/agentic/pricing.py"
T = Tariff.frozen()


def checked_out_bytes() -> bytes:
    """The working-tree file; a core.autocrlf checkout may turn every LF into CRLF, nothing else."""
    raw = open(pricing.__file__, "rb").read()
    if b"\r\n" in raw:
        assert b"\n" not in raw.replace(b"\r\n", b""), "mixed line endings: not a checkout conversion"
        raw = raw.replace(b"\r\n", b"\n")
    return raw


def git_show(ref: str) -> bytes | None:
    git = shutil.which("git")
    if git is None:
        return None
    done = subprocess.run([git, "show", f"{ref}:{SOURCE_PATH}"], cwd=REPO_ROOT, capture_output=True, timeout=60)
    return done.stdout if done.returncode == 0 else None


def test_pricing_is_the_pinned_byte_copy():
    raw = checked_out_bytes()
    assert hashlib.sha256(raw).hexdigest() == PRICING_SHA256
    assert raw.count(b"\n") == 161
    assert b"luna" not in raw and MODEL == "gpt-6-astra"


def test_pricing_is_byte_identical_to_git_show_of_the_source():
    source = git_show(SOURCE_COMMIT)
    if source is None:
        require_data(None, f"git object {SOURCE_COMMIT[:7]}:{SOURCE_PATH}")
    assert hashlib.sha256(source).hexdigest() == PRICING_SHA256
    assert checked_out_bytes() == source
    on_main = git_show("main")
    if on_main is not None:     # while main still carries the modeler, its tariff must be the one glasses3d copied
        assert hashlib.sha256(on_main).hexdigest() == PRICING_SHA256


def test_rates_per_million_tokens():
    # 200,000 input tokens is below the 272k step: one fifth of the per-million rate
    assert T.settle_micro(usage(tokens=200_000, output=0)) == 10 * MICRO // 5                      # input $10/M
    assert T.settle_micro(usage(tokens=200_000, output=0, cached=200_000)) == 1 * MICRO // 5       # cached $1/M
    assert T.settle_micro(usage(tokens=200_000, output=0, written=200_000)) == 12_500_000 // 5     # write $12.5/M
    assert T.settle_micro(usage(tokens=0, output=1_000_000)) == 50 * MICRO                         # output $50/M
    mixed = usage(tokens=200_000, output=100_000, cached=100_000, written=50_000)
    assert T.settle_micro(mixed) == 500_000 + 100_000 + 625_000 + 5_000_000
    assert (T.input_per_million, T.cached_per_million, T.cache_write_per_million, T.output_per_million) == (
        10 * MICRO, 1 * MICRO, 12_500_000, 50 * MICRO)


def test_long_context_multipliers_apply_above_272k_input():
    assert LONG_CONTEXT_INPUT_TOKENS == 272_000
    at, above = 272_000, 272_001
    assert T.settle_micro(usage(tokens=at, output=1000)) == at * 10 + 1000 * 50
    assert T.settle_micro(usage(tokens=above, output=0)) == above * 10 * 2                        # input x2
    assert T.settle_micro(usage(tokens=above, output=0, cached=above)) == above * 1 * 2           # cached x2
    assert T.settle_micro(usage(tokens=above, output=0, written=above)) == above * 25             # write x2
    assert T.settle_micro(usage(tokens=above, output=1000)) == above * 20 + 1000 * 75             # output x1.5
    assert T.multipliers(at)["long_context"] is False and T.multipliers(above)["long_context"] is True


def test_every_division_rounds_up():
    assert T.settle_micro(usage(tokens=1, output=0, written=1)) == 13          # 12.5 -> 13
    assert T.settle_micro(usage(tokens=3, output=0, written=3)) == 38          # 37.5 -> 38
    assert T.settle_micro(usage(tokens=4, output=0, written=1, cached=1)) == 20 + 1 + 13
    assert T.reserve_micro(1, 1) == 13 + 50
    assert usd_to_micro("0.0000001") == 1 and usd_to_micro("1.0000011") == 1_000_002 and usd_to_micro(2) == 2 * MICRO
    assert Tariff.frozen(region="eu").reserve_micro(1, 1) == 70               # (13 + 50) x 1.10 = 69.3 -> 70
    assert usd_text(14_261_424) == "14.261424"


def test_reserve_is_all_input_at_cache_write_plus_full_output_cap():
    assert T.reserve_micro(1000, 100) == 1000 * 12.5 + 100 * 50 == 17_500
    assert T.reserve_micro(24_787, 8_192) == 719_438                           # Prada row 1 (WP2_DESIGN section 14)
    assert T.reserve_micro(272_001, 25_000) == 8_675_025                       # long context: x2 write, x1.5 output
    for counts in [dict(tokens=24_787, written=24_784, output=8_192), dict(tokens=225_128, cached=220_158,
                   written=4_967, output=8_192), dict(tokens=300_000, output=8_192)]:
        assert T.settle_micro(usage(**counts)) <= T.reserve_micro(counts["tokens"], 8_192)


@pytest.mark.parametrize("input_tokens,output_limit", [
    (1.5, 100), (True, 100), (-1, 100), (100, 0), (100, None), (100, 128_001), (922_001, 100), ("1000", 100)])
def test_reserve_refuses_what_it_cannot_bound(input_tokens, output_limit):
    with pytest.raises(PricingError):
        T.reserve_micro(input_tokens, output_limit)


def test_tariff_refuses_unpriced_modes():
    for kwargs in (dict(service_tier="flex"), dict(service_tier="priority"), dict(service_tier="auto"),
                   dict(fast_mode=True), dict(region="apac")):
        with pytest.raises(PricingError):
            Tariff.frozen(**kwargs)
    with pytest.raises(PricingError):
        Tariff.from_dict({**T.to_dict(), "price_version": "something-else"})
    assert Tariff.from_dict(T.to_dict()) == T


@pytest.mark.parametrize("block", [
    None, [], {"input_tokens": -1, "output_tokens": 0}, {"input_tokens": True, "output_tokens": 0},
    {"input_tokens": 10, "output_tokens": 1.0}, usage(tokens=10, cached=6, written=5),
    usage(tokens=10, output=2, reasoning=3), dict(usage(), total_tokens=5),
    {"input_tokens": 10, "output_tokens": 1, "input_tokens_details": [1]},
    {"input_tokens": 10, "output_tokens": 1, "output_tokens_details": "x"}])
def test_malformed_usage_never_settles(block):
    with pytest.raises(PricingError):
        validate_usage(block)
    with pytest.raises(PricingError):
        T.settle_micro(block)
