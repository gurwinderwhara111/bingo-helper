"""v1.14 patch 6: per-frame fault isolation (the mid-game death fix).

bingo.log census: 8 mid-game process deaths, all one class - an unexpected
exception inside the poll loop (OCR garbage 'ï¿½'/'?3'/'1' hitting int(),
one old NameError) killed the whole app; the user kept playing unprotected
while re-scanning.  read_card_ex/read_ball_robust already filter OCR text
to ASCII digits (that fix landed after those crashes) - but the loop
itself still catches nothing, so the NEXT unknown exception dies the same
way.

Fix: the standard watcher pattern (per-iteration fault isolation - one bad
task never kills the supervisor):
  - App._poll_once(frame): confirm + check_round + poll_ball wrapped;
    exceptions are counted and logged (traceback) at most once per second
    and the watch continues.
  - App._safe_detect(): same guard around every detect() call site.
No behaviour change on healthy frames.
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
    # 1. traceback import
    (
        "import sys\n"
        "import time\n",
        "import sys\n"
        "import time\n"
        "import traceback\n",
    ),
    # 2. guard counters
    (
        "        self.pending_t0 = 0.0\n"
        "        self.pending_box: tuple[int, int, int, int] | None = None\n",
        "        self.pending_t0 = 0.0\n"
        "        self.pending_box: tuple[int, int, int, int] | None = None\n"
        "        self.crash_count = 0\n"
        "        self.last_crash_log = 0.0\n",
    ),
    # 3. hotkey Scan press
    (
        "                for kid in pump_messages():\n"
        "                    if kid == 1:\n"
        "                        self.detect()\n",
        "                for kid in pump_messages():\n"
        "                    if kid == 1:\n"
        "                        self._safe_detect()\n",
    ),
    # 4. F8 / bar rescan
    (
        "                if self.rescan_requested:\n"
        "                    self.rescan_requested = False\n"
        "                    self.detect()\n",
        "                if self.rescan_requested:\n"
        "                    self.rescan_requested = False\n"
        "                    self._safe_detect()\n",
    ),
    # 5. auto-retry while unlocked
    (
        "                if self.grid is None:\n"
        "                    if time.time() - self.last_detect_try > 0.6:\n"
        "                        self.detect(quiet=self.detect_fail > 0)\n",
        "                if self.grid is None:\n"
        "                    if time.time() - self.last_detect_try > 0.6:\n"
        "                        self._safe_detect("
        "quiet=self.detect_fail > 0)\n",
    ),
    # 6. the poll body
    (
        "                    frame = self.grab()\n"
        "                    if frame is not None:\n"
        "                        self._confirm_pending(frame)\n"
        "                        self.check_round(frame)\n"
        "                        self.poll_ball(frame)\n",
        "                    frame = self.grab()\n"
        "                    if frame is not None:\n"
        "                        self._poll_once(frame)\n",
    ),
    # 7. the guarded methods
    (
        "    def cleanup(self) -> None:\n",
        "    def _guard_log(self) -> None:\n"
        "        self.crash_count += 1\n"
        "        nowt = time.time()\n"
        "        if nowt - self.last_crash_log >= 1.0:\n"
        "            self.last_crash_log = nowt\n"
        "            print(f\"engine error on frame #{self.crash_count}"
        " - frame skipped, still watching:\")\n"
        "            traceback.print_exc()\n"
        "\n"
        "    def _poll_once(self, frame) -> None:\n"
        "        \"\"\"One production tick, fault-isolated.\n"
        "\n"
        "        bingo.log records 8 mid-game process deaths from exactly\n"
        "        this class (OCR garbage hitting a parse, one old\n"
        "        NameError): the exception escaped the loop, the app died,\n"
        "        and the user kept playing unprotected during the re-scan.\n"
        "        One bad frame must never kill the watcher: log it, skip\n"
        "        the frame, keep watching.\n"
        "        \"\"\"\n"
        "        try:\n"
        "            self._confirm_pending(frame)\n"
        "            self.check_round(frame)\n"
        "            self.poll_ball(frame)\n"
        "        except Exception:\n"
        "            self._guard_log()\n"
        "\n"
        "    def _safe_detect(self, quiet: bool = False) -> None:\n"
        "        \"\"\"detect() with the same per-call fault isolation.\"\"\"\n"
        "        try:\n"
        "            self.detect(quiet=quiet)\n"
        "        except Exception:\n"
        "            self._guard_log()\n"
        "\n"
        "    def cleanup(self) -> None:\n",
    ),
])
