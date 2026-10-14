# Provenance — diagnosis of 2026-10-13

* Source: cloud runtime (OCI Ubuntu host) host-verified backup `20261012T202501Z`, taken 2026-10-12T20:25:02Z, published as GitHub Actions
  artifact `avdi-ubuntu-backup-17` (run 38338137990, repo Aryanvirpsu/trading-terminal), 1.77 MB.
* Mirrored read-only with `scripts/sync_cloud_data.py` (sha256 of every file + SQLite `integrity_check` re-verified locally) into
  `~/.avdi_cloud_mirror/20261012T202501Z/` — outside the local ledger's data directory.
* Files read (pristine, SQLite `mode=ro&immutable=1`):
  * `robinhood_500_baseline.db` sha256 `991d2dab62426c858f50b2039b0075fde63c611761b35da094aca2cc617c0b62`
  * `shadow__shadow_candidates.db` sha256 `c35aa01007c41a59db770fb39225385b6cf2245faef287df304c1c6e06bb3a9a`
* Engine of the sample: `decision_engine/gates-v1.1` (config `cfg-a0eede144e`, runtime `b236d54a469475b7c6ac7c1980b1e4b30e10de0d`).
* Reproduce: sync the same backup, then `python diagnose.py --backup <path to that folder>`. A newer backup will include more sessions and
  give different (later) numbers — by design.
* Nothing was written to the local ledger (Case 2) or to the cloud.
