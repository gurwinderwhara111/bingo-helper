"""Trace the production ball flow and explain tail-window misses.

Drives the real App exactly like tools/replay_video.py (same clock, same
fps-4 sampling, same poll cadence) but wraps the key call points so every
poll in a requested time window logs:

  POLL entry state (pending_n / last_multi / crop state / visibility)
  the gate metrics (absdiff whole + hot means) and whether the gate skipped
  find_balls output, branch taken, every read (letter/number/scores),
  _check_pick_me_up / _instant_ball_active verdicts, commits.

Read-only for app.py / vision.py - everything is monkeypatched here.

Usage:
  python tools/_trace_tail.py "<video path>" <print_from_s> [print_to_s]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import app as A  # noqa: E402
import vision as V  # noqa: E402

VIDEO = Path(sys.argv[1])
T_FROM = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
T_TO = float(sys.argv[3]) if len(sys.argv) > 3 else 1e9

_state = {"t": 0.0, "poll": 0, "n": 0}


def log(msg):
    t = _state["t"]
    if T_FROM <= t <= T_TO:
        print(f"{t:7.2f}s p{_state['poll']:03d} {msg}", flush=True)


# ------------------------------------------------------------ wrappers
_orig_absdiff = cv2.absdiff


def absdiff_logged(a, b, *args, **kw):
    r = _orig_absdiff(a, b, *args, **kw)
    try:
        m = float(r.mean())
    except Exception:
        m = -1.0
    _state["n"] += 1
    log(f"   absdiff#{_state['n']} shape={a.shape} mean={m:.2f}")
    return r


cv2.absdiff = absdiff_logged

_orig_read = V.read_ball_robust


def read_logged(engine, img, bbox, *a, **kw):
    r = _orig_read(engine, img, bbox, *a, **kw)
    log(f"   READ ({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}) "
        f"fast={kw.get('fast', True)} -> {r['letter']}{r['number']}/"
        f"{r['number_score']:.2f}/L{r.get('letter_score', -1):.2f}")
    return r


V.read_ball_robust = read_logged

_orig_find = V.find_balls


def find_logged(img, grid, *a, **kw):
    r = _orig_find(img, grid, *a, **kw)
    log(f"   find_balls -> {[(b[0], b[1], b[2], b[3]) for b in r]}")
    return r


V.find_balls = find_logged

_orig_handle = A.App._handle_ball


def handle_logged(self, frame, bbox, *a, **kw):
    log(f"   _handle_ball ({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}) {kw}")
    return _orig_handle(self, frame, bbox, *a, **kw)


A.App._handle_ball = handle_logged

_orig_pinst = A.App._poll_instant


def pinst_logged(self, *a, **kw):
    log(f"   _poll_instant boxes={a[1] if len(a) > 1 else '?'}")
    return _orig_pinst(self, *a, **kw)


A.App._poll_instant = pinst_logged

_orig_commit = A.App.commit


def commit_logged(self, letter, num, frame, *a, **kw):
    log(f"   COMMIT {letter} {num}")
    return _orig_commit(self, letter, num, frame, *a, **kw)


A.App.commit = commit_logged

_orig_pmu = A.App._check_pick_me_up


def pmu_logged(self, frame, *a, **kw):
    r = _orig_pmu(self, frame, *a, **kw)
    log(f"   _check_pick_me_up -> {r}")
    return r


A.App._check_pick_me_up = pmu_logged

_orig_iact = A.App._instant_ball_active


def iact_logged(self, frame, bboxes=None, *a, **kw):
    r = _orig_iact(self, frame, bboxes, *a, **kw)
    log(f"   _instant_ball_active -> {r}")
    return r


A.App._instant_ball_active = iact_logged

_orig_band = V.find_pink_band


def band_logged(frame, *a, **kw):
    r = _orig_band(frame, *a, **kw)
    if r is not None:
        log(f"   find_pink_band -> {r}")
    return r


V.find_pink_band = band_logged

_orig_chg = A.App._changed_cells


def chg_logged(self, frame, *a, **kw):
    r = _orig_chg(self, frame, *a, **kw)
    log(f"   _changed_cells -> {r}")
    return r


A.App._changed_cells = chg_logged

_orig_poll = A.App.poll_ball


def poll_logged(self, frame, *a, **kw):
    _state["n"] = 0
    _state["poll"] += 1
    log(f"POLL pend_n={self.pending_n} multi={self.last_multi} "
        f"crop={'none' if self.last_ball_crop is None else 'set'} "
        f"vis={self.card_visible}")
    r = _orig_poll(self, frame, *a, **kw)
    log(f"POLL end (absdiff_calls={_state['n']})")
    return r


A.App.poll_ball = poll_logged

_orig_cr = A.App.check_round


def cr_logged(self, frame, *a, **kw):
    log("CHECK_ROUND")
    return _orig_cr(self, frame, *a, **kw)


A.App.check_round = cr_logged

_orig_cp = A.App._confirm_pending


def cp_logged(self, frame, *a, **kw):
    log(f"CONFIRM_PENDING pend={self.pending} n={self.pending_n}")
    return _orig_cp(self, frame, *a, **kw)


A.App._confirm_pending = cp_logged

# ---------------------------------------------------------------- harness
from replay_video import Clock, StubOverlay  # noqa: E402
from rapidocr import RapidOCR  # noqa: E402


def main():
    cap = cv2.VideoCapture(str(VIDEO))
    if not cap.isOpened():
        print(f"cannot open {VIDEO}")
        return 1
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total / fps
    step = max(1, int(round(fps / 4.0)))
    end = duration

    clock = Clock()
    A.time = clock
    app = A.App()
    app.dry_run = True
    app.overlay = StubOverlay()
    app.engine = RapidOCR()
    app.engine(np.full((64, 64, 3), 255, np.uint8),
               use_det=False, use_cls=False, use_rec=True)

    print(f"tracing {VIDEO.name} {duration:.1f}s, printing {T_FROM}-{T_TO}s",
          flush=True)
    frame_index = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = frame_index / fps
        frame_index += 1
        if frame_index % step != 0:
            continue
        _state["t"] = t
        clock.t = t
        app.source_image = frame
        if app.grid is None:
            if t - app.last_detect_try >= 0.6:
                app.detect(quiet=True)
        else:
            app._confirm_pending(frame)
            app.check_round(frame)
            app.poll_ball(frame)
    cap.release()
    print("trace done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
