# VPS research runner

How to run `algo-sweep` / `algo-backtest` / `algo-walkforward` on a VPS in
`tmux`, so multi-hour queues no longer need your PC powered on. The VPS is a
**pure compute clone**: repo + locked uv environment + a byte-identical copy of the
dataset. Data goes up by rsync, results come back by rsync, and no secrets ever
land on the box.

> **TL;DR:** one-time — install uv 0.11.28, clone the repo with a read-only
> deploy key, `uv sync --locked`, `make check`. Per run —
> `make sync-up` (data PC→VPS), start the job in `tmux`, `make sync-down`
> (results VPS→PC). Sweeps run **sequentially** — never two processes writing
> the same `index.csv`.

The design decisions behind this setup (Phase 2 Session 4b, 2026-07-04):

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Environment | uv 0.11.28 + `.python-version` + `uv.lock` | Matches local Beta 0 exactly: vanilla CPython 3.12.13, NautilusTrader 1.230.0 and TA-Lib 0.7.0. The TA-Lib wheel bundles the C library. |
| 2 | Data source | rsync `bot_data/processed/` from PC | Reproducibility: the VPS backtests on the exact dataset you validated in Session 2. `algo-fetch` on the VPS would drift (extra bars, silent Binance revisions) and make PC↔VPS results incomparable. |
| 3 | Parallelism | Sequential in tmux | Zero code change. `results/experiments/index.csv` is append-only and **not** multi-process safe. A `--index_csv`-per-run flag is a deferred follow-up. |
| 4 | Results transport | rsync `results/` VPS→PC | Analysis (notebook 03) and the brain-Claude audit live on the PC. |
| 5 | Security | Read-only deploy key, no secrets | Backtests need no exchange keys. Results return over rsync, not git push, so a read-only key is sufficient and minimises blast radius. |

Reference host for this project: OVH VPS-2, 6 vCores / 12 GB RAM / 100 GB disk,
Ubuntu 22.04 LTS, region `os-waw2` (Warsaw).

---

## Prerequisites

- SSH access to the VPS as a non-root sudo user (OVH gives you one on
  provisioning; `ssh ubuntu@<vps-ip>`).
- On the PC (WSL): the repo at `~/quant_projects/algo_bot` with the six
  processed CSVs present in `bot_data/processed/` (Session 2 dataset).
- The repo's GitHub remote URL (`git remote -v` on the PC).

---

## Part A — one-time VPS setup

Run everything below **on the VPS** unless a step says otherwise.

### A1. Base packages

```bash
sudo apt-get update
sudo apt-get install -y git rsync tmux curl
```

TA-Lib's C library is **not** installed through apt. The pinned TA-Lib 0.7.0
wheel contains it (Decision 1).

### A2. Install the pinned uv

```bash
curl -LsSf https://astral.sh/uv/0.11.28/install.sh | sh
exec bash   # reload PATH if requested by the installer
uv --version
# uv 0.11.28
```

Do not install an unpinned latest uv on the research runner. Conda/Miniforge is
a superseded historical setup, not a second default.

### A3. Read-only deploy key + clone

Generate a **fresh** key on the VPS (the private key never leaves the box —
Decision 5). Add only the public half to GitHub.

```bash
ssh-keygen -t ed25519 -f ~/.ssh/algo_bot_deploy -C "vps-algo-bot-ro" -N ""
cat ~/.ssh/algo_bot_deploy.pub   # copy this line
```

On GitHub: repo → **Settings → Deploy keys → Add deploy key** → paste the
public key → **leave "Allow write access" unchecked** (read-only).

Tell SSH to use this key for GitHub, via an alias so it never clashes with any
other key:

```bash
cat >> ~/.ssh/config <<'EOF'

Host github.com-algobot
    HostName github.com
    User git
    IdentityFile ~/.ssh/algo_bot_deploy
    IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
```

Clone using the alias (replace `<owner>` with your GitHub org/user — check
`git remote -v` on the PC):

```bash
mkdir -p ~/quant_projects && cd ~/quant_projects
git clone git@github.com-algobot:<owner>/algo_bot.git
cd algo_bot
```

`git pull` works; `git push` is refused by the read-only key — intended.
Results go back over rsync, not git.

### A4. Create the locked environment

```bash
cd ~/quant_projects/algo_bot
make env            # uv sync --locked; CPython 3.12.13 + project + dev deps
uv run python --version
uv run python -c 'from importlib.metadata import version; print(version("nautilus-trader"), version("TA-Lib"))'
```

No activation step is needed. All Python tools and project CLIs run through
`uv run`; Makefile quality targets use `uv run --locked` internally.

### A5. Smoke test — `make check`

```bash
make check          # ruff + format-check + mypy + pytest
```

Green here proves the env is faithful to WSL. Integration data tests
(`-m integration`) skip until data is synced (Part B) — that is expected.

---

## Part B — sync the dataset (PC → VPS)

Done **on the PC (WSL)**.

### B1. SSH alias + key to the VPS

Let WSL reach the VPS without a password prompt on every rsync:

```bash
# once, if you don't already have a personal key:
# ssh-keygen -t ed25519
ssh-copy-id ubuntu@<vps-ip>

cat >> ~/.ssh/config <<'EOF'

Host algo-vps
    HostName <vps-ip>
    User ubuntu
EOF
```

### B2. Push the data

```bash
cd ~/quant_projects/algo_bot
make sync-up VPS_HOST=algo-vps
```

This rsyncs `bot_data/processed/` (the six OHLCV CSVs plus any funding CSVs)
to the same path on the VPS. Verify on the VPS:

```bash
ls -lh ~/quant_projects/algo_bot/bot_data/processed/
uv run pytest tests/test_data_integrity.py -m integration -q   # now runs for real
```

---

## Part C — run a sweep in tmux (on the VPS)

`tmux` keeps the job alive after you disconnect.

```bash
cd ~/quant_projects/algo_bot
git pull                       # get the latest strategy/config before a run
make sync                      # exact uv.lock; no env activation

tmux new -s sweep              # new session

uv run algo-sweep --strategy bghtrend_pullback \
  --symbols BTC/USDT ETH/USDT \
  --timeframes 1h \
  --start 2019-09-08 --end 2026-07-04 \
  --space_file config/bghtrend_b1.yaml \
  --microstructure full
```

Detach with **Ctrl-b then d** — the sweep keeps running. Reconnect any time:

```bash
tmux attach -t sweep           # list sessions: tmux ls
```

Queue several spaces back-to-back in one session (sequential — see
anti-patterns):

```bash
for space in b1 b2 b3 b4; do
  uv run algo-sweep --strategy bghtrend_pullback --symbols BTC/USDT ETH/USDT \
    --timeframes 1h --start 2019-09-08 --end 2026-07-04 \
    --space_file config/bghtrend_${space}.yaml --microstructure full
done
```

---

## Part D — bring results back (VPS → PC)

Done **on the PC (WSL)** once a run finishes:

```bash
cd ~/quant_projects/algo_bot
make sync-down VPS_HOST=algo-vps
```

This rsyncs `results/` (including `results/experiments/index.csv` and
`results/backtests/<run_id>/`) back to the PC, where notebook 03 and the
brain-Claude audit read it.

---

## Smoke test (end-to-end)

To prove the pipeline before trusting a long queue, run one short sweep and
confirm the row lands on the PC:

```bash
# VPS, inside tmux:
uv run algo-sweep --strategy bghtrend_pullback --symbols BTC/USDT \
  --timeframes 4h --start 2024-01-01 --end 2024-06-30 \
  --space_file config/bghtrend_b4.yaml --microstructure full
#   (4h + 6 months + __n small = a few minutes)

# PC:
make sync-down VPS_HOST=algo-vps
tail -n 2 results/experiments/index.csv
```

A fresh row with populated `sharpe_post` / `n_trades_post` means env, data,
compute and transport all work. This is a plumbing check, **not** research —
strategy backtests stay on hold pending the pivot decision.

---

## Anti-patterns — do not do this

- **Two processes writing the same `index.csv`.** The sweep appends to
  `results/experiments/index.csv` with no lock. Parallel sweeps interleave and
  corrupt rows. Run sequentially in one tmux session (Decision 3). Real
  parallelism waits for a `--index_csv`-per-run flag.
- **`uv run algo-fetch` on the VPS.** It diverges the dataset from the PC and breaks
  result comparability (Decision 2). Data always flows PC→VPS via `sync-up`.
  To refresh: re-fetch on the PC, re-run the integrity test, `sync-up` again.
- **Copying `.env` or API keys to the VPS.** Backtests need none (Decision 5).
  Keep the box a pure research runner.
- **`rsync --delete`.** `vps-sync.sh` never deletes on either side by design —
  a wrong `VPS_HOST` should never wipe local `results/`. Clean up manually.
- **A write-access deploy key.** Read-only is enough; results return over
  rsync, not `git push`.

---

*Phase 2 Session 4b deliverable. Scripts: `scripts/vps-sync.sh`, Makefile
targets `sync-up` / `sync-down`. See `docs/ROADMAP.md` → Phase 2 → Session 4b
and `docs/guides/running-sweep.md` for what the VPS actually runs.*
