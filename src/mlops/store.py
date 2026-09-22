"""File-backed training inbox + model registry (no CMMS required to start)."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.config import settings
from src.schemas.training import ComponentLifeEvent, LabeledWindow, RulTrajectory

LIFE_FILE = "component_life_events.jsonl"
LABEL_FILE = "labeled_windows.jsonl"
TRAJ_FILE = "rul_trajectories.jsonl"


def _root() -> Path:
    configured = Path(settings.TRAINING_DATA_DIR)
    if configured.is_absolute():
        return configured
    return Path(__file__).resolve().parents[2] / configured


def inbox_dir() -> Path:
    path = _root() / "inbox"
    path.mkdir(parents=True, exist_ok=True)
    return path


def registry_dir() -> Path:
    path = _root() / "registry"
    path.mkdir(parents=True, exist_ok=True)
    return path


def templates_dir() -> Path:
    return _root() / "templates"


def _append_jsonl(name: str, payload: Dict[str, Any]) -> None:
    path = inbox_dir() / name
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, default=str) + "\n")


def _read_jsonl(name: str) -> List[Dict[str, Any]]:
    path = inbox_dir() / name
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def add_life_event(event: ComponentLifeEvent) -> None:
    _append_jsonl(LIFE_FILE, event.model_dump(by_alias=True, mode="json"))


def add_labeled_window(window: LabeledWindow) -> None:
    _append_jsonl(LABEL_FILE, window.model_dump(by_alias=True, mode="json"))


def add_trajectory(traj: RulTrajectory) -> None:
    _append_jsonl(TRAJ_FILE, traj.model_dump(by_alias=True, mode="json"))


def load_life_events(asset_class: Optional[str] = None) -> List[ComponentLifeEvent]:
    events = [ComponentLifeEvent(**row) for row in _read_jsonl(LIFE_FILE)]
    if asset_class:
        events = [e for e in events if e.asset_class == asset_class]
    return [e for e in events if not e.machine_id.upper().startswith("EXAMPLE")]


def load_labeled_windows() -> List[LabeledWindow]:
    windows = [LabeledWindow(**row) for row in _read_jsonl(LABEL_FILE)]
    return [w for w in windows if not w.machine_id.upper().startswith("EXAMPLE")]


def load_trajectories(asset_class: Optional[str] = None) -> List[RulTrajectory]:
    trajs = [RulTrajectory(**row) for row in _read_jsonl(TRAJ_FILE)]
    if asset_class:
        trajs = [t for t in trajs if t.asset_class == asset_class]
    return [t for t in trajs if not t.machine_id.upper().startswith("EXAMPLE")]


def save_registry_json(name: str, payload: Dict[str, Any]) -> Path:
    path = registry_dir() / name
    payload = {**payload, "written_at": datetime.now(timezone.utc).isoformat()}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_registry_json(name: str) -> Optional[Dict[str, Any]]:
    path = registry_dir() / name
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def registry_path(name: str) -> Path:
    return registry_dir() / name


def import_life_events_csv(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(_skip_comments(fh))
        for row in reader:
            if not row or not row.get("machine_id") and not row.get("machineId"):
                continue
            event = ComponentLifeEvent(
                machineId=row.get("machineId") or row.get("machine_id"),
                component=row["component"],
                assetClass=row.get("assetClass") or row.get("asset_class") or "SKF-6208",
                hoursAtInstall=float(row.get("hoursAtInstall") or row.get("hours_at_install") or 0),
                hoursAtEvent=float(row.get("hoursAtEvent") or row.get("hours_at_event")),
                eventType=row.get("eventType") or row.get("event_type"),
                eventDate=row.get("eventDate") or row.get("event_date"),
                workOrderId=row.get("workOrderId") or row.get("work_order_id"),
                notes=row.get("notes"),
            )
            if event.machine_id.upper().startswith("EXAMPLE"):
                continue
            add_life_event(event)
            count += 1
    return count


def import_labeled_windows_csv(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(_skip_comments(fh))
        for row in reader:
            mid = row.get("machineId") or row.get("machine_id")
            if not mid:
                continue
            features = {}
            numeric_keys = {
                "imuAcceleration", "emVoltageImbalance", "emMachineLoad", "tempMotor", "tempAmbient",
            }
            for key in (
                "imuAcceleration", "emVoltageImbalance", "emMachineLoad", "tempMotor",
                "tempAmbient", "pattern", "isolatedDomain", "matchedFault",
            ):
                if row.get(key):
                    val = row[key]
                    if key in numeric_keys:
                        try:
                            val = float(val)
                        except ValueError:
                            pass
                    features[key] = val
            window = LabeledWindow(
                machineId=mid,
                timestamp=row.get("timestamp") or datetime.now(timezone.utc).isoformat(),
                label=row["label"],
                source=row.get("source") or "wo",
                features=features,
                notes=row.get("notes"),
            )
            if window.machine_id.upper().startswith("EXAMPLE"):
                continue
            add_labeled_window(window)
            count += 1
    return count


def import_trajectories_csv(path: Path) -> int:
    """CSV with one point per row, grouped by trajectory_id."""
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    meta: Dict[str, Dict[str, str]] = {}
    with path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(_skip_comments(fh))
        for row in reader:
            tid = row.get("trajectoryId") or row.get("trajectory_id")
            mid = row.get("machineId") or row.get("machine_id")
            if not tid or not mid:
                continue
            if mid.upper().startswith("EXAMPLE"):
                continue
            grouped.setdefault(tid, []).append(row)
            meta[tid] = {
                "machine_id": mid,
                "component": row.get("component") or "DE_bearing",
                "asset_class": row.get("assetClass") or row.get("asset_class") or "SKF-6208",
            }
    count = 0
    for tid, rows in grouped.items():
        points = []
        for row in rows:
            points.append({
                "runHours": float(row.get("runHours") or row.get("run_hours")),
                "imuAcceleration": float(row.get("imuAcceleration") or row.get("imu_acceleration")),
                "tempCompressor": float(row.get("tempCompressor") or row.get("temp_compressor")),
                "emMachineLoad": float(row.get("emMachineLoad") or row.get("em_machine_load") or 70),
                "rpm": float(row.get("rpm") or 1480),
                "trueRulHours": float(row.get("trueRulHours") or row.get("true_rul_hours")),
            })
        add_trajectory(RulTrajectory(
            trajectoryId=tid,
            machineId=meta[tid]["machine_id"],
            component=meta[tid]["component"],
            assetClass=meta[tid]["asset_class"],
            points=points,
        ))
        count += 1
    return count


def _skip_comments(fh: Iterable[str]) -> Iterable[str]:
    for line in fh:
        if line.lstrip().startswith("#"):
            continue
        yield line
