import numpy as np

from framewire.tracking import MotionTracker


def test_motion_tracker_warms_up_then_finds_moving_region():
    tracker = MotionTracker()
    empty = np.zeros((120, 160, 3), dtype=np.uint8)
    for _ in range(13):
        assert tracker.detect(empty) is None
    moved = empty.copy()
    moved[30:70, 80:120] = 255
    target = tracker.detect(moved)
    assert target is not None
    assert target.kind == "motion"
    assert 0.5 < target.x < 0.75
