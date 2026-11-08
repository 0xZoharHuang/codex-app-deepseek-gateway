"""Exercise the shared aggregate planner through CADSG's actual HTTP bridge."""
import json

from aiohttp import web
import pytest

from codex_app_deepseek import bridge
from codex_app_deepseek.image_transport import FileMetadata, MAX_REFERENCED_FILE_BYTES
from codex_deepseek import image_selection
from test_bridge import HEADERS, KEY, ImageFiles, harness, image_factory, image_input, terminal


def existing_image_request(file_id, count=1):
    return image_input(input=[{'role': 'user', 'content': [
        {'type': 'input_image', 'detail': 'original', 'file_id': file_id}
        for _ in range(count)
    ]}])


def inject_internal_reference(monkeypatch, file_id, count=1):
    # Native validation intentionally refuses provider IDs. Exercise the
    # already-normalized payload boundary without weakening its input gate.
    convert = bridge.prepare

    def use_internal_reference(body, evidence, **options):
        payload, registry = convert(body, evidence, **options)
        assert payload['input'][0]['content'][1]['image_url'].startswith('data:image/')
        payload['input'][0]['content'] = existing_image_request(file_id, count)['input'][0]['content']
        return payload, registry

    monkeypatch.setattr(bridge, 'prepare', use_internal_reference)


@pytest.mark.asyncio
@pytest.mark.parametrize('status,consumption,pinned', [
    (200, 'provider_terminal', 0),
    (429, 'http_rejected', 0),
    (503, 'unknown', 1),
])
async def test_zero_new_conversions_still_pin_owned_existing_reference(
    tmp_path, monkeypatch, status, consumption, pinned,
):
    files = ImageFiles()
    factory, transports = image_factory(tmp_path, files)
    seeded = factory(None, lambda: KEY)
    async with seeded.prepare(image_input()) as plan:
        file_id = plan.payload['input'][0]['content'][1]['file_id']
    inject_internal_reference(monkeypatch, file_id)
    model_calls = []

    async def upstream(request):
        model_calls.append(await request.json())
        live = await seeded.diagnostics(private=True)
        assert live['active_transport_pins'] == 1
        assert len(live['unknown_upstream_calls']) == 1
        if status == 200:
            return web.json_response(terminal()['response'])
        return web.json_response({'error': {'message': 'fixture rejection'}}, status=status)

    async with harness(tmp_path, upstream, image_transport_factory=factory) as (client, state):
        response = await client.post('/v1/responses', json=image_input(), headers=HEADERS)
        await response.read()
        assert response.status == status and len(model_calls) == len(files.uploads) == 1
        assert model_calls[0]['input'][0]['content'][0]['file_id'] == file_id
        receipt = state.list_receipts()[0]
        detail = receipt['adaptations']['image_transport']
        assert detail['consumption_status'] == consumption
        preparation = detail['preparation']
        assert preparation['converted_image_occurrences'] == 0
        assert preparation['existing_file_references'] == preparation['owned_existing_file_references'] == 1
        assert preparation['consumption_managed'] is True
        assert preparation['referenced_file_bytes'] == len(files.uploads[0][1])
        assert preparation['max_referenced_file_bytes'] == MAX_REFERENCED_FILE_BYTES
        assert file_id not in json.dumps(receipt)
        assert (await seeded.diagnostics())['active_transport_pins'] == pinned


@pytest.mark.asyncio
async def test_external_file_reference_is_budgeted_but_has_no_local_lease(tmp_path, monkeypatch):
    files = ImageFiles()
    factory, transports = image_factory(tmp_path, files)
    file_id = 'or_file_external0001'
    files.files[file_id] = FileMetadata(file_id, 'outside.png', 'image/png', 10485760)
    inject_internal_reference(monkeypatch, file_id, 2)
    calls = []

    async def upstream(request):
        calls.append(await request.json())
        return web.json_response({'error': {'message': 'uncertain server failure'}}, status=503)

    async with harness(tmp_path, upstream, image_transport_factory=factory) as (client, state):
        response = await client.post('/v1/responses', json=image_input(), headers=HEADERS)
        await response.read()
        assert response.status == 503 and len(calls) == 1
        preparation = state.list_receipts()[0]['adaptations']['image_transport']
        assert preparation['consumption_status'] == 'not_applicable'
        assert preparation['preparation']['consumption_managed'] is False
        assert preparation['preparation']['referenced_file_bytes'] == MAX_REFERENCED_FILE_BYTES
        assert preparation['preparation']['owned_existing_file_references'] == 0
        assert preparation['preparation']['references'] == []
        cleanup = await transports[-1].cleanup(all_unpinned=True)
        assert cleanup['deleted'] == cleanup['pinned'] == 0 and not files.uploads and not files.deletes
        assert file_id in files.files


@pytest.mark.asyncio
async def test_repeated_file_occurrences_over_cap_reject_before_model(tmp_path, monkeypatch):
    files = ImageFiles()
    factory, _ = image_factory(tmp_path, files)
    file_id = 'or_file_external0001'
    nbytes = MAX_REFERENCED_FILE_BYTES // 2 + 1
    files.files[file_id] = FileMetadata(file_id, 'outside.png', 'image/png', nbytes)
    inject_internal_reference(monkeypatch, file_id, 2)

    async def upstream(request):
        pytest.fail('Oversized aggregate must not reach inference')

    async with harness(tmp_path, upstream, image_transport_factory=factory) as (client, state):
        response = await client.post('/v1/responses', json=image_input(), headers=HEADERS)
        await response.read()
        assert response.status == 413 and not files.uploads and not files.deletes
        detail = state.list_receipts()[0]['adaptations']['image_transport']
        assert detail['consumption_status'] == 'not_sent'
        budget = detail['budget_failure']
        assert budget['status'] == 'referenced_file_bytes_exceeded'
        assert budget['existing_referenced_file_bytes'] == 2 * nbytes
        assert budget['selection_feasibility_proven'] is True
        assert file_id not in json.dumps(detail)


@pytest.mark.asyncio
async def test_search_work_exhaustion_is_unknown_not_proven_oversize(tmp_path, monkeypatch):
    files = ImageFiles()
    factory, _ = image_factory(tmp_path, files)
    def exhausted(*args, **kwargs):
        raise image_selection.SelectionComplexityExceeded('injected exact solver exhaustion')
    monkeypatch.setattr(image_selection, 'select_positions', exhausted)

    async def upstream(request):
        pytest.fail('Unresolved plan must not reach inference')

    async with harness(tmp_path, upstream, image_transport_factory=factory) as (client, state):
        response = await client.post('/v1/responses', json=image_input(), headers=HEADERS)
        await response.read()
        assert response.status == 400 and not files.uploads
        detail = state.list_receipts()[0]['adaptations']['image_transport']
        assert detail['consumption_status'] == 'not_sent'
        assert detail['budget_failure']['status'] == 'selection_work_limit_exceeded'
        assert detail['budget_failure']['selection_feasibility_proven'] is False
        assert detail['budget_failure']['estimated_plan_bytes'] is None

@pytest.mark.asyncio
async def test_native_history_cannot_submit_provider_specific_image_file_ids(tmp_path):
    files = ImageFiles()
    factory, created = image_factory(tmp_path, files)
    async def upstream(request):
        pytest.fail('Native history replay must be rejected before inference')

    async with harness(tmp_path, upstream, image_transport_factory=factory) as (client, state):
        response = await client.post('/v1/responses', json=existing_image_request('or_file_external0001'), headers=HEADERS)
        assert response.status == 400
        assert 'Provider-specific image file IDs cannot be replayed' in await response.text()
        assert created == [] and files.uploads == [] and state.list_receipts() == []
