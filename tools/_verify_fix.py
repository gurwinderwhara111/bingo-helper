"""Verify the proposed 3-agreeing-read commit fix without touching app.py.

Replays videos exactly like tools/_trace_tail.py (fps-4 sampling, one poll
per sampled frame) with App._handle_ball replaced by the proposed version:
agreeing reads accumulate in pending_n, and a call commits on the first
settled agreeing read OR on the third consecutive agreeing read (>=0.92
score each, letter-contradiction and repeat-suppress checks unchanged).

COMMIT lines print for the whole run (timestamped); poll/read detail prints
only inside the requested window, so the run can be diffed against the
v1.14d baseline traces.

Usage:
  python tools/_verify_fix.py "<video>" <print_from> <print_to> [<video2> <from2> <to2> ...]
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import cv2  # noqa: E402
import time  # noqa: E402
import numpy as np  # noqa: E402

import app as A  # noqa: E402
import vision as V  # noqa: E402

_args = sys.argv[1:]
VIDEOS = []
for i in range(0, len(_args), 3):
    VIDEOS.append((Path(_args[i]), float(_args[i + 1]), float(_args[i + 2])))

_state = {"t": 0.0, "win": (0.0, 1e9)}


def log(msg, always=False):
    t = _state["t"]
    lo, hi = _state["win"]
    if always or (lo <= t <= hi):
        print(f"{t:7.2f}s {msg}", flush=True)


# ---------------------------------------------- proposed _handle_ball (fix)
def handle_fixed(self, frame, bbox, settled: bool = False) -> None:
    cell = (self.grid.x1 - self.grid.x0) / 5.0
    d = (bbox[2] + bbox[3]) / 2
    if not (V.BALL_DMIN_F * cell <= d <= V.BALL_DMAX_F * cell):
        nowt = time.time()
        if nowt - self.last_disagree_log >= 1.0:
            self.last_disagree_log = nowt
            print(f"ball out of size band (d={d:.0f}px) - waiting")
        self.last_ball_crop = None
        return
    ball = V.read_ball_robust(self.engine, frame, bbox,
                              min_score=A.BALL_SCORE, fast=False)
    num = ball["number"]
    if num is None or not (1 <= num <= 75) or ball["number_score"] < A.BALL_SCORE:
        self.last_ball_crop = None
        nowt = time.time()
        if nowt - self.last_unread_log >= 1.0:
            self.last_unread_log = nowt
            print(f"ball unread (score {ball['number_score']:.2f})"
                  " - retrying")
        return
    derived = V.letter_for(num)
    if derived is None:
        self.last_ball_crop = None
        return
    letter = derived
    ltr = ball.get("letter") or ""
    if ltr and ball.get("letter_score", 0.0) >= 0.80 and ltr != letter:
        self.last_ball_crop = None
        nowt = time.time()
        if nowt - self.last_disagree_log >= 1.0:
            self.last_disagree_log = nowt
            print(f"letter/number contradiction ({ltr} vs "
                  f"{letter} {num}) - read rejected")
        return
    key = (letter, num)
    if key in self.last_calls:
        if key not in self.repeat_warned:
            self.repeat_warned.add(key)
            print(f"repeat read {letter} {num} - already called, "
                  "suppressed (possible misread)")
        self.pending = None
        self.pending_n = 0
        return
    if self.pending == key:
        # THE FIX: agreeing reads accumulate; commit on the first
        # settled confirming read, or on the third consecutive
        # agreeing read even if the pixels never measured still.
        self.pending_n += 1
        log(f"   AGREE#{self.pending_n} {letter}{num} settled={settled}")
        if settled or self.pending_n >= 3:
            self.pending = None
            self.pending_n = 0
            self.commit(letter, num, frame)
        return
    if self.pending is not None and self.pending != key:
        nowt = time.time()
        if nowt - self.last_disagree_log >= 1.0:
            self.last_disagree_log = nowt
            p = self.pending
            print(f"ball reads disagree ({p[0]} {p[1]} vs "
                  f"{letter} {num}) - trusting neither yet")
    self.pending = key
    self.pending_n = 1
    self.pending_t0 = time.perf_counter()


A.App._handle_ball = handle_fixed

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

_orig_commit = A.App.commit


def commit_logged(self, letter, num, frame, *a, **kw):
    log(f"   *** COMMIT {letter} {num} ***", always=True)
    return _orig_commit(self, letter, num, frame, *a, **kw)


A.App.commit = commit_logged

_orig_iact = A.App._instant_ball_active


def iact_logged(self, frame, bboxes=None, *a, **kw):
    r = _orig_iact(self, frame, bboxes, *a, **kw)
    log(f"   _instant_ball_active -> {r}")
    return r


A.App._instant_ball_active = iact_logged

_orig_poll = A.App.poll_ball


def poll_logged(self, frame, *a, **kw):
    _state["n"] = 0
    log(f"POLL pend=({self.pending},{self.pending_n}) "
        f"crop={'none' if self.last_ball_crop is None else 'set'} "
        f"vis={self.card_visible}")
    r = _orig_poll(self, frame, *a, **kw)
    return r


A.App.poll_ball = poll_logged

# ---------------------------------------------------------------- harness
from replay_video import Clock, StubOverlay  # noqa: E402
from rapidocr import RapidOCR  # noqa: E402


def run(video, t_from, t_to):
    _state["win"] = (t_from, t_to)
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        print(f"cannot open {video}")
        return
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    step = max(1, int(round(fps / 4.0)))
    clock = Clock()
    A.time = clock
    app = A.App()
    app.dry_run = True
    app.overlay = StubOverlay()
    app.engine = RapidOCR()
    app.engine(np.full((64, 64, 3), 255, np.uint8),
               use_det=False, use_cls=False, use_rec=True)
    print(f"=== fixed run {video.name} (detail {t_from}-{t_to}s) ===",
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
    print(f"=== done {video.name} ===", flush=True)


for vid, f0, f1 in VIDEOS:
    run(vid, f0, f1)
