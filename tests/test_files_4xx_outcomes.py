"""Synthetic outcome checks; no real Files, model, App or user profile."""
import pytest
from aiohttp import web
from codex_app_deepseek.image_transport import FileOperationError,OpenRouterFilesBackend,UploadOutcomeUnknown,_FileResponse
from test_bridge import HEADERS,ImageFiles,harness,image_factory,image_input
from test_image_transport import PagedFiles,transport,payload


@pytest.mark.asyncio
@pytest.mark.parametrize('status',[400,401,403,404,413,415,422,429])
async def test_responses_4xx_does_not_prove_files_consumption_ended(tmp_path,status):
    backend=ImageFiles();factory,made=image_factory(tmp_path,backend);calls=[]
    async def upstream(request):
        calls.append(1);return web.json_response({'error':{'message':'synthetic'}},status=status,headers={'Retry-After':'3'})
    async with harness(tmp_path,upstream,image_transport_factory=factory) as (client,state):
        response=await client.post('/v1/responses',json=image_input(),headers=HEADERS)
        assert response.status==status and response.headers['Retry-After']=='3'
        await response.read()
        receipt=state.receipt(response.headers['X-Gateway-Request-Id'])
        assert receipt['status']=='upstream_error'
        assert receipt['adaptations']['image_transport']['consumption_status']=='unknown'
        assert len(backend.uploads)==1 and calls==[1]
        result=await made[0].cleanup(all_unpinned=True)
        assert result['pinned']==1 and not result['all_owned_files_deleted'] and not backend.deletes


class CreatedBefore4xx(PagedFiles):
    """Risk model only; no claim about actual OpenRouter internals."""
    def __init__(self,status):super().__init__();self.status=status
    async def upload(self,key,data,mime,filename):
        await super().upload(key,data,mime,filename)
        backend=OpenRouterFilesBackend(None)
        async def fake_call(method,suffix,credential,**kwargs):
            assert method=='POST' and not suffix
            return _FileResponse(self.status,None,None,{'retry-after':'3'})
        backend._call=fake_call
        return await backend.upload(key,data,mime,filename)


@pytest.mark.asyncio
@pytest.mark.parametrize('status',[400,401,403,413,415,422,429,500])
async def test_files_4xx_preserves_original_unknown_upload_for_reconciliation(tmp_path,status):
    backend=CreatedBefore4xx(status);tr=transport(tmp_path,backend)
    with pytest.raises(FileOperationError) as error:
        async with tr.prepare(payload()):pass
    assert error.value.status==status and not error.value.definitely_not_created
    assert error.value.safe_headers=={'retry-after':'3'}
    diag=await tr.diagnostics(private=True)
    assert diag['assets'][0]['state']=='upload_unknown' and len(backend.files)==len(backend.uploaded)==1
    cold=transport(tmp_path,backend)
    with pytest.raises(UploadOutcomeUnknown):
        async with cold.prepare(payload()):pass
    assert len(backend.files)==len(backend.uploaded)==1
    assert not (await cold.cleanup(all_unpinned=True))['complete']
    recovered=await cold.reconcile_unknown_uploads()
    assert recovered['complete'] and recovered['resolved_count']==1
    assert len(backend.deleted)==1 and not backend.files and len(backend.uploaded)==1
