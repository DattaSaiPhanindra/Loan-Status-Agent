"""Simple JSON file storage for capability artifacts."""

import json
from pathlib import Path

from .schema import CapabilityArtifact


def save_artifact(artifact: CapabilityArtifact, directory: Path) -> Path:
    """Save artifact as JSON. Returns the file path."""
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{artifact.artifact_id}.json"
    path = directory / filename
    path.write_text(artifact.model_dump_json(indent=2))
    return path


def load_artifact(path: Path) -> CapabilityArtifact:
    """Load artifact from JSON file."""
    data = json.loads(path.read_text())
    return CapabilityArtifact.model_validate(data)


def list_artifacts(directory: Path) -> list[CapabilityArtifact]:
    """List all artifacts in a directory."""
    artifacts = []
    if directory.exists():
        for f in sorted(directory.glob("*.json")):
            try:
                artifacts.append(load_artifact(f))
            except Exception:
                continue
    return artifacts
