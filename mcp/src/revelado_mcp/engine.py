# SPDX-License-Identifier: GPL-3.0-or-later
"""Thin async HTTP client for the local Revelado engine API (see engine/README.md)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from .config import Config
from .naming import APP_NAME, TOKEN_HEADER, env


class EngineError(ToolError):
    """An error reported by the engine, or the engine being unreachable. Shown to the model as a tool error."""

    def __init__(self, message: str, *, code: str = "E_ENGINE", status: int | None = None,
                 retryable: bool = False) -> None:
        super().__init__(message)
        self.code, self.status, self.retryable = code, status, retryable


def _format_engine_error(status: int, body: Any) -> EngineError:
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        code = str(err.get("code") or "E_ENGINE")
        en = err.get("message_en") or ""
        es = err.get("message_es") or (body.get("detail") if isinstance(body, dict) else "") or ""
        detail = err.get("detail") or ""
        retry = bool(err.get("retryable"))
        parts = [f"[{code}] {en}".strip()]
        if es and es != en:
            parts.append(f"(es: {es})")
        if detail:
            parts.append(f"- {detail}")
        if retry:
            parts.append("(retryable)")
        return EngineError(" ".join(parts), code=code, status=status, retryable=retry)
    detail = body.get("detail") if isinstance(body, dict) else None
    return EngineError(f"[E_ENGINE] engine returned HTTP {status}: {detail or body!s}"[:500], status=status)


class EngineClient:
    def __init__(self, config: Config, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.config = config
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._token: str | None = config.token

    # ---- token
    def _read_token(self) -> str | None:
        if self.config.token:
            return self.config.token
        for p in self.config.token_files:
            try:
                t = Path(p).read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if t:
                return t
        return None

    def _headers(self) -> dict[str, str]:
        if self._token is None:
            self._token = self._read_token()
        return {TOKEN_HEADER: self._token} if self._token else {}

    def _client_(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self.config.engine_url, transport=self._transport,
                                             timeout=self.config.request_timeout_s, follow_redirects=False,
                                             trust_env=False)  # never route the local engine through a proxy
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---- requests
    async def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None) -> httpx.Response:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in (0, 1):
            try:
                r = await self._client_().request(method, path, params=params, json=json, headers=self._headers())
            except httpx.TimeoutException:
                raise EngineError(f"[E_TIMEOUT] the {APP_NAME} engine did not answer in time ({method} {path}).",
                                  code="E_TIMEOUT", retryable=True) from None
            except httpx.TransportError:
                raise EngineError(
                    f"[E_ENGINE_DOWN] cannot reach the {APP_NAME} engine at {self.config.engine_url}. Start the "
                    f"{APP_NAME} app/engine first (or set {env('ENGINE_URL')}).", code="E_ENGINE_DOWN",
                    retryable=True) from None
            if r.status_code == 401 and attempt == 0 and not self.config.token:
                self._token = self._read_token()          # the engine writes a new token on every start
                continue
            break
        if r.status_code == 401:
            raise EngineError(
                f"[E_AUTH] the {APP_NAME} engine rejected the token. Set {env('ENGINE_TOKEN')} or "
                f"{env('TOKEN_FILE')} (the engine writes its token to a '.runtime_token' file on start).",
                code="E_AUTH", status=401)
        if r.status_code >= 400:
            try:
                body = r.json()
            except ValueError:
                body = r.text[:300]
            raise _format_engine_error(r.status_code, body)
        return r

    async def get_json(self, path: str, **params: Any) -> Any:
        return (await self.request("GET", path, params=params)).json()

    async def post_json(self, path: str, body: Any) -> Any:
        return (await self.request("POST", path, json=body)).json()

    async def get_bytes(self, path: str, **params: Any) -> tuple[bytes, httpx.Headers]:
        r = await self.request("GET", path, params=params)
        return r.content, r.headers
