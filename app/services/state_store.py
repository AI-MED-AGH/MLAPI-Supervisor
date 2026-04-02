from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, state_file: str) -> None:
        self.state_file = Path(state_file)
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {"models": {}}
        self._load()

    def set_model(self, *, model_id: str, image: str, tag: str) -> None:
        with self._lock:
            models = self._state.setdefault("models", {})
            models[model_id] = {
                "image": image,
                "tag": tag,
            }
            self._persist()

    def get_model(self, model_id: str) -> dict[str, str] | None:
        with self._lock:
            models = self._state.get("models", {})
            entry = models.get(model_id)
            if not isinstance(entry, dict):
                return None
            image = str(entry.get("image", "")).strip() or None
            tag = str(entry.get("tag", "")).strip() or None
            if image is None and tag is None:
                return None
            return {
                "image": image or "",
                "tag": tag or "",
            }

    def get_tag(self, model_id: str) -> str | None:
        model = self.get_model(model_id)
        if model is None:
            return None
        tag = model.get("tag")
        return tag or None

    def delete_model(self, model_id: str) -> None:
        with self._lock:
            models = self._state.setdefault("models", {})
            models.pop(model_id, None)
            self._persist()

    def list_models(self) -> dict[str, dict[str, str]]:
        with self._lock:
            models = self._state.get("models", {})
            normalized: dict[str, dict[str, str]] = {}
            for model_id, value in models.items():
                if not isinstance(value, dict):
                    continue
                normalized[model_id] = {
                    "image": str(value.get("image", "")).strip(),
                    "tag": str(value.get("tag", "")).strip(),
                }
            return normalized

    def _load(self) -> None:
        if not self.state_file.exists():
            return

        try:
            loaded = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        if isinstance(loaded, dict) and isinstance(loaded.get("models"), dict):
            self._state = loaded

    def _persist(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.state_file.with_suffix(f"{self.state_file.suffix}.tmp")
        temp_file.write_text(json.dumps(self._state, indent=2, sort_keys=True), encoding="utf-8")
        temp_file.replace(self.state_file)
