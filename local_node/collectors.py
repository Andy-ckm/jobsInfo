"""Concrete local executor entrypoints. All actions are read-only."""
from __future__ import annotations
from pathlib import Path
from typing import Any

def collect_boss(config:dict[str,Any],out:Path)->dict[str,Any]:
 from validation.source_control_plane import collect_boss as implementation
 return implementation(config,out)

def collect_liepin(config:dict[str,Any],out:Path)->dict[str,Any]:
 from validation.source_control_plane import collect_liepin as implementation
 return implementation(config,out)

def collect_generic_browser_export(config:dict[str,Any],out:Path|None=None)->dict[str,Any]:
 from validation.source_runtime import drive_snapshot_ingest
 return drive_snapshot_ingest(config,out)
