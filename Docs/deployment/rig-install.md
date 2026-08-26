# Installing the labgate Platform on the Rig Computer

Target: the Windows setup computer that drives the 2PP rig (Python 3.12 in
`.venv312`, ACS `SPiiPlusPython` wheel installed from the SPiiPlus ADK).

## 1. Install

```bat
cd C:\path\to\LaserPrintingSoftware
.venv312\Scripts\python -m pip install -e ".[dev]"
:: verify SPiiPlusPython is present in this venv (vendor wheel, NOT on PyPI)
.venv312\Scripts\python -c "import SPiiPlusPython; print('ACS wheel OK')"
```

## 2. Configure

1. Copy `config/tokens.example.yaml` → `config/tokens.yaml` (gitignored);
   set real tokens/identities. Roles: `operator` proposes/executes,
   `approver` approves (must be a different person than the proposer).
2. In `config/default.yaml`, set:
   ```yaml
   labgate:
     mode: "sim"            # keep sim for the first server run!
     storage_dir: "labgate_data"
     tokens_file: "config/tokens.yaml"
   ```

## 3. First run (simulation mode, on the rig computer)

```bat
.venv312\Scripts\python -m pytest tests\ -q     :: full suite, no hardware
.venv312\Scripts\labgate-serve                  :: http://127.0.0.1:8523
curl http://127.0.0.1:8523/health
.venv312\Scripts\python examples\experimentalist_demo.py
```

## 4. Hardware verification (BEFORE flipping to rig mode)

Run with the rig powered, sample area clear, and a qualified person present:

```bat
.venv312\Scripts\python scripts\verify_hardware.py   :: read-only checks
.venv312\Scripts\python scripts\smoke_test.py        :: laser toggle + 0.05 mm jog
```

Then verify the two flagged items from code review (both are marked
NEEDS ON-RIG VERIFICATION in the source):

- **Stage halt**: in a Python shell, connect `StageController`, start a slow
  move, call `.halt()` — confirm motion stops and no exception is logged.
  If the ADK lacks `KillM`/`HaltM`, `StageTcp.halt_all` raises and logs —
  report which functions the installed `SPiiPlusPython` exposes.
- **Motion-profile capture**: run a single `MotionProfiling` condition and
  confirm `raw_profile.csv` shows a complete ramp (rises to the target
  velocity and settles — not truncated).

## 5. Calibrate, then go live

1. Run `MotionProfiling` at 1, 3, and 5 mm/s; add the measured
   `accel_time_s` / `accel_distance_mm` per velocity to
   `config/default.yaml` → `synchronization.calibration`.
2. Set `labgate.mode: "rig"` and restart `labgate-serve`.
3. First rig plan: a short, low-power `write_line` at a safe position,
   approved by a second person, with a hand on the e-stop.

## 6. Notes

- The platform binds to `127.0.0.1:8523` by default (`LABGATE_HOST` /
  `LABGATE_PORT` env vars). Do NOT expose it beyond the lab network until
  the network/TLS decision (requirements Q-P1) is made.
- Camera and white-light run as SIMULATED devices in rig mode until the
  real adapters land (requirements Q-H1/Q-H2).
- Every run writes telemetry + artifacts under `labgate_data/runs/<plan>`
  and audit entries to `labgate_data/audit.jsonl`.
