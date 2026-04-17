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
# Results saved to: ./results/remo/ParOpt_01/experiment_design.csv