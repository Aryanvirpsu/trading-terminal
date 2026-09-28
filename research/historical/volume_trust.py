"""H5 blocker #6 — enforce volume trust at runtime instead of merely documenting it.

FABHAUS_AUDIT_REPORT.md sec 6 found fabhaus's volume is a consistent 20-46% of Yahoo's consolidated
volume and marked it `RELATIVE_ONLY`: usable for a same-symbol, same-session RELATIVE comparison (e.g.
today's bar vs. its own trailing average), never as an ABSOLUTE dollar-liquidity figure. Until this
module, that verdict was documentation only -- nothing stopped a historical run from silently handing
RELATIVE_ONLY volume to a Champion component that assumes consolidated absolute volume.

`strategies.score_liquid_momentum` is the ONLY one of the three funnel strategies with an absolute-dollar-
volume floor (`dollar_vol < 5e6`); `score_sector_rs` and `score_mean_reversion` read only closes. Rather
than corrupting the bars dict handed to `score_liquid_momentum` (which would ALSO destroy its legitimate,
still-valid `rel_vol` computation from the very same input, and risks producing a *wrong-but-plausible*
number instead of a clean refusal), the fail-closed point is choosing NOT to run that one strategy at all
against a source whose volume can't support its assumption -- exactly the `strategies=` override
`strategies.scan()` already accepts as an ordinary external parameter. No Champion code changes: the
function is not run, not fed different data.
"""
from __future__ import annotations

import enum
from typing import Optional, Sequence, Tuple


class VolumeTrust(str, enum.Enum):
    ABSOLUTE = "ABSOLUTE"              # consolidated/near-consolidated tape volume (e.g. Yahoo for US equities)
    RELATIVE_ONLY = "RELATIVE_ONLY"    # partial/differently-defined coverage; same-symbol ratios only
    UNKNOWN = "UNKNOWN"                # trust not declared -- treated exactly like RELATIVE_ONLY (fail closed)


# Strategies in lab/paper/strategies.py that read an ABSOLUTE dollar-volume figure, not just a same-symbol
# ratio. Kept as an explicit, named allowlist rather than inferring it by inspection, so a reviewer can
# check this list against strategies.py directly and a new strategy defaults to "not trusted" until someone
# deliberately reviews and adds it here.
ABSOLUTE_VOLUME_STRATEGIES: Tuple[str, ...] = ("liquid_momentum",)

# Every strategy scan() currently knows how to run (kept in sync with cfg.enabled_strategies()'s universe;
# see test_h5_volume_trust.py for a guard that fails loudly if strategies.py grows a new one this module
# doesn't know about).
ALL_STRATEGIES: Tuple[str, ...] = ("liquid_momentum", "sector_relative_strength", "mean_reversion")


def trusts_absolute_volume(trust: "VolumeTrust | str") -> bool:
    return VolumeTrust(trust) == VolumeTrust.ABSOLUTE


def enabled_strategies_for(volume_trust: "VolumeTrust | str",
                           requested: Optional[Sequence[str]] = None) -> Tuple[str, ...]:
    """The `strategies=` tuple to pass to `strategies.scan()`/`ctx.scan()` for a source with this volume
    trust level. `requested` is the caller's own preference (defaults to every strategy); any strategy in
    it that needs absolute volume is dropped, never silently kept, when the source isn't `ABSOLUTE`. An
    explicit, named exclusion (not a silent one): callers can inspect `ABSOLUTE_VOLUME_STRATEGIES` to see
    exactly why a strategy is missing."""
    base = tuple(requested) if requested is not None else ALL_STRATEGIES
    if trusts_absolute_volume(volume_trust):
        return base
    return tuple(s for s in base if s not in ABSOLUTE_VOLUME_STRATEGIES)
