"""v1.14 patch 5: box-docked ratification for the landing-bounce straddle.

Trace-measured (tools/_trace_tail.py, both videos, v1.14d):

  The settle-required confirm needs the confirming read to land on a
  frame whose ball-window diff vs the PREVIOUS processed poll is < 3.0.
  A ball that lands between two polls never gets such a pair while the
  bounce tail (~0.6 s) outlives the poll gap, so a round's last ball
  (I26 play1 146.8s, O67 play2 152.9s: three agreeing 1.00 reads each,
  diffs 33/7/9 and 39/12/9) ran out of round before ratification.

  Fix: ALSO ratify once this ball has been read identically a third
  time AND its Hough box stopped moving between the last two reads
  (docked jitter <=2px/poll vs 10-45px/poll for a sliding or flying
  ball), so mid-motion misreads stay blocked.  Verified on both videos:
  I26 commits 146.84, O67 commits 152.92, all other commits unchanged.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def patch(path: Path, pairs) -> None:
    src = path.read_bytes().decode("utf8")
    for old, new in pairs:
        o = old.replace("\n", "\r\n")
        n = new.replace("\n", "\r\n")
        assert src.count(o) == 1, f"{path.name}: marker not unique: {old[:60]!r}"
        src = src.replace(o, n, 1)
    path.write_bytes(src.encode("utf8"))
    print(f"{path.name}: {len(pairs)} replacements")


patch(ROOT / "app.py", [
    # 1. pending_box attribute
    (
        "        self.pending: tuple[str, int] | None = None\n"
        "        self.pending_n = 0\n"
        "        self.pending_t0 = 0.0\n",
        "        self.pending: tuple[str, int] | None = None\n"
        "        self.pending_n = 0\n"
        "        self.pending_t0 = 0.0\n"
        "        self.pending_box: tuple[int, int, int, int] | None = None\n",
    ),
    # 2. repeat-suppress also drops the box anchor
    (
        "            self.pending = None\n"
        "            self.pending_n = 0\n"
        "            return\n",
        "            self.pending = None\n"
        "            self.pending_n = 0\n"
        "            self.pending_box = None\n"
        "            return\n",
    ),
    # 3. the confirm block: settle fast path + docked 3-read path
    (
        "        if self.pending == key:\n"
        "            # Second agreeing read, and only on a frame where this\n"
        "            # ball's own pixels stopped moving: commit for real.  Two\n"
        "            # reads of one frozen mid-animation frame must never ratify\n"
        "            # each other (pixel-forensics: that is how B2/N34/B8\n"
        "            # phantom commits survived single-frame confirmation).\n"
        "            if settled and self.pending_n == 1:\n"
        "                self.pending = None\n"
        "                self.pending_n = 0\n"
        "                self.commit(letter, num, frame)\n"
        "            return\n",
        "        if self.pending == key:\n"
        "            # Commit on a frame where this ball's own pixels stopped\n"
        "            # moving (two mid-animation reads must never ratify each\n"
        "            # other; pixel-forensics: that is how B2/N34/B8 phantoms\n"
        "            # survived single-frame confirmation).  That pair compares\n"
        "            # this poll against the previous poll, so a ball that\n"
        "            # lands between two polls reads as \"moving\" one poll too\n"
        "            # long - at a round's end that burned the whole confirm\n"
        "            # window (I26 play1 146.8s, O67 play2 152.9s: three\n"
        "            # agreeing 1.00 reads each, never ratified).  So also\n"
        "            # ratify once this ball has been read identically a\n"
        "            # third time AND its box stopped moving between the last\n"
        "            # two reads: a sliding or flying ball never holds its box\n"
        "            # (measured 10-45px/poll vs <=2px for a docked one), so\n"
        "            # mid-motion misreads stay blocked.\n"
        "            if settled or (self.pending_n >= 2\n"
        "                           and self._ball_docked(bbox)):\n"
        "                self.pending = None\n"
        "                self.pending_n = 0\n"
        "                self.pending_box = None\n"
        "                self.commit(letter, num, frame)\n"
        "            else:\n"
        "                # agreeing, not yet ratifiable: count the read\n"
        "                self.pending_n += 1\n"
        "                self.pending_box = bbox\n"
        "            return\n",
    ),
    # 4. first read anchors the box
    (
        "        self.pending = key\n"
        "        self.pending_n = 1\n"
        "        self.pending_t0 = time.perf_counter()\n",
        "        self.pending = key\n"
        "        self.pending_n = 1\n"
        "        self.pending_t0 = time.perf_counter()\n"
        "        self.pending_box = bbox\n",
    ),
    # 5. the _ball_docked helper
    (
        "        a = crop[cy0:cy1, cx0:cx1]\n"
        "        b = prev_crop[cy0:cy1, cx0:cx1]\n"
        "        return float(cv2.absdiff(a, b).mean()) < 3.0\n"
        "\n"
        "    def _instant_ball_active(self, frame, bboxes=None) -> bool:\n",
        "        a = crop[cy0:cy1, cx0:cx1]\n"
        "        b = prev_crop[cy0:cy1, cx0:cx1]\n"
        "        return float(cv2.absdiff(a, b).mean()) < 3.0\n"
        "\n"
        "    def _ball_docked(self, bbox) -> bool:\n"
        "        \"\"\"True when this ball's box stopped moving since the last read.\n"
        "\n"
        "        The pixel-settle pair needs the previous poll to already hold\n"
        "        the landed ball; a ball that lands mid-interval never gets\n"
        "        such a pair.  Box stability is the motion signal that\n"
        "        survives that: Hough's box of a docked ball jitters ~2px\n"
        "        while a sliding or flying ball moves 10-45px per poll.\n"
        "        \"\"\"\n"
        "        if self.pending_box is None or self.grid is None:\n"
        "            return False\n"
        "        px, py, pw, ph = self.pending_box\n"
        "        x, y, w, h = bbox\n"
        "        cell = (self.grid.x1 - self.grid.x0) / 5.0\n"
        "        return (abs(x - px) <= 0.05 * cell and abs(y - py) <= 0.05 * cell\n"
        "                and abs(w - pw) <= 0.10 * cell\n"
        "                and abs(h - ph) <= 0.10 * cell)\n"
        "\n"
        "    def _instant_ball_active(self, frame, bboxes=None) -> bool:\n",
    ),
])
