from __future__ import annotations
import asyncio
import hashlib
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.exceptions import MCPError
import pytest

BUNDLE = Path(__file__).parent / "fixtures" / "app_extensions"

def snapshot():
    return {str(p.relative_to(BUNDLE)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in BUNDLE.rglob("*") if p.is_file() and "__pycache__" not in p.parts}

def test_real_stdio_sdk_discovery_calls_resources_and_boundaries():
    before = snapshot()
    async def scenario():
        parameters = StdioServerParameters(command=sys.executable,
            args=["-I", "-X", "utf8", str(BUNDLE / "mcp_server.py")], env={})
        async with stdio_client(parameters) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=10) as session:
                initialized = await session.initialize()
                assert initialized.server_info.name == "CDAG acceptance inventory"
                listed = await session.list_tools()
                assert {tool.name for tool in listed.tools} == {
                    "filter_capabilities", "artifact_digest", "verify_artifact_digest"}
                assert all(tool.annotations.read_only_hint is True for tool in listed.tools)
                assert all(tool.annotations.destructive_hint is False for tool in listed.tools)
                result = await session.call_tool("filter_capabilities", {"family": "web"})
                assert result.is_error is False
                assert result.structured_content["count"] == 2
                assert {r["id"] for r in result.structured_content["records"]} == {"web-navigation", "web-media"}
                raw = (BUNDLE / "data/capabilities.json").read_bytes()
                assert result.structured_content["snapshot_sha256"] == hashlib.sha256(raw).hexdigest()
                result = await session.call_tool("artifact_digest", {"artifact": "baseline"})
                digest = hashlib.sha256((BUNDLE / "data/baseline.json").read_bytes()).hexdigest()
                assert result.structured_content["sha256"] == digest
                valid = await session.call_tool("verify_artifact_digest", {"artifact": "baseline", "expected_sha256": digest})
                assert valid.structured_content["matches"] is True
                mismatch = await session.call_tool("verify_artifact_digest", {"artifact": "baseline", "expected_sha256": "0" * 64})
                assert mismatch.is_error is False and mismatch.structured_content["matches"] is False
                for name, arguments in [
                    ("filter_capabilities", {"family": "not-a-family"}),
                    ("artifact_digest", {"artifact": "../../config.toml"}),
                    ("verify_artifact_digest", {"artifact": "baseline", "expected_sha256": "bad"}),
                ]:
                    invalid = await session.call_tool(name, arguments)
                    assert invalid.is_error is True
                resources = await session.list_resources()
                assert {str(item.uri) for item in resources.resources} == {"cdag-acceptance://baseline"}
                templates = await session.list_resource_templates()
                assert {item.uri_template for item in templates.resource_templates} == {"cdag-acceptance://capability/{capability_id}"}
                baseline = await session.read_resource("cdag-acceptance://baseline")
                assert json.loads(baseline.contents[0].text)["native_source_commit"] == "680c4ba93ec79c292287fb9e0b627dc7a76e3b5e"
                capability = await session.read_resource("cdag-acceptance://capability/mcp-extensions")
                assert json.loads(capability.contents[0].text)["family"] == "extensions"
                with pytest.raises(MCPError, match="Unknown capability identifier") as missing:
                    await session.read_resource("cdag-acceptance://capability/not-present")
                assert missing.value.code == -32602
                healthy = await session.read_resource("cdag-acceptance://baseline")
                assert json.loads(healthy.contents[0].text)["fixture_record"] is True
    asyncio.run(scenario())
    assert snapshot() == before, "The read-only MCP fixture must not rewrite its data"


def test_plugin_and_marketplace_paths_remain_within_bundle():
    marketplace = json.loads((BUNDLE / ".agents/plugins/marketplace.json").read_text())
    entry = marketplace["plugins"][0]
    assert entry["source"]["source"] == "local"
    root = (BUNDLE / entry["source"]["path"]).resolve()
    assert root.is_relative_to(BUNDLE.resolve())
    manifest = json.loads((root / ".codex-plugin/plugin.json").read_text())
    assert manifest["name"] == entry["name"]
    assert manifest["skills"].startswith("./")
    skill_root = (root / manifest["skills"]).resolve()
    assert skill_root.is_relative_to(root)
    skills = list(skill_root.glob("*/SKILL.md"))
    assert len(skills) == 1
    assert "mcpServers" not in manifest, "This fixture deliberately tests a skills-only plugin"
