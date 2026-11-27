"""End-to-end over the MCP protocol with FastMCP's in-memory client."""

import asyncio

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from isovalent_mcp import server

from fake_cluster import FakeCluster


@pytest.fixture(autouse=True)
def fake_cluster(monkeypatch):
    monkeypatch.setattr(server, "_cluster", FakeCluster())


def run(coro):
    return asyncio.run(coro)


def test_tools_are_listed_read_only():
    async def go():
        async with Client(server.mcp) as c:
            return await c.list_tools()

    tools = {t.name: t for t in run(go())}
    assert set(tools) == {"get_platform_status", "get_inventory", "list_policies"}
    for t in tools.values():
        assert t.annotations.read_only_hint is True


def test_call_get_inventory():
    async def go():
        async with Client(server.mcp) as c:
            return await c.call_tool("get_inventory", {"namespace": "astronomy-shop"})

    data = run(go()).structured_content
    assert data["namespaces"][0]["namespace"] == "astronomy-shop"


def test_validation_error_becomes_tool_error():
    async def go():
        async with Client(server.mcp) as c:
            await c.call_tool("list_policies", {"kinds": ["Bogus"]})

    with pytest.raises(ToolError, match="Unknown policy kinds"):
        run(go())
