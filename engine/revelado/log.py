# SPDX-License-Identifier: GPL-3.0-or-later
"""JSON-lines logging with redaction.

Redaction: Anthropic keys (``sk-ant-...``), Authorization headers and the user's home-directory prefix (→ ``~``).
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import re
import sys
import time
from pathlib import Path

_HOME = str(Path.home())
_KEY_RE = re.compile(r"sk-ant-[A-Za-z0-9_\-]{6,}")
_AUTH_RE = re.compile(r"(?i)(authorization|x-api-key)\s*[:=]\s*\S+")


def redact(text: str) -> str:
    text = _KEY_RE.sub("sk-ant-***", text)
    text = _AUTH_RE.sub(r"\1: ***", text)
    if _HOME and len(_HOME) > 1:
        text = text.replace(_HOME, "~")
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created)),
             "level": record.levelname, "logger": record.name, "msg": redact(record.getMessage())}
        for k in ("job_id", "photo_id", "stage"):
            v = getattr(record, k, None)
            if v is not None:
                d[k] = v
        if record.exc_info:
            d["exc"] = redact(self.formatException(record.exc_info))[-4000:]
        return json.dumps(d, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        s = f"{time.strftime('%H:%M:%S', time.localtime(record.created))} {record.levelname[:4]} {record.name}: " \
            f"{record.getMessage()}"
        if record.exc_info:
            s += "\n" + self.formatException(record.exc_info)
        return redact(s)


_configured = False


def setup_logging(log_dir: Path, level: str | None = None) -> None:
    global _configured
    if _configured:
        return
    _configured = True
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("revelado")
    root.setLevel(level or os.environ.get("REVELADO_LOG_LEVEL") or "INFO")
    fh = logging.handlers.TimedRotatingFileHandler(log_dir / "engine.jsonl", when="midnight", backupCount=14,
                                                   encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    root.addHandler(fh)
    ch = logging.StreamHandler(sys.stderr)
    ch.setFormatter(ConsoleFormatter())
    root.addHandler(ch)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"revelado.{name}")
