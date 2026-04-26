from __future__ import annotations

from collections import Counter
from typing import Any

import requests

from core.cloud_sync import CloudSyncEngine
from core.config import Settings


class CloudSyncClient:
    def __init__(self, settings: Settings, sync_engine: CloudSyncEngine) -> None:
        self.settings = settings
        self.sync_engine = sync_engine

    def is_enabled(self) -> bool:
        return bool(
            self.settings.cloud_sync_enabled and self.settings.cloud_api_base_url
        )

    def _headers(self, bearer_token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {bearer_token}",
            "Content-Type": "application/json",
        }

    def _token_or_disabled(
        self, bearer_token_override: str | None = None
    ) -> tuple[bool, str, str | None]:
        if not self.is_enabled():
            return False, "", "Cloud sync is not enabled."

        token = (
            bearer_token_override or self.settings.cloud_bearer_token or ""
        ).strip()
        if not token:
            return False, "", "Missing cloud bearer token for sync."

        return True, token, None

    def _raise_for_status_with_detail(self, response: requests.Response) -> None:
        if response.ok:
            return

        detail: str | None = None
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail_value = payload.get("detail")
                if isinstance(detail_value, str) and detail_value.strip():
                    detail = detail_value.strip()
        except Exception:
            detail = None

        if detail is None:
            body = response.text.strip()
            if body:
                detail = body[:240]

        status_line = f"{response.status_code} {response.reason}"
        if detail:
            raise RuntimeError(f"{status_line}: {detail}")

        response.raise_for_status()

    def remote_status(
        self,
        bearer_token_override: str | None = None,
    ) -> dict[str, Any]:
        enabled, token, error = self._token_or_disabled(
            bearer_token_override=bearer_token_override
        )
        if not enabled:
            return {
                "enabled": False,
                "ok": False,
                "error": error,
            }

        base = (self.settings.cloud_api_base_url or "").rstrip("/")
        response = requests.get(
            f"{base}/cloud/sync/status",
            headers=self._headers(token),
            timeout=30,
        )
        self._raise_for_status_with_detail(response)

        payload = response.json()
        if isinstance(payload, dict):
            payload["enabled"] = True
            return payload
        return {"enabled": True, "ok": False, "error": "Invalid cloud status payload."}

    def sync_once(
        self,
        remote_name: str = "default",
        bearer_token_override: str | None = None,
    ) -> dict[str, Any]:
        enabled, token, error = self._token_or_disabled(
            bearer_token_override=bearer_token_override
        )
        if not enabled:
            return {
                "enabled": False,
                "pushed": 0,
                "pulled": 0,
                "applied": 0,
                "skipped": 0,
                "error": error,
            }

        base = (self.settings.cloud_api_base_url or "").rstrip("/")
        checkpoint = self.sync_engine.load_checkpoint(remote_name)
        last_pushed = int(checkpoint["last_pushed_seq"])
        last_pulled = int(checkpoint["last_pulled_seq"])

        export = self.sync_engine.export_changes(since_seq=last_pushed, limit=500)
        local_changes = export["changes"]
        local_last_seq = int(export["last_seq"])
        pushed_by_table: dict[str, int] = dict(
            sorted(
                Counter(
                    str(change.get("table") or "unknown") for change in local_changes
                ).items()
            )
        )

        pushed = 0
        if local_changes:
            push_response = requests.post(
                f"{base}/cloud/sync/push",
                json={
                    "client_id": self.settings.cloud_client_id,
                    "changes": local_changes,
                },
                headers=self._headers(token),
                timeout=30,
            )
            self._raise_for_status_with_detail(push_response)
            pushed = len(local_changes)
            last_pushed = local_last_seq

        pull_response = requests.post(
            f"{base}/cloud/sync/pull",
            json={
                "client_id": self.settings.cloud_client_id,
                "since_seq": last_pulled,
                "limit": 500,
            },
            headers=self._headers(token),
            timeout=30,
        )
        self._raise_for_status_with_detail(pull_response)
        pull_payload = pull_response.json()
        remote_changes = pull_payload.get("changes") or []
        remote_last_seq = int(pull_payload.get("last_seq") or last_pulled)

        apply_result = self.sync_engine.apply_changes(remote_changes)
        last_pulled = remote_last_seq

        self.sync_engine.save_checkpoint(
            remote_name,
            last_pushed_seq=last_pushed,
            last_pulled_seq=last_pulled,
        )

        return {
            "enabled": True,
            "pushed": pushed,
            "pushed_by_table": pushed_by_table,
            "pulled": len(remote_changes),
            "applied": int(apply_result["applied"]),
            "skipped": int(apply_result["skipped"]),
            "last_pushed_seq": last_pushed,
            "last_pulled_seq": last_pulled,
        }
