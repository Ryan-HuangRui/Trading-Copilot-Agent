#!/usr/bin/env python3
"""Explicit-user lark-cli adapter for earnings documents (never messages)."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any
import re

from earnings_common import ROOT, atomic_write_json, ensure_inside, read_json, sha256_file, utc_now


class LarkAuthenticationError(RuntimeError):
    pass


class LarkReadError(RuntimeError):
    pass


def publication_delivery_route_key(config: dict[str, Any]) -> str:
    """Return the durable route identity without initializing lark-cli or credentials."""
    if config.get("enabled") is not True:
        raise ValueError("lark document publishing is disabled")
    if not config.get("profile") or not config.get("user_route") or config.get("as") != "user" \
            or not any(config.get(key) for key in ("parent_token", "parent_position", "folder_token")):
        raise ValueError("explicit lark profile, user_route, --as user and parent target are required")
    route = {key: config.get(key) for key in
             ("profile", "user_route", "parent_token", "parent_position", "folder_token")}
    return hashlib.sha256(json.dumps(route, sort_keys=True).encode()).hexdigest()


def _content(payload: dict[str, Any]) -> str | None:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    for key in ("content", "markdown", "text"):
        if isinstance(data.get(key), str): return data[key]
    document = data.get("document")
    if isinstance(document, dict):
        for key in ("content", "markdown", "text"):
            if isinstance(document.get(key), str): return document[key]
    return None


def _identity(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    document = data.get("document") if isinstance(data.get("document"), dict) else data
    doc_id = document.get("document_id") or document.get("doc_token") or document.get("token")
    url = document.get("url") or document.get("document_url")
    return doc_id, url


def normalize_markdown_for_lark(text: str) -> str:
    """Expand reference links and remove links that only make sense on the NAS filesystem."""
    definitions = {key.lower(): url for key, url in re.findall(r"^\[([^\]]+)\]:\s*(https?://\S+)\s*$", text, re.MULTILINE)}
    text = re.sub(r"^\[[^\]]+\]:\s*https?://\S+\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)\]\[([^\]]+)\]", lambda m: f"[{m.group(1)}]({definitions[m.group(2).lower()]})" if m.group(2).lower() in definitions else m.group(1), text)
    text = re.sub(r"!?\[([^\]]*)\]\((?!https?://)(?:#[^)]+|[^)]+)\)", lambda m: m.group(1), text)
    return text.strip() + "\n"


def _readback_key(text: str) -> str:
    """Normalize only known lark Markdown export representation changes."""
    text = text or ""
    text = re.sub(r"\A\s*<title>[^\r\n<]*</title>\s*", "", text, count=1)
    text = re.sub(r"\\([\[\]|])", r"\1", text)
    lines = []
    for line in text.splitlines():
        if re.match(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$", line):
            count = len(line.strip().strip("|").split("|"))
            lines.append("|" + "|".join("-" for _ in range(count)) + "|")
        else:
            lines.append(line)
    return "".join("\n".join(lines).split())


class LarkDocumentPublisher:
    def __init__(self, root: Path, config: dict[str, Any], *, timeout: int = 120):
        self.root = root.resolve(); self.config = config; self.timeout = timeout
        binary = Path(str(config.get("lark_cli_bin", "")))
        if config.get("enabled") is not True: raise ValueError("lark document publishing is disabled")
        if not binary.is_absolute() or not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError("absolute executable lark_cli_bin is required")
        publication_delivery_route_key(config)
        self.binary = binary
        self.base = [str(binary), "--profile", str(config["profile"]), "--as", "user"]
        self.route = {key: config.get(key) for key in ("profile", "user_route", "parent_token", "parent_position", "folder_token")}

    def _run(self, action: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
        result = subprocess.run([*self.base, *action, "--format", "json"], capture_output=True, text=True,
                                timeout=self.timeout, check=False, cwd=str(cwd) if cwd else None)
        if result.returncode:
            diagnostic = (result.stdout + " " + result.stderr).lower()
            if any(token in diagnostic for token in ("auth", "login", "unauthorized", "permission")):
                raise LarkAuthenticationError("lark user authentication or permission is unavailable")
            if "+fetch" in action:
                raise LarkReadError(f"lark-cli readback returned exit {result.returncode}")
            raise RuntimeError(f"lark-cli returned exit {result.returncode}")
        try: payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc: raise RuntimeError("lark-cli returned invalid JSON") from exc
        if payload.get("ok") is not True: raise RuntimeError("lark-cli operation was not accepted")
        if payload.get("identity") != "user": raise LarkAuthenticationError("lark-cli did not confirm explicit user identity")
        return payload

    def _state_path(self, document_key: str) -> Path:
        safe = hashlib.sha256(json.dumps({"document_key": document_key, "route": self.route}, sort_keys=True).encode()).hexdigest()
        return self.root / "runtime/earnings/publications/cloud" / safe / "state.json"

    @contextmanager
    def _lock(self, state_path: Path):
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with state_path.with_name("delivery.lock").open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _verify_manifest(self, path: Path | None, publication_id: str, body: Path, expected_sha256: str) -> dict[str, Any]:
        if path is None: raise ValueError("checked publication manifest is required")
        manifest_path = ensure_inside(path.resolve(), [self.root / "report/earnings/publications"])
        manifest = read_json(manifest_path)
        artifact = manifest.get("artifacts", {}).get("markdown", {})
        if manifest.get("publication_id") != publication_id or manifest.get("publishable") is not True:
            raise ValueError("publication is not accepted for cloud delivery")
        if manifest.get("checker", {}).get("status") != "passed" or manifest.get("checker", {}).get("errors"):
            raise ValueError("publication checker acceptance is invalid")
        if artifact.get("sha256") != expected_sha256 or (self.root / artifact.get("path", "")).resolve() != body:
            raise ValueError("publication manifest artifact binding mismatch")
        if any(sha256_file(self.root / source["path"]) != source["sha256"] for source in manifest.get("sources", [])):
            raise ValueError("publication source changed after acceptance")
        return manifest

    def _mirror(self, state: dict[str, Any]) -> None:
        state_db = self.root / "runtime/earnings/state.sqlite"
        if not state_db.exists(): return
        from earnings_state import EarningsState
        ledger = EarningsState(state_db)
        try:
            route_key = publication_delivery_route_key(self.config)
            ledger.db.execute("""INSERT INTO publication_delivery_routes VALUES(?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(route_key,series_id) DO UPDATE SET publication_id=excluded.publication_id,state=excluded.state,
              document_id=excluded.document_id,url=excluded.url,local_sha256=excluded.local_sha256,
              verified_remote_sha256=excluded.verified_remote_sha256,attempts=excluded.attempts,
              reason=excluded.reason,updated_at=excluded.updated_at""",
              (route_key, state.get("series_id") or state["publication_id"], state["publication_id"], state["state"],
               state.get("document_id"), state.get("url"), state.get("local_sha256"), state.get("verified_remote_sha256"),
               state.get("attempts", 0), state.get("reason"), state.get("updated_at") or utc_now()))
            ledger.db.execute("""INSERT INTO publication_delivery VALUES(?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(series_id) DO UPDATE SET publication_id=excluded.publication_id,state=excluded.state,
              document_id=excluded.document_id,url=excluded.url,local_sha256=excluded.local_sha256,
              verified_remote_sha256=excluded.verified_remote_sha256,attempts=excluded.attempts,
              reason=excluded.reason,updated_at=excluded.updated_at""",
              (state.get("series_id") or state["publication_id"], state["publication_id"], state["state"], state.get("document_id"),
               state.get("url"), state.get("local_sha256"), state.get("verified_remote_sha256"), state.get("attempts", 0),
               state.get("reason"), state.get("updated_at") or utc_now()))
        finally:
            ledger.close()

    def publish(self, publication_id: str, title: str, markdown_path: Path, *, expected_sha256: str,
                series_id: str | None = None, publication_manifest: Path | None = None) -> dict[str, Any]:
        body = ensure_inside(markdown_path.resolve(), [self.root / "report/earnings", self.root / "runtime/earnings"])
        if sha256_file(body) != expected_sha256: raise ValueError("publication body hash mismatch")
        manifest = self._verify_manifest(publication_manifest, publication_id, body, expected_sha256)
        state_path = self._state_path(series_id or publication_id); state_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock(state_path):
            return self._publish_locked(publication_id, title, body, expected_sha256=expected_sha256,
                                        series_id=series_id, state_path=state_path, manifest=manifest)

    def _publish_locked(self, publication_id: str, title: str, body: Path, *, expected_sha256: str,
                        series_id: str | None, state_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
        prepared = state_path.with_name("prepared.md")
        prepared.write_text(normalize_markdown_for_lark(body.read_text(encoding="utf-8")), encoding="utf-8")
        state = read_json(state_path) if state_path.exists() else {"schema_version": 1, "publication_id": publication_id,
            "state": "pending", "attempts": 0, "document_id": None, "url": None, "local_sha256": None,
            "verified_remote_sha256": None}
        if state["state"] in {"creating", "updating"}:
            state.update(state="unknown", reason="orphaned in-flight operation requires reconciliation", updated_at=utc_now())
            atomic_write_json(state_path, state); self._mirror(state); return state
        if state["state"] == "auth_failed" and self.config.get("credentials_refreshed_at", "") > state.get("updated_at", ""):
            state.update(state="pending", reason=None, updated_at=utc_now())
            atomic_write_json(state_path, state)
        if state["state"] in {"unknown", "conflict", "auth_failed"}:
            return state
        if state["state"] == "verified" and state.get("local_sha256") == expected_sha256:
            return state
        creating = not state.get("document_id")
        try:
            if not creating and state.get("state") in {"written", "readback_failed"} and state.get("local_sha256") == expected_sha256:
                fetched = self._run(["docs", "+fetch", "--doc", state["document_id"], "--doc-format", "markdown"])
                remote = _content(fetched); remote_hash = hashlib.sha256((remote or "").encode()).hexdigest()
                if remote is not None and _readback_key(remote) == _readback_key(prepared.read_text(encoding="utf-8")):
                    state.update(state="verified", verified_remote_sha256=remote_hash, verified_at=utc_now(), reason=None, updated_at=utc_now())
                    atomic_write_json(state_path, state); self._mirror(state); return state
                state.update(state="readback_failed", reason="remote content differs from local publication", remote_sha256=remote_hash,
                             updated_at=utc_now()); atomic_write_json(state_path, state); self._mirror(state); return state
            if not creating:
                fetched = self._run(["docs", "+fetch", "--doc", state["document_id"], "--doc-format", "markdown"])
                remote = _content(fetched)
                if remote is None: raise RuntimeError("lark fetch omitted document content")
                remote_hash = hashlib.sha256(remote.encode()).hexdigest()
                if state.get("verified_remote_sha256") and remote_hash != state["verified_remote_sha256"]:
                    state.update(state="conflict", reason="remote content changed after last verified sync", remote_sha256=remote_hash, updated_at=utc_now())
                    atomic_write_json(state_path, state); self._mirror(state); return state
            state.update(state="creating" if creating else "updating", attempts=state["attempts"] + 1,
                         local_sha256=expected_sha256, started_at=utc_now(), publication_id=publication_id,
                         series_id=series_id, version=manifest.get("version"), route=self.route,
                         operation_id=hashlib.sha256(f"{publication_id}:{expected_sha256}:{state['attempts'] + 1}".encode()).hexdigest())
            atomic_write_json(state_path, state)
            if creating:
                parent = (["--parent-token", str(self.config.get("parent_token") or self.config["folder_token"])]
                          if self.config.get("parent_token") or self.config.get("folder_token") else
                          ["--parent-position", str(self.config["parent_position"])])
                payload = self._run(["docs", "+create", "--doc-format", "markdown", "--title", title,
                                     "--content", "@./prepared.md", *parent], cwd=state_path.parent)
                document_id, url = _identity(payload)
                if not document_id or not url: raise RuntimeError("lark create omitted document identity or accessible URL")
                state.update(document_id=document_id, url=url)
            else:
                self._run(["docs", "+update", "--doc", state["document_id"], "--command", "overwrite",
                           "--doc-format", "markdown", "--content", "@./prepared.md"], cwd=state_path.parent)
            state.update(state="written", publication_id=publication_id, series_id=series_id, written_at=utc_now()); atomic_write_json(state_path, state)
            fetched = self._run(["docs", "+fetch", "--doc", state["document_id"], "--doc-format", "markdown"])
            remote = _content(fetched)
            if remote is None: raise RuntimeError("lark readback omitted document content")
            remote_hash = hashlib.sha256(remote.encode()).hexdigest()
            if not state.get("url"):
                state.update(state="readback_failed", reason="verified document has no accessible URL", remote_sha256=remote_hash)
            elif _readback_key(remote) != _readback_key(prepared.read_text(encoding="utf-8")):
                state.update(state="readback_failed", reason="remote content differs from local publication", remote_sha256=remote_hash)
            else:
                state.update(state="verified", verified_remote_sha256=remote_hash, verified_at=utc_now(), reason=None)
        except subprocess.TimeoutExpired:
            state.update(state="unknown", reason="lark-cli timed out after operation start; reconcile before retry")
        except OSError as exc:
            state.update(state="retryable_failed", reason=f"lark-cli did not start: {type(exc).__name__}")
        except LarkAuthenticationError as exc:
            state.update(state="auth_failed", reason=str(exc))
        except LarkReadError as exc:
            state.update(state="readback_failed" if state.get("document_id") else "retryable_failed", reason=str(exc))
        except RuntimeError as exc:
            reason = str(exc)
            state.update(state="unknown", reason=reason)
        state["updated_at"] = utc_now(); atomic_write_json(state_path, state); self._mirror(state)
        return state

    def reconcile(self, publication_id: str, markdown_path: Path, *, expected_sha256: str,
                  document_id: str, url: str | None = None, series_id: str | None = None,
                  publication_manifest: Path | None = None) -> dict[str, Any]:
        """Resolve an ambiguous create/readback using an operator-confirmed document identity; never writes."""
        body = ensure_inside(markdown_path.resolve(), [self.root / "report/earnings", self.root / "runtime/earnings"])
        if sha256_file(body) != expected_sha256 or not document_id or not url:
            raise ValueError("reconciliation requires exact body, document id and accessible URL")
        manifest = self._verify_manifest(publication_manifest, publication_id, body, expected_sha256)
        state_path = self._state_path(series_id or publication_id)
        with self._lock(state_path):
            return self._reconcile_locked(publication_id, body, expected_sha256=expected_sha256, document_id=document_id,
                                          url=url, series_id=series_id, manifest=manifest, state_path=state_path)

    def _reconcile_locked(self, publication_id: str, body: Path, *, expected_sha256: str,
                          document_id: str, url: str | None, series_id: str | None,
                          manifest: dict[str, Any], state_path: Path) -> dict[str, Any]:
        if not state_path.exists(): raise ValueError("no publication delivery state to reconcile")
        state = read_json(state_path)
        if state.get("state") not in {"unknown", "readback_failed", "written"}: raise ValueError("delivery state does not require reconciliation")
        if state.get("publication_id") != publication_id or state.get("version") != manifest.get("version") or state.get("route") != self.route:
            raise ValueError("reconciliation publication/version/route mismatch")
        fetched = self._run(["docs", "+fetch", "--doc", document_id, "--doc-format", "markdown"])
        remote = _content(fetched)
        expected = normalize_markdown_for_lark(body.read_text(encoding="utf-8"))
        remote_hash = hashlib.sha256((remote or "").encode()).hexdigest()
        if remote is not None and _readback_key(remote) == _readback_key(expected):
            state.update(state="verified", document_id=document_id, url=url or state.get("url"), publication_id=publication_id,
                         series_id=series_id, local_sha256=expected_sha256, verified_remote_sha256=remote_hash,
                         verified_at=utc_now(), reason=None)
        else:
            state.update(state="conflict", document_id=document_id, url=url or state.get("url"), remote_sha256=remote_hash,
                         reason="operator-selected document differs from checked publication")
        state["updated_at"] = utc_now(); atomic_write_json(state_path, state); self._mirror(state); return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(ROOT)); parser.add_argument("--deployment", default="runtime/earnings/deployment.json")
    parser.add_argument("--publication-manifest", required=True); parser.add_argument("--execute", action="store_true")
    parser.add_argument("--reconcile-document-id"); parser.add_argument("--reconcile-url")
    args = parser.parse_args(); root = Path(args.repo_root).resolve()
    try:
        deployment = read_json(root / args.deployment); manifest_path = ensure_inside(root / args.publication_manifest, [root / "report/earnings/publications"])
        manifest = read_json(manifest_path); artifact = manifest["artifacts"]["markdown"]
        if not args.execute:
            result = {"status": "skipped", "state": "preview", "publication_id": manifest["publication_id"]}
        else:
            publisher = LarkDocumentPublisher(root, deployment["lark_documents"])
            if args.reconcile_document_id:
                state = publisher.reconcile(manifest["publication_id"], root / artifact["path"], expected_sha256=artifact["sha256"],
                    document_id=args.reconcile_document_id, url=args.reconcile_url, series_id=manifest.get("series_id"),
                    publication_manifest=manifest_path)
            else:
                state = publisher.publish(manifest["publication_id"], manifest["title"], root / artifact["path"],
                    expected_sha256=artifact["sha256"], series_id=manifest.get("series_id"), publication_manifest=manifest_path)
            result = {"status": "success" if state["state"] == "verified" else "failed", **state}
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        result = {"status": "failed", "reason": str(exc)}
    print(json.dumps({"workflow": "earnings-lark-document", **result}, ensure_ascii=False))
    raise SystemExit(result["status"] == "failed")


if __name__ == "__main__":
    main()
