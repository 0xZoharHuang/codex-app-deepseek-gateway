from pathlib import Path
import asyncio
import copy
import hashlib
import importlib.util
import json
import sys
import time
import uuid
import argparse
import aiohttp
from aiohttp import web

ROOT = Path(__file__).parent
p=argparse.ArgumentParser(description="Real loopback HTTP mock-only observer wiring; no App/model/provider.")
p.add_argument("--inputs",type=Path,required=True)
p.add_argument("--inputs-sha256",required=True)
p.add_argument("--output-dir",type=Path,required=True)
args=p.parse_args()
assert hashlib.sha256(args.inputs.read_bytes()).hexdigest()==args.inputs_sha256
inputs=json.loads(args.inputs.read_bytes())
assert inputs["limits"]=={"lifetime_seconds":900,"processed_cap":64,"event_cap":256,"live_cap":8}
for rel,wanted in inputs["modules"].items():
 path=Path(rel);assert not path.is_absolute() and ".."not in path.parts
 assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==wanted
OUT=args.output_dir;OUT.mkdir(parents=True,exist_ok=False)
MODEL = "deepseek/deepseek-v4.1-flash-1017"
THREAD = "local-observer-http-only"
TOKEN = "local-test-bearer-not-real"
UPSTREAM = "local-test-upstream-not-real"
# Deliberately synthetic custom declaration; never an exported native/App tool directory.
TOOLS = [{"type":"custom","name":"exec","description":"Synthetic mock only. ALL_TOOLS is a literal QA marker; no tool execution.","format":{"type":"grammar","syntax":"lark","definition":"start: /[\\s\\S]+/"}}]

def digest(value):
    return hashlib.sha256(value).hexdigest()

def sha(path):
    return digest(Path(path).read_bytes())

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result

class Harness:
    def __init__(self):
        self.fixture = {"gateway_token": TOKEN, "upstream_token": UPSTREAM, "nonce": "pure-local-http-integration"}
        self.deadline = time.time() + 60

    async def upstream(self, request):
        raise AssertionError("This metadata-only test does not invoke a real fixture/model upstream")

async def listen(app):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, "http://127.0.0.1:" + str(port)

async def exercise(label, observer_dir, expect_index):
    evidence = OUT / ("http-" + label)
    evidence.mkdir(exist_ok=False)
    role = module(label + "_role", observer_dir / "native_role_observer.py")
    v4 = module(label + "_v4", observer_dir / "request_observer_v4.py")
    proof = module(label + "_proof", ROOT / "proof_contract.py")
    texts = ["wire-baseline", "wire-slow", "wire-fast", "wire-fail"]
    profiles = [{
        "profile_id": "a21-unstructured-codemode-main-v1",
        "requested_model": MODEL,
        "normalized_prompt_sha256": digest(text.encode()),
        "prompt_mode": "last_text_block",
        "prompt_source": "actual_gui_submission",
        "permitted_purpose_tags": {},
    } for text in texts]
    profile_file = evidence / "profiles.json"
    profile_file.write_bytes(json.dumps(profiles).encode())
    harness = Harness()
    observer = role.NativeRoleObserver(harness, evidence / "roles", profile_file)
    started, release = asyncio.Event(), asyncio.Event()
    upstream_requests = []

    async def upstream(request):
        raw = await request.read()
        payload = json.loads(raw)
        text = payload["input"][-1]["content"][0]["text"]
        assert request.headers["Authorization"] == "Bearer " + UPSTREAM
        assert request.headers.get("X-CDAG-QA-Role-Request-ID")
        upstream_requests.append({"text_sha256": digest(text.encode()), "body_sha256": digest(raw)})
        if text == "wire-slow":
            started.set()
            await release.wait()
        status = 503 if text == "wire-fail" else 200
        return web.json_response({"fixture": "local-http-observation-only", "body_sha256": digest(raw)}, status=status)

    backend = web.Application()
    backend.router.add_post("/sink", upstream)
    backend_runner, backend_url = await listen(backend)
    app = web.Application()

    async def client_context(application):
        application["only_http_client"] = aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=10))
        try:
            yield
        finally:
            await application["only_http_client"].close()

    app.cleanup_ctx.append(client_context)
    observer.attach(app)
    v4.attach_observer(app, expected_bearer=TOKEN, output_dir=evidence / "captures")

    async def handler(request):
        if request.headers.get("Authorization") != "Bearer " + TOKEN:
            return web.json_response({"error": "unauthorized"}, status=401)
        raw = await request.read()
        headers = {"Authorization": "Bearer " + UPSTREAM, "Content-Type": "application/json"}
        async with app["only_http_client"].post(backend_url + "/sink", data=raw, headers=headers) as response:
            output = await response.read()
            return web.Response(status=response.status, body=output, content_type="application/json",
                                headers={"X-Gateway-Request-ID": str(uuid.uuid4())})

    app.router.add_post("/v1/responses", handler)
    app_runner, url = await listen(app)
    seen = {}

    async def send(client, text, token=TOKEN):
        payload = {"model": MODEL, "stream": True, "input": [{"role": "user", "content": [
            {"type": "input_text", "text": text}]}], "tools": copy.deepcopy(TOOLS)}
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        headers = {"Authorization": "Bearer " + token, "thread-id": THREAD, "Content-Type": "application/json"}
        async with client.post(url + "/v1/responses", data=raw, headers=headers) as response:
            result = await response.json()
            assert result.get("body_sha256", digest(raw)) == digest(raw)
            return {"status": response.status, "body_sha256": digest(raw),
                    "request_id": response.headers.get("X-Gateway-Request-ID")}

    try:
        async with aiohttp.ClientSession(trust_env=False, timeout=aiohttp.ClientTimeout(total=15)) as client:
            seen[texts[0]] = await send(client, texts[0])
            slow = asyncio.create_task(send(client, texts[1]))
            await asyncio.wait_for(started.wait(), 5)
            seen[texts[2]] = await send(client, texts[2])
            assert not slow.done()
            release.set()
            seen[texts[1]] = await slow
            seen[texts[3]] = await send(client, texts[3])
            rejected = await send(client, "not-authenticated", "wrong-local-token")
            assert rejected["status"] == 401

        capture = [json.loads(path.read_bytes()) for path in sorted((evidence / "captures").glob("request-*.json"))]
        roles = [json.loads(path.read_bytes()) for path in sorted((evidence / "roles").glob("request-*.json"))]
        assert len(capture) == len(roles) == len(upstream_requests) == 4
        assert all(row["native_role"] == "MAIN" for row in roles)
        results = []
        for text, fact in seen.items():
            a = [row for row in capture if row["raw_body_sha256"] == fact["body_sha256"]]
            b = [row for row in roles if row["raw_body_sha256"] == fact["body_sha256"]]
            assert len(a) == len(b) == 1
            native, classified = a[0], b[0]
            assert native["response_ids"]["x-gateway-request-id"] == classified["acceptedGatewayRequestID"] == fact["request_id"]
            assert native["http_status"] == classified["http_status"] == fact["status"]
            arguments = dict(root=THREAD, prompt=text, model=MODEL, status=fact["status"], native_prompt_text=text)
            if expect_index:
                assert type(native["physical_request_index"]) is int and native["physical_request_index"] > 0
                assert native["physical_request_index"] == classified["physical_request_index"]
                assert proof.join_main(native, classified, **arguments) == fact["request_id"]
                broken = copy.deepcopy(classified)
                broken["physical_request_index"] = None
                try:
                    proof.join_main(native, broken, **arguments)
                except ValueError:
                    pass
                else:
                    raise AssertionError("None cannot substitute for a real physical counter")
            else:
                assert native["physical_request_index"] is None and classified["physical_request_index"] is None
                try:
                    proof.join_main(native, classified, **arguments)
                except ValueError as error:
                    assert "Physical attempt" in str(error)
                else:
                    raise AssertionError("Legacy missing field must fail the strict validator")
            results.append({"text_sha256": digest(text.encode()), "status": fact["status"], "index": native["physical_request_index"]})
        native_coverage = json.loads((evidence / "captures/coverage.json").read_bytes())
        role_coverage = json.loads((evidence / "roles/coverage.json").read_bytes())
        assert native_coverage["coverage"] == "complete_so_far" and role_coverage["status"] == "complete_so_far"
        return {"variant": label, "requests": 4, "concurrent_slow_fast_observed": True, "HTTP503_observed": True,
                "unauthorized401_not_captured_or_forwarded": True, "legacy_missing_index_reproduced": not expect_index,
                "metadata_rows": results}
    finally:
        release.set()
        await app_runner.cleanup()
        await backend_runner.cleanup()

async def main():
    files = list((ROOT / "observers").glob("*.py"))
    before = {str(path): sha(path) for path in files}
    new = await exercise("wired", ROOT / "observers", True)
    assert before == {str(path): sha(path) for path in files}
    result = {"schema": "real-local-HTTP-observer-index-wiring-integration/v1", "at": time.time(),
              "results": [new], "source_before_after_unchanged": True,
              "no_model_no_native_App_no_credential_no_case_init": True,
              "scope": "Real loopback HTTP and actual Role/v4 middleware, with plain mock response bodies. Metadata wiring only, not Responses or agent execution."}
    path = OUT / "http-integration-result.json"
    path.write_bytes((json.dumps(result, indent=2) + "\n").encode())
    print(json.dumps({"status": "passed", "result": str(path), "sha256": sha(path), "real_OpenRouter_requests": 0}))

if __name__ == "__main__":
    asyncio.run(main())
