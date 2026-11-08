"""Offline replay: run the real helper logic over a gameplay video.

The video is decoded locally; frames are sampled at a low rate so the run is
fast.  A virtual clock keeps the helper's cooldowns honest.  The overlay is
stubbed out, so nothing is drawn and nothing is clicked.

Usage:
    python debug/replay_video.py "gameplay videos/bingo normal live play1.mp4"
    python debug/replay_video.py <video> --fps 4 --start 20 --end 150
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as A
import bingo as B


class StubOverlay:
    def hide(self) -> None:
        pass

    def show(self, *_args, **_kwargs) -> None:
        pass

    def destroy(self) -> None:
        pass


class Clock:
    """A deterministic clock driven by the video timestamp."""

    def __init__(self) -> None:
        self.t = 0.0

    def time(self) -> float:
        return self.t

    def perf_counter(self) -> float:
        return self.t

    def sleep(self, _seconds: float) -> None:
        pass


def snapshot(app: A.App) -> dict:
    grid = None if app.grid is None else tuple(app.grid.rect)
    card = None if app.card is None else tuple(tuple(row) for row in app.card)
    daubed = tuple((r, c) for r in range(5) for c in range(5)
                   if app.daubed[r][c] and (r, c) not in app.free_cells)
    calls = tuple(sorted(app.last_calls))
    current = tuple(sorted(app.current_cells))
    pending = tuple(sorted(app.pending_cells))
    popup = None
    if app.popup is not None:
        popup = {
            "offers": app.popup.get("offers"),
            "best": app.popup.get("best"),
            "manual": app.popup.get("manual"),
            "reason": app.popup.get("reason"),
        }
    return {
        "grid": grid,
        "visible": app.card_visible,
        "card": card,
        "daubed": daubed,
        "calls": calls,
        "current": current,
        "pending": pending,
        "popup": popup,
        "free": tuple(sorted(app.free_cells)),
    }


def flatten(snap: dict) -> dict:
    return {
        "grid": "" if snap["grid"] is None else json.dumps(snap["grid"]),
        "visible": int(snap["visible"]),
        "card": "" if snap["card"] is None else json.dumps(snap["card"]),
        "daubed": json.dumps(snap["daubed"]),
        "calls": json.dumps(snap["calls"]),
        "current": json.dumps(snap["current"]),
        "pending": json.dumps(snap["pending"]),
        "popup": "" if snap["popup"] is None else json.dumps(snap["popup"]),
        "free": json.dumps(snap["free"]),
    }


def describe_change(prev: dict, cur: dict) -> str:
    changes = []
    if prev["grid"] != cur["grid"]:
        changes.append(f"grid {prev['grid']} -> {cur['grid']}")
    if prev["visible"] != cur["visible"]:
        changes.append(f"visible {prev['visible']} -> {cur['visible']}")
    if prev["card"] != cur["card"]:
        changes.append("card changed")
    if prev["daubed"] != cur["daubed"]:
        old = set(prev["daubed"])
        new = set(cur["daubed"])
        changes.append("daub +" + str(sorted(new - old)) + " -" + str(sorted(old - new)))
    if prev["calls"] != cur["calls"]:
        old = set(prev["calls"])
        new = set(cur["calls"])
        changes.append("calls +" + str(sorted(new - old)))
    if prev["current"] != cur["current"]:
        changes.append(f"current {prev['current']} -> {cur['current']}")
    if prev["popup"] != cur["popup"]:
        changes.append(f"popup {prev['popup']} -> {cur['popup']}")
    return "; ".join(changes)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--fps", type=float, default=4.0,
                    help="sampling rate for replay (default 4)")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--end", type=float, default=0.0,
                    help="stop time in seconds; 0 means end of video")
    ap.add_argument("--out", default="")
    ap.add_argument("--save-events", action="store_true",
                    help="save half-size JPEGs when the helper state changes")
    args = ap.parse_args()

    video = Path(args.video)
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        print(f"cannot open {video}")
        return 1
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    duration = total / fps
    step = max(1, int(round(fps / max(0.2, args.fps))))
    end = args.end if args.end > 0 else duration
    outdir = Path(args.out) if args.out else Path("debug") / f"replay_{video.stem.replace(' ', '_')}"
    outdir.mkdir(parents=True, exist_ok=True)
    eventdir = outdir / "events"
    if args.save_events:
        eventdir.mkdir(parents=True, exist_ok=True)

    print(f"video: {video.name}  {duration:.1f}s  {fps:.2f}fps  sample={fps/step:.2f}fps")
    print(f"output: {outdir}")

    clock = Clock()
    A.time = clock  # all helper timers use the video clock

    app = A.App()
    app.dry_run = True
    app.overlay = StubOverlay()
    from rapidocr import RapidOCR
    app.engine = RapidOCR()
    app.engine(__import__("numpy").full((64, 64, 3), 255, __import__("numpy").uint8),
               use_det=False, use_cls=False, use_rec=True)

    rows_path = outdir / "timeline.csv"
    events_path = outdir / "events.txt"
    fields = ["t", "grid", "visible", "card", "daubed", "calls",
              "current", "pending", "popup", "free", "change"]
    csv_file = open(rows_path, "w", newline="", encoding="utf-8")
    writer = csv.DictWriter(csv_file, fieldnames=fields)
    writer.writeheader()
    events = open(events_path, "w", encoding="utf-8")

    prev = snapshot(app)
    frame_index = 0
    sampled = 0
    detections = 0
    event_count = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        t = frame_index / fps
        frame_index += 1
        if t < args.start:
            continue
        if t > end:
            break
        if frame_index % step != 0:
            continue

        clock.t = t
        app.source_image = frame
        before = snapshot(app)

        if app.grid is None:
            # Match the live loop's retry cadence.
            if t - app.last_detect_try >= 0.6:
                app.detect(quiet=True)
        else:
            app._confirm_pending(frame)
            app.check_round(frame)
            app.poll_ball(frame)

        after = snapshot(app)
        change = describe_change(before, after)
        if after["grid"] is not None and before["grid"] is None:
            detections += 1
        writer.writerow({"t": f"{t:.3f}", **flatten(after), "change": change})

        if change:
            event_count += 1
            line = f"{t:7.2f}s  {change}"
            print(line)
            events.write(line + "\n")
            if args.save_events and (before["calls"] != after["calls"]
                                     or before["popup"] != after["popup"]
                                     or before["card"] != after["card"]
                                     or before["current"] != after["current"]
                                     or before["visible"] != after["visible"]):
                small = cv2.resize(frame, (frame.shape[1] // 2, frame.shape[0] // 2))
                name = f"{event_count:03d}_{t:07.2f}s.jpg"
                cv2.imwrite(str(eventdir / name), small,
                            [cv2.IMWRITE_JPEG_QUALITY, 88])
        prev = after
        sampled += 1

    cap.release()
    csv_file.close()
    events.close()
    print(f"\nsampled {sampled} frames, {detections} card locks, {event_count} events")
    print(f"timeline: {rows_path}")
    print(f"events:   {events_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
