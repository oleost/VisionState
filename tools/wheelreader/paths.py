"""Where the data lives: never in the repository (``VisionStateLocal/`` is ignored by git)."""

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOCAL = Path(os.environ.get("VISIONSTATE_LOCAL", REPO / "VisionStateLocal"))
WORK = LOCAL / "wheel"  # data/ (built sets), runs/ (models, baselines), fonts/
DRYAD = LOCAL / "dryad-wordwheel/rec/Word-Wheel_Water_Meter_Dataset/recognition"
