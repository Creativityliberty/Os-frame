# A2A Task state machine

States:
- `submitted`
- `working`
- `input-required`
- `completed`
- `failed`
- `canceled`

Transitions (typical):
- submitted -> working
- working -> input-required (approval gate)
- input-required -> working (approved)
- input-required -> canceled/failed (denied/timeout)
- working -> completed
- working -> failed

This mirrors A2A lifecycle concepts and supports UI progress tracking.

