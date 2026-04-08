# Laser Printing Software - Technical Guide

## Table of Contents

1. [What This Software Does](#1-what-this-software-does)
2. [Hardware Setup](#2-hardware-setup)
3. [Previous Version: Issues and Limitations](#3-previous-version-issues-and-limitations)
4. [New Version: What Changed and Why](#4-new-version-what-changed-and-why)
5. [Architecture Overview](#5-architecture-overview)
6. [Module Reference](#6-module-reference)
7. [How to Use](#7-how-to-use)
8. [Configuration](#8-configuration)
9. [Open Questions and Future Work](#9-open-questions-and-future-work)

---

## 1. What This Software Does

This is a Python control system for a **two-photon polymerization (2PP) laser printing and ablation** setup. It coordinates two independent pieces of hardware:

- A **femtosecond pulsed laser** (Phoebe PH2, 515nm, ~200 kHz) that provides the light source
- A **3-axis precision stage** (Standa 8MTL120XY, ACS SPiiPlus controller) that positions the sample

The software generates motion paths (either from explicit coordinates or from STL 3D model files), then executes those paths while precisely timing the laser on/off to ensure uniform exposure. It also supports Design of Experiments (DOE) for systematic parameter optimization.

### Supported Operations

| Operation | Description |
|-----------|-------------|
| **Single-line printing** | Horizontal line at fixed Y, Z with configurable repetitions |
| **Parameter optimization (DOE)** | Full-factorial sweep of power, speed, divider, and repetitions |
| **Z-stack power sweep (2PP)** | Vertical stacks at varying power for polymerization threshold finding |
| **STL-based 3D printing** | Convert 3D model to layer-by-layer scan paths |
| **STL-based ablation** | Same as above but layers processed top-down |
| **Motion profiling** | Characterize stage acceleration/jerk for synchronization calibration |
| **3D path visualization** | Preview scan paths before printing |

---

## 2. Hardware Setup

### Stage (Motion Control)

- **Hardware**: Standa 8MTL120XY stage
- **Controller**: ACS Motion Control SPiiPlus
- **Connection**: Ethernet TCP at `10.0.0.100:701`
- **Library**: `SPiiPlusPython` (C bindings for Python)
- **Axes**: X (axis 0), Y (axis 1), Z (axis 2)
- **Range**: +/-25 mm per axis
- **Precision**: ~1 micron positioning
- **Motor type**: Axes 0 and 1 are brushless linear motors (require commutation at startup)

The SPiiPlus controller software can be installed from:
`https://acsmotioncontrol.com/products/spiiplus/`

Documentation (once installed) is at:
`C:\Program Files (x86)\ACS Motion Control\SPiiPlus Documentation Kit\`

### Laser

- **Hardware**: Phoebe PH2 femtosecond pulsed laser
- **Wavelength**: 515 nm
- **Base repetition rate**: ~200.8 kHz
- **Connection**: HTTP REST API at `http://192.168.244.10`
- **Control panel**: Browser-accessible at the same IP address
- **API base path**: `/phoebe/v0/Basic`

The laser manufacturer's installation software exists but requires physical connection to the laser to run. During installation, the manufacturer configured the laser to use its built-in web API, so all control goes through HTTP requests.

### Key Controls

| Control | Location | Description |
|---------|----------|-------------|
| Attenuator (0-100%) | Laser API | Controls beam power via mechanical attenuator |
| Pulse-picker divider (1+) | Laser API | Divides the base repetition rate (1 = full rate) |
| Output enable/disable | Laser API | Master switch for laser emission |
| Position (X, Y, Z) | Stage controller | Absolute position in mm |
| Velocity (per axis) | Stage controller | Speed in mm/s |
| Acceleration / Jerk | Stage controller | Motion profile tuning (firmware-dependent) |

---

## 3. Previous Version: Issues and Limitations

The previous code is preserved in `Docs/Archive Folder/`. Below is a detailed breakdown of every significant issue found during the review.

### 3.1 The Synchronization Problem (Critical)

**The core issue**: The laser must fire ONLY during the constant-velocity region of each motion segment. A stage move follows a trapezoidal velocity profile:

```
Speed
  ^
  |      ___________
  |     /           \
  |    /             \
  |   /               \
  |  /                 \
  +--+---+---+---+---+---> Time
     accel  constant   decel
            velocity
     
     Laser should ONLY be ON here ↑
```

**What the old code did** (`main.py:72-83`):

```python
def __call__(self, laser_and_lines):
    for line, laser in laser_and_lines:
        self.move_and_wait(line)   # ← blocks until motion complete
        if laser:
            laser_on()             # ← laser turns on AFTER motion!
        else:
            laser_off()
```

The laser was toggled **between** moves (while the stage was stationary), not **during** moves. This means:

- For single-line experiments at one velocity, it happened to work (the laser was on during the next move)
- But for STL printing with varying line lengths, the timing was completely wrong
- The laser would "flicker on and off during run and finally turn off" (reported bug)

**Partial fix attempt** (`async_motor.py`): Used `time.sleep()` with hardcoded timing values calibrated for 5 mm/s:

```python
wait_time = 0.357    # seconds to wait through acceleration
full_time = 2.4      # total motion time
length = 0.79        # distance during acceleration (mm)
```

This worked for one specific velocity and line length but was not generalizable.

### 3.2 Missing Function: `generate_lines_with_pitch()`

This function was **imported in 6 experiment files** but **did not exist anywhere**:

```python
# These files all import it but it doesn't exist:
# - parameter_optimization.py
# - parameter_optimization_rectangle.py
# - parameter_optimization_for_2pp.py (indirectly)
# - exp_AP_T_A_11.02.25.py
# - AnnaManebt.py
# - point by point printing

from spiiplus_motor_control.controlers.lines_laser_states_from_coords import generate_lines_with_pitch
```

We verified this by checking:
- The current source file (not there)
- The `.zip` archive (not there)
- The compiled `.pyc` bytecode (not there)

The function was apparently lost during a code refactor. We reconstructed it from its usage patterns across the experiment files.

### 3.3 Bug: Attenuator Validation (`laser_control.py:41`)

```python
# WRONG: Python chained comparison
if 0 < attenuator_percentage > 100:
    raise ValueError(...)

# This evaluates as: (0 < attenuator_percentage) AND (attenuator_percentage > 100)
# Only catches values > 100. Negative values pass through silently.

# CORRECT (used in AsyncLaserAPI on line 123):
if 0 > attenuator_percentage or attenuator_percentage > 100:
```

Interestingly, the async version of the same class had the correct validation.

### 3.4 Global Singleton at Import Time (`laser_control.py:99`)

```python
laser_api = LaserAPI()  # ← This runs at import time!
```

The `LaserAPI.__init__()` immediately sends HTTP requests to the laser (disable output, set defaults). This means:

- **Importing `laser_control` without the laser connected crashes the program**
- Cannot develop, test, or even run `import laser_control` without the physical hardware
- Other modules that import from this file (like `main.py`) also crash

### 3.5 Silent Error Swallowing (`main.py:72-83`)

```python
def __call__(self, laser_and_lines):
    try:
        for line, laser in laser_and_lines:
            self.move_and_wait(line)
            if laser: laser_on()
            else: laser_off()
    except Exception as e:
        print(e)                 # ← only prints, doesn't re-raise
        print("starting cleanup!")
        self.cleanup()           # ← cleanup runs but error is lost
```

If an error occurred during printing, the cleanup would run (good), but the error was swallowed — the calling code had no way to know something went wrong.

### 3.6 Missing Function: `move_motors()` 

Referenced in `exp_AP_T_A_11.02.25.py:45`:

```python
from spiiplus_motor_control.main import move_motors
```

This function existed in an older version (visible as commented-out code in `async_motor.py:59-89`) but was replaced by the `MotorControl` class without updating the experiment files.

### 3.7 Code Organization Issues

| Issue | Details |
|-------|---------|
| `async_motor.py` | ~95% commented-out experimental code (214 lines, almost all comments) |
| Hardcoded IPs | `10.0.0.100` and `192.168.244.10` appear as string literals throughout |
| No logging | All output via `print()` statements |
| No config file | Experiment parameters, IP addresses, ports all hardcoded |
| Inconsistent naming | `controlers/` (typo), mixed camelCase/snake_case |
| No cleanup guarantee | Some experiments lack `try/finally` blocks |
| Duplicate `insert_multiple_interjections()` | Copy-pasted across `parameter_optimization.py` and `parameter_optimization_for_2pp.py` |
| Scattered velocity data | `.json` and `.npy` files mixed in with source code |

### 3.8 STL Laser State Bug (Detailed)

In `line_laser_states_from_stl.py`, the `add_laser_states()` function decides laser on/off by checking if consecutive points share Y and Z:

```python
if list_of_lines[i][1] == list_of_lines[i+1][1] and \
   list_of_lines[i][2] == list_of_lines[i+1][2]:
    lines_and_laser.append((list_of_lines[i], True))   # same Y,Z → laser ON
else:
    lines_and_laser.append((list_of_lines[i], False))  # different Y or Z → OFF
```

This logic is correct for determining **which segments are print vs reposition**. However, the problem is upstream: even when the laser state is correctly assigned, the execution loop in `main.py` toggles the laser AFTER each move completes (see issue 3.1). The laser state labels are right, but the timing of the laser commands is wrong.

---

## 4. New Version: What Changed and Why

### 4.1 Summary of Changes

| Component | Old | New | Why |
|-----------|-----|-----|-----|
| **Project structure** | Flat `spiiplus_motor_control/` package | `src/laser_printing/` with clear subpackages | Separation of concerns, installable package |
| **Configuration** | Hardcoded values everywhere | `config/default.yaml` + `config.py` loader | Change hardware settings without editing code |
| **Laser control** | Global `LaserAPI()` singleton | `LaserController` class with lifecycle | Safe import, explicit connect/disconnect |
| **Stage control** | `MotorControl` class | `StageController` with context manager | Guaranteed cleanup, position reading, data collection API |
| **Synchronization** | Sequential (laser between moves) | `PrintSynchronizer` with velocity-timed strategy | Laser fires during constant-velocity window |
| **Path generation** | `generate_lines_with_pitch` missing | `lines_with_pitch()` reconstructed | Experiments can actually run |
| **STL pipeline** | Monolithic with laser state bug | Staged pipeline with documented stages | Clearer data flow, easier debugging |
| **Experiments** | Standalone scripts with duplicated code | `BaseExperiment` class with lifecycle | Progress reporting, logging, safe shutdown |
| **DOE** | `ExpDesign` with wrong index logic | `ExperimentDesign` with clean DataFrame API | Correct parameter lookup, simpler interface |
| **Logging** | `print()` only | Python `logging` (console + file) | Persistent experiment logs |
| **Error handling** | `except: print(e)` (swallowed) | Context managers + `finally` blocks | Errors propagate, hardware always cleaned up |
| **Validation** | `if 0 < x > 100` (bug) | `if not (0 <= x <= 100)` | Correct range check |

### 4.2 New: PrintSynchronizer (The Core Fix)

The synchronizer implements two strategies:

**Sequential** (simple, for testing):
```
Move to point → Wait → Laser on/off → Move to next point → Wait → ...
```

**Velocity-timed** (production, for actual printing):
```
Start non-blocking move → Sleep(accel_time) → Laser ON → Sleep(coast_time) → Laser OFF → Wait for motion end
```

The velocity-timed strategy uses a **calibration table** of (velocity → accel_time, accel_distance) mappings. These are measured once per velocity using the `MotionProfiling` experiment and stored in the config file. The synchronizer interpolates between calibration points for intermediate velocities.

```
                         calibration_table
                         ┌─────────────────────────────────┐
                         │ velocity │ accel_time │ accel_dist │
                         │ 1 mm/s   │ 0.15 s     │ 0.08 mm   │
MotionProfiling ───────► │ 3 mm/s   │ 0.25 s     │ 0.38 mm   │ ◄── config/default.yaml
  experiment             │ 5 mm/s   │ 0.357 s    │ 0.79 mm   │
                         │ 10 mm/s  │ 0.50 s     │ 2.50 mm   │
                         └──────────┴────────────┴───────────┘
                                        │
                                        ▼
                              PrintSynchronizer
                              uses calibration to time
                              laser on/off precisely
```

### 4.3 New: BaseExperiment Lifecycle

Every experiment now follows a guaranteed lifecycle:

```
┌─────────────────────────────────────────────────────────┐
│ 1. Connect hardware (laser + stage)                      │
│ 2. Define experimental conditions                        │
│ 3. For each condition:                                   │
│    ├─ Log parameters                                     │
│    ├─ Display progress (elapsed / remaining time)        │
│    ├─ Execute condition                                  │
│    └─ Record results                                     │
│ 4. Save results (CSV + JSON + description)               │
│ 5. Disconnect hardware (GUARANTEED, even on error)       │
└─────────────────────────────────────────────────────────┘
```

The progress reporting was specifically requested in the project presentation:
> "do printing with reporting each structure, printing parameters, total passed time and total remaining time"

### 4.4 New: Reconstructed `lines_with_pitch()`

The missing function was rebuilt from its call sites. The function generates parallel horizontal lines at regular Y spacing, grouped into independently executable blocks:

```python
blocks = lines_with_pitch(
    x_start=-20, x_end=-16,   # X endpoints of each line
    y_start=-22,               # Y coordinate of first line
    y_pitch=0.1,               # 100 micron spacing between lines
    z_focus=35.88,             # focal plane height
    count=10,                  # number of lines
    repetitions=[1,3,5,...],   # back-and-forth sweeps per line
)
# blocks[0] = initial positioning (laser off)
# blocks[1:] = one block per line (each independently executable)
```

### 4.5 New: Unified Parameter Sweep

The original codebase had two nearly-identical experiment scripts (`parameter_optimization.py` and `parameter_optimization_for_2pp.py`) with duplicated helper functions. These are now a single `ParameterSweep` class with two modes:

- `"line_doe"` mode: Full-factorial DOE with single lines (replaces `parameter_optimization.py`)
- `"z_stack"` mode: Z-stack power sweep for 2PP (replaces `parameter_optimization_for_2pp.py`)

---

## 5. Architecture Overview

### Directory Structure

```
LaserPrintingSoftware/
├── pyproject.toml                    # Package definition and dependencies
├── config/
│   └── default.yaml                  # Hardware and experiment configuration
├── src/
│   └── laser_printing/
│       ├── __init__.py
│       ├── config.py                 # YAML config loading and validation
│       ├── controllers/
│       │   ├── laser.py              # LaserController (HTTP REST API)
│       │   ├── stage.py              # StageController (SPiiPlus Ethernet TCP)
│       │   └── sync.py               # PrintSynchronizer (laser-stage coordination)
│       ├── path_generation/
│       │   ├── coordinates.py        # Paths from explicit coordinates
│       │   ├── stl.py                # Paths from STL mesh files
│       │   └── patterns.py           # Reusable patterns (indicator lines, etc.)
│       ├── experiment/
│       │   ├── base.py               # BaseExperiment (lifecycle, logging, progress)
│       │   ├── parameter_sweep.py    # DOE + Z-stack experiments
│       │   └── motion_profiling.py   # Acceleration/jerk characterization
│       ├── utils/
│       │   ├── mesh.py               # Scale, translate, discretize meshes
│       │   ├── doe.py                # Design of Experiments (pyDOE2 wrapper)
│       │   └── io.py                 # JSON/CSV save/load
│       └── visualization/
│           └── plotting.py           # 3D path plots, motion profile plots
├── tests/                            # Unit tests (to be expanded)
└── Docs/
    ├── Archive Folder/               # Previous version (preserved as-is)
    └── TECHNICAL_GUIDE.md            # This document
```

### Data Flow

```
                    ┌──────────────┐
                    │  STL File    │     OR     Explicit Coordinates
                    └──────┬───────┘            (x_start, x_end, y, z)
                           │                              │
                           ▼                              ▼
                   ┌───────────────┐           ┌──────────────────┐
                   │ stl.py        │           │ coordinates.py   │
                   │ generate_     │           │ lines_with_pitch │
                   │ from_stl()    │           │ z_stacks_with_   │
                   └───────┬───────┘           │ power()          │
                           │                   └────────┬─────────┘
                           ▼                            ▼
                   ┌─────────────────────────────────────────┐
                   │   Path: list of (point, laser_on) tuples │
                   └──────────────────┬──────────────────────┘
                                      │
                                      ▼
                              ┌───────────────┐
                              │ sync.py       │
                              │ PrintSynchro- │
                              │ nizer         │
                              └───┬───────┬───┘
                                  │       │
                         ┌────────┘       └────────┐
                         ▼                         ▼
                 ┌──────────────┐         ┌──────────────┐
                 │ stage.py     │         │ laser.py     │
                 │ StageControl │         │ LaserControl │
                 │ (move stage) │         │ (fire laser) │
                 └──────────────┘         └──────────────┘
                         │                         │
                         ▼                         ▼
                  Ethernet TCP              HTTP REST API
                  10.0.0.100:701            192.168.244.10
```

### Dependency Graph Between Modules

```
config.py ◄──── All modules read configuration from here
    │
    ▼
controllers/
    laser.py ◄───── No internal dependencies
    stage.py ◄───── No internal dependencies  
    sync.py  ◄───── Depends on: laser.py, stage.py
    
utils/
    mesh.py  ◄───── Uses: trimesh, numpy
    doe.py   ◄───── Uses: pyDOE2, pandas
    io.py    ◄───── Uses: json, numpy

path_generation/
    coordinates.py ◄── No internal dependencies
    stl.py         ◄── Depends on: utils/mesh.py
    patterns.py    ◄── Depends on: coordinates.py

experiment/
    base.py             ◄── Depends on: config.py, controllers/*
    parameter_sweep.py  ◄── Depends on: base.py, path_generation/coordinates.py, utils/doe.py
    motion_profiling.py ◄── Depends on: base.py

visualization/
    plotting.py ◄── Uses: matplotlib, numpy
```

---

## 6. Module Reference

### 6.1 `controllers/laser.py` - LaserController

Controls the Phoebe laser via HTTP REST API.

| Method | Description |
|--------|-------------|
| `LaserController(ip, api_base, command_delay_s)` | Create controller (does not connect) |
| `LaserController.from_config(config["laser"])` | Create from config dict |
| `connect()` | Verify laser is reachable, disable output |
| `disconnect()` | Disable output and mark as disconnected |
| `set_attenuator(percentage)` | Set beam power (0-100%), skips if already at target |
| `set_pp_divider(divider)` | Set pulse-picker divider (>= 1) |
| `enable_output()` | Enable laser emission |
| `disable_output()` | Disable laser emission |
| `get_status()` | Query full laser status (returns JSON dict) |
| Properties: `attenuator_pct`, `pp_divider`, `output_enabled` | Cached state values |

Supports context manager: `with LaserController.from_config(...) as laser:`

### 6.2 `controllers/stage.py` - StageController

Controls the ACS SPiiPlus 3-axis motion stage.

| Method | Description |
|--------|-------------|
| `StageController(ip, port, axes, commutation_axes, default_velocity)` | Create controller |
| `StageController.from_config(config["stage"])` | Create from config dict |
| `connect(home_position)` | Connect, enable and commutate axes |
| `disconnect()` | Return to home, close connection |
| `set_velocity(velocities)` | Set axis velocities (single value or list per axis) |
| `set_acceleration(axis, acceleration)` | Set acceleration (firmware-dependent) |
| `set_jerk(axis, jerk)` | Set jerk (firmware-dependent) |
| `move_to(point, wait=True)` | Move to [x,y,z], optionally wait for completion |
| `move_to_non_blocking(point)` | Start move without waiting (returns waitblock) |
| `wait_for_motion(timeout_ms)` | Block until all axes stop |
| `get_position()` | Read current [x,y,z] position |
| `get_velocity_feedback()` | Read current actual velocity |
| `declare_data_array(name, rows, cols)` | Declare controller-side array for data collection |
| `start_data_collection(array_name, n_samples, period, variables)` | Start recording |
| `read_data_array(array_name, rows, cols)` | Read collected data back |

### 6.3 `controllers/sync.py` - PrintSynchronizer

Coordinates laser firing with stage motion.

| Method | Description |
|--------|-------------|
| `PrintSynchronizer(laser, stage, strategy, velocity_tolerance, calibration)` | Create |
| `PrintSynchronizer.from_config(config, laser, stage)` | Create from full config |
| `execute_path(path)` | Execute complete path with coordinated laser control |
| `execute_print_line(start, end)` | Execute single line with strategy-appropriate timing |
| `add_calibration(point)` | Add/update a calibration point for a velocity |

Strategies:
- `"sequential"`: Move, wait, toggle laser. Simple but inaccurate.
- `"velocity_timed"`: Fire laser during constant-velocity window using calibrated timing.

### 6.4 `path_generation/coordinates.py`

| Function | Description |
|----------|-------------|
| `single_line(x_start, x_end, y, z, repetitions)` | Single horizontal line with back-and-forth sweeps |
| `lines_with_pitch(x_start, x_end, y_start, y_pitch, z_focus, count, repetitions, max_block_size)` | Parallel lines at regular Y spacing (reconstructed missing function) |
| `z_stacks_with_power(...)` | Z-stacks with power sweep for 2PP experiments |

### 6.5 `path_generation/stl.py`

| Function | Description |
|----------|-------------|
| `generate_from_stl(stl_file, unit, step_size, start_position, ...)` | Full STL-to-path pipeline |

Internal pipeline stages (not called directly):
1. `_mesh_to_point_cloud()` - Sample mesh surface
2. `_bin_by_z_y()` - Partition into Z-layers and Y-rows
3. `_extract_scan_lines()` - Find min/max X per row
4. `_snake_order()` - Alternating direction optimization
5. `_assign_laser_states()` - Label print vs reposition segments

### 6.6 `experiment/base.py` - BaseExperiment

Abstract base class. Subclasses implement:
- `_define_conditions() -> list[dict]` - What to sweep
- `_run_condition(index, condition) -> dict` - How to run one condition

The base class provides:
- Hardware connection/disconnection
- Progress reporting with elapsed/remaining time
- Experiment logging to file
- Results saving (CSV)
- Error recovery and safe shutdown

### 6.7 `experiment/parameter_sweep.py` - ParameterSweep

Two modes:
- `"line_doe"` - Full-factorial DOE (replaces `parameter_optimization.py`)
- `"z_stack"` - Z-stack power sweep (replaces `parameter_optimization_for_2pp.py`)

### 6.8 `experiment/motion_profiling.py` - MotionProfiling

Sweeps acceleration/jerk values and records velocity profiles. Outputs calibration data that feeds into the `PrintSynchronizer`.

---

## 7. How to Use

### 7.1 Installation

```bash
cd LaserPrintingSoftware
pip install -e .
```

Or if using the virtual environment:

```bash
.venv/bin/pip install -e .
```

### 7.2 Configuration

Copy and edit the config file for your setup:

```bash
cp config/default.yaml config/my_setup.yaml
# Edit IP addresses, velocities, calibration data, etc.
```

Key settings to verify:
- `laser.ip` - Your laser's IP address
- `stage.ip` and `stage.port` - Your stage controller's address
- `synchronization.calibration` - Measured acceleration profiles

### 7.3 Running a Parameter Optimization Experiment (DOE)

```python
import pandas as pd
from laser_printing.experiment.parameter_sweep import ParameterSweep

# Define the parameters to sweep
parameters = pd.DataFrame({
    "AP (%)": [10, 20, 30],        # Attenuator power levels
    "RRD": [10, 5, 1],             # Repetition rate divider levels
    "MS (mm/s)": [1, 3, 5],        # Motor speed levels
    "LR": [1, 3, 5],               # Line repetition levels
})

# Create and run the experiment
exp = ParameterSweep(
    experiment_id="ParOpt_01",
    output_dir="./results",
    z_focus=35.88,                  # Focal plane height (mm)
    mode="line_doe",
    x_start=-20, x_end=-16,        # Line X endpoints (mm)
    y_start=-22,                    # First line Y position (mm)
    y_pitch=0.1,                    # 100 micron spacing between lines
    parameters=parameters,
    replication_count=5,            # 5 replicates per condition
    description="Full factorial ablation parameter sweep on glass with 20x objective",
)

results = exp.run()
print(results.head())
# Results saved to: ./results/ParOpt_01/experiment_design.csv
```

### 7.4 Running a 2PP Z-Stack Experiment

```python
from laser_printing.experiment.parameter_sweep import ParameterSweep

exp = ParameterSweep(
    experiment_id="2PP_ZStack_01",
    output_dir="./results",
    z_focus=5.9981,
    mode="z_stack",
    x_start=4, x_end=-1,
    y_start=8, y_pitch=0.03,       # 30 micron shift between stacks
    z_step=0.001,                   # 1 micron Z spacing within stack
    z_count=5,                      # 5 lines per stack
    start_power=30, end_power=42,   # Power sweep range (%)
    power_step=2,                   # 2% increments
    motor_velocity=5.0,
    description="Z-stack power sweep for 2PP threshold finding",
)

results = exp.run()
```

### 7.5 Running a Motion Profiling Experiment (Calibration)

This experiment does NOT use the laser. It characterizes the stage's acceleration behavior to build the synchronization calibration table.

```python
from laser_printing.experiment.motion_profiling import MotionProfiling

exp = MotionProfiling(
    experiment_id="accel_jerk_sweep",
    output_dir="./results",
    z_focus=6.0,
    x_start=1.0, x_end=2.0,        # 1 mm test line
    y_line=7.0,
    target_velocity=5.0,
    acceleration_values=[5, 10, 25, 50, 125, 150],
    jerk_values=[5, 10, 25, 50, 125, 150],
    description="Acceleration/jerk sweep for synchronization calibration",
)

results = exp.run()
# Use results["accel_time_s"] and results["accel_distance_mm"]
# to update config/default.yaml synchronization.calibration
```

### 7.6 STL-Based 3D Printing

```python
from laser_printing.path_generation.stl import generate_from_stl
from laser_printing.visualization.plotting import plot_path_3d
import numpy as np

# Generate scan path from STL file
path = generate_from_stl(
    stl_file="models/cube_100.stl",
    unit="micron",                   # STL units
    step_size=5.0,                   # Discretization step (microns)
    start_position=np.array([0., 0., 6.]),  # Center position (mm)
    repetition_count=1,
    is_ablation=False,               # True for ablation, False for fabrication
)

# Preview the path before printing
plot_path_3d(path, title="Cube 100um - Print Preview")

# Execute with synchronized laser control
from laser_printing.config import load_config
from laser_printing.controllers.laser import LaserController
from laser_printing.controllers.stage import StageController
from laser_printing.controllers.sync import PrintSynchronizer

config = load_config()

with LaserController.from_config(config["laser"]) as laser, \
     StageController.from_config(config["stage"]) as stage:
    
    laser.set_attenuator(30)
    laser.set_pp_divider(1)
    stage.set_velocity(5.0)
    
    sync = PrintSynchronizer.from_config(config, laser, stage)
    sync.execute_path(path)
```

### 7.7 Using Controllers Directly (Advanced)

```python
from laser_printing.config import load_config
from laser_printing.controllers.laser import LaserController
from laser_printing.controllers.stage import StageController

config = load_config()

# Context managers guarantee safe cleanup
with LaserController.from_config(config["laser"]) as laser, \
     StageController.from_config(config["stage"]) as stage:
    
    # Check laser status
    status = laser.get_status()
    print(f"Laser operational: {status}")
    
    # Read current stage position
    pos = stage.get_position()
    print(f"Current position: X={pos[0]:.3f}, Y={pos[1]:.3f}, Z={pos[2]:.3f} mm")
    
    # Move stage
    stage.set_velocity([5, 5, 5])
    stage.move_to([10, 0, 6])
    
    # Set laser parameters
    laser.set_attenuator(25)
    laser.set_pp_divider(1)
    
    # Fire laser
    laser.enable_output()
    # ... do something ...
    laser.disable_output()
```

### 7.8 Visualizing Paths Without Hardware

Path generation and visualization work without any hardware connection:

```python
from laser_printing.path_generation.coordinates import lines_with_pitch
from laser_printing.visualization.plotting import plot_path_3d

# Generate a grid of lines
blocks = lines_with_pitch(
    x_start=0, x_end=5, y_start=0, y_pitch=0.1,
    z_focus=6, count=20, repetitions=1,
)

# Flatten blocks into a single path for visualization
all_points = []
for block in blocks:
    all_points.extend(block)

plot_path_3d(all_points, title="20-line grid preview")
```

---

## 8. Configuration

### 8.1 Configuration File Format

The configuration file (`config/default.yaml`) has four sections:

```yaml
laser:
  ip: "192.168.244.10"          # Laser IP address
  api_base: "/phoebe/v0/Basic"  # API endpoint base path
  default_attenuator_percentage: 30
  default_pp_divider: 1
  command_delay_s: 0.1          # Delay after each command (seconds)

stage:
  ip: "10.0.0.100"              # Controller IP
  port: 701                     # Controller port
  axes: [0, 1, 2]               # Active axes
  commutation_axes: [0, 1]      # Axes needing brushless commutation
  default_velocity: [1.0, 1.0, 1.0]  # Default speed (mm/s)
  range_mm: [-25.0, 25.0]       # Physical travel range

synchronization:
  strategy: "velocity_timed"    # or "sequential"
  velocity_tolerance_fraction: 0.05
  calibration:                  # Measured from MotionProfiling experiments
    - velocity_mm_s: 5.0
      accel_time_s: 0.357
      accel_distance_mm: 0.79

logging:
  level: "INFO"                 # DEBUG, INFO, WARNING, ERROR
  file: null                    # null = console only; set path for file logging
```

### 8.2 Loading a Custom Config

```python
from laser_printing.config import load_config

# Load default config
config = load_config()

# Load a custom config
config = load_config("config/my_setup.yaml")

# Pass to any experiment
exp = ParameterSweep(config_path="config/my_setup.yaml", ...)
```

---

## 9. Open Questions and Future Work

### 9.1 Questions for the Team

These questions arose during the code review and may affect future development decisions:

1. **Hardware TTL triggers**: Does the ACS SPiiPlus controller support hardware-triggered digital outputs? If so, we could trigger the laser based on real-time velocity or position thresholds, eliminating the need for software timing entirely.

2. **Laser enable/disable latency**: The 100ms delay after each laser API call - is this measured or a conservative guess? The actual latency affects synchronization accuracy.

3. **SPiiPlusPython version**: What version is installed? The `SetAcceleration()` and `SetJerk()` functions may or may not be available depending on firmware.

4. **Calibration values**: The timing values (0.357s, 0.79mm at 5mm/s) in `async_motor.py` - were these measured experimentally? Are there measurements for other velocities?

5. **STL printing symptom**: Is the "laser flickers on/off" problem during scan lines, between scan lines, or both?

6. **Laser state query**: Can we read the laser's actual output state from the API to confirm synchronization?

7. **Servo cycle time**: The `scycle = 0.001064s` value in data collection - how was this determined?

8. **Relative coordinates**: Should experiments work in relative coordinates (deltas from current position) or absolute coordinates?

9. **Motion buffering**: Can SPiiPlus buffer multiple motion commands for smoother multi-segment paths?

10. **Brussels team update**: Any news on alternative synchronization approaches?

### 9.2 Recommended Next Steps

1. **Build calibration table**: Run `MotionProfiling` at velocities 1, 2, 3, 5, 7, 10 mm/s to populate the synchronization calibration in `default.yaml`

2. **Test STL printing**: Run a simple STL (cube) with the new synchronizer to verify the flickering bug is fixed

3. **Investigate hardware triggers**: Check if the ACS controller can output TTL signals based on velocity thresholds (this would be the ideal synchronization solution)

4. **User interface**: Phase 5 from the presentation - CLI first, then GUI for non-expert lab users

### 9.3 Mapping: Old Files to New Modules

For reference when migrating existing experiment scripts:

| Old File | New Module | Notes |
|----------|------------|-------|
| `main.py` (MotorControl) | `controllers/stage.py` (StageController) | Context manager, data collection added |
| `controlers/laser_control.py` (LaserAPI) | `controllers/laser.py` (LaserController) | Fixed validation, no global singleton |
| `controlers/laser_control.py` (AsyncLaserAPI) | Removed | Async approach replaced by velocity-timed sync |
| `controlers/async_motor.py` | `controllers/sync.py` (PrintSynchronizer) | Generalized from hardcoded timing |
| `controlers/lines_laser_states_from_coords.py` | `path_generation/coordinates.py` | Added missing `lines_with_pitch` |
| `controlers/line_laser_states_from_stl.py` | `path_generation/stl.py` | Staged pipeline, clearer data flow |
| `controlers/point_by_point_motion.py` | Not yet migrated | Voxel-based printing (future) |
| `utils/utils.py` | `utils/mesh.py` + `utils/io.py` | Split by responsibility |
| `utils/experiment_design.py` | `utils/doe.py` | Cleaner API |
| `utils/error_handling.py` | Removed | Validation moved into controllers |
| `visualization/visualization.py` | `visualization/plotting.py` | Added motion profile plots |
| `experiments/parameter_optimization.py` | `experiment/parameter_sweep.py` (mode="line_doe") | Unified with z_stack |
| `experiments/parameter_optimization_for_2pp.py` | `experiment/parameter_sweep.py` (mode="z_stack") | Unified with line_doe |
| `experiments/motor_acceleration_experiment.py` | `experiment/motion_profiling.py` | Cleaner, integrated with calibration |

---

*Document generated: 2026-04-08*
*Software version: 0.1.0*
