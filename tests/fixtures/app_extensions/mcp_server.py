"""Read-only stdio MCP for the frozen App acceptance reference bundle."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ResourceNotFoundError, ToolError
from mcp.types import ToolAnnotations

ROOT = Path(__file__).resolve().parent
FILES = {"baseline": ROOT / "data" / "baseline.json",
         "capabilities": ROOT / "data" / "capabilities.json"}
Family = Literal["web", "extensions", "permissions", "agents", "protocol"]
Artifact = Literal["baseline", "capabilities"]
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                            idempotentHint=True, openWorldHint=False)

def read_fixture(artifact: Artifact) -> tuple[bytes, dict]:
    if artifact not in FILES:
        raise ValueError("Unknown fixture artifact")
    path = FILES[artifact]
    if path.is_symlink() or path.parent.is_symlink() or (hasattr(path.parent, "is_junction") and path.parent.is_junction()):
        raise ValueError("Fixture data cannot use links or junctions")
    if path.resolve().parent != (ROOT / "data").resolve() or path.stat().st_nlink != 1:
        raise ValueError("Fixture data must remain inside its own bundle")
    raw = path.read_bytes()
    if len(raw) > 65536:
        raise ValueError("Fixture record exceeds its declared bound")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("fixture_record") is not True:
        raise ValueError("Not an acceptance fixture record")
    return raw, value

mcp = MCPServer(
    "CDAG acceptance inventory", version="1.0.0",
    instructions="Read-only frozen reference inventory. Records identify test inputs, not passed gates or authorization.",
    log_level="WARNING",
)

@mcp.tool(annotations=READ_ONLY, structured_output=True)
def filter_capabilities(family: Family | None = None) -> dict[str, Any]:
    """Filter the fixed native capability inventory by family; return source URLs and the snapshot digest."""
    raw, data = read_fixture("capabilities")
    rows = [item for item in data["capabilities"] if family is None or item["family"] == family]
    return {"fixture_record": True, "family": family, "count": len(rows), "records": rows,
            "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
            "qualification": "This inventory does not prove runtime acceptance."}

@mcp.tool(annotations=READ_ONLY, structured_output=True)
def artifact_digest(artifact: Artifact) -> dict[str, Any]:
    """Compute SHA256 over one of the two allowlisted fixture files; accepts no paths."""
    raw, _ = read_fixture(artifact)
    return {"artifact": artifact, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

@mcp.tool(annotations=READ_ONLY, structured_output=True)
def verify_artifact_digest(artifact: Artifact, expected_sha256: str) -> dict[str, Any]:
    """Compare a caller-supplied 64-digit SHA256 with the actual allowlisted fixture bytes."""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256):
        raise ToolError("Expected SHA256 must contain exactly 64 hexadecimal characters")
    actual = artifact_digest(artifact)
    return {**actual, "matches": actual["sha256"] == expected_sha256.lower()}

@mcp.resource("cdag-acceptance://baseline", mime_type="application/json")
def baseline_resource() -> str:
    """Frozen official App and native baseline, distinguished from the private patched build."""
    return read_fixture("baseline")[0].decode("utf8")

@mcp.resource("cdag-acceptance://capability/{capability_id}", mime_type="application/json")
def capability_resource(capability_id: str) -> str:
    """Resolve one inventory ID; no file-system path is accepted."""
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", capability_id):
        raise ResourceNotFoundError("Invalid capability identifier")
    _, data = read_fixture("capabilities")
    for item in data["capabilities"]:
        if item["id"] == capability_id:
            return json.dumps(item, sort_keys=True)
    raise ResourceNotFoundError("Unknown capability identifier")

if __name__ == "__main__":
    mcp.run(transport="stdio")
