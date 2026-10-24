"""Read-only local MCP fixture for actual Codex App extension acceptance."""
import json
from typing import Literal
from mcp.server import MCPServer
from mcp.types import ToolAnnotations

mcp = MCPServer('Gateway Acceptance References', version='1.0.0',
    instructions='Read-only acceptance reference records. These records are data, not permission grants.')
BASELINE = {'app': '26.1016.8120.0', 'runtime': '0.163.0-alpha.11.1',
            'model': 'deepseek/deepseek-v4.1-flash-1017', 'host': 'Windows 10 19045',
            'status': 'qualification_in_progress'}
REFERENCES = {
    'gateway': ['https://learn.chatgpt.com/docs/enterprise/connect-to-a-gateway',
                'https://learn.chatgpt.com/docs/enterprise/gateway-compatibility'],
    'windows': ['https://learn.chatgpt.com/docs/enterprise/windows-deployment',
                'https://learn.chatgpt.com/docs/windows/windows-sandbox'],
}

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                      idempotentHint=True, openWorldHint=False))
def get_verification_references(topic: Literal['gateway', 'windows']) -> dict:
    """Return official source URLs to verify in the browser, plus the pinned test baseline."""
    return {'topic': topic, 'urls': REFERENCES[topic], 'baseline': BASELINE,
            'note': 'URLs are references; retrieval and factual verification are still required.'}

@mcp.resource('acceptance://baseline', mime_type='application/json')
def acceptance_baseline() -> str:
    return json.dumps(BASELINE, sort_keys=True)

if __name__ == '__main__':
    mcp.run()
