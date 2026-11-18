# SPDX-License-Identifier: GPL-3.0-or-later
"""Revelado engine.

Local photo developer: library + RAW/JPG pairing, diagnostics masks, camera/lens profiles, culling, and a bundled
develop pipeline (RAW develop + noise/CA clean-up, Kodak Ektar 100 film emulation or neutral render, retouch,
framing) run through a queue that executes ONE heavy job at a time.
"""

__version__ = "0.2.0b1"
