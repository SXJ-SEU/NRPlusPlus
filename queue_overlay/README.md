# Queue Overlay

Native macOS queue visualizer for the local Android game fixture. It monitors
the scheduler record before activation and renders a click-through arena overlay.

## Run

Calibrate the 18x32 arena grid once:

```bash
queue_overlay/run_calibration.sh
```

Move or edge-resize the grid, then save the profile. Use `Flip Arena 180 deg`
for the opposite home/away orientation. The saved profile is
`arena_grid_profile.json` in this directory.

Start the live click-through overlay:

```bash
queue_overlay/run_overlay.sh
```

Queued tiles flash red for one second and show the card name. Card names come
from the bundled `card_catalog.json`.

For console-only operation:

```bash
queue_overlay/run_queue_cli.sh
```

## Files

- `arena_overlay.py`: transparent macOS overlay, calibration UI, and Frida bridge.
- `hook_queue_deploy.js`: low-overhead scheduler queue probe.
- `queue_cli.py`: console event formatter.
- `card_catalog.json`: card ID to display-name mapping.
