from __future__ import annotations
from contextlib import closing
import asyncio,base64,copy,hashlib,json,os,sqlite3,struct,subprocess,sys,time,zlib
from pathlib import Path
import pytest
from codex_app_deepseek.image_transport import (
    ImageTransport,TransportPolicy,ImageTransportError,UploadOutcomeUnknown,
    FileOperationError,FileMetadata,encode_body,image_plan,OpenRouterFilesBackend)


def png(color=(0,20,240)):
    def chunk(kind,data):return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',16,16,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+bytes(color)*16)*16))+chunk(b'IEND',b'')


def part(data=None,detail='original'):
    return {'type':'input_image','detail':detail,'image_url':'data:image/png;base64,'+base64.b64encode(data or png()).decode()}


def payload(parts=None):
    return {'model':'deepseek/deepseek-v4.1-flash-1017','provider':{'only':['deepseek'],'allow_fallbacks':False},
        'input':[{'type':'message','role':'user','content':[{'type':'input_text','text':'保持内容 😀'}]+(parts or [part()])}],
        'stream':True,'store':False}


class FakeFiles:
    def __init__(self):
        self.files={};self.uploaded=[];self.deleted=[];self.gets=[]
        self.upload_error=None;self.delete_error=None;self.delay=0;self.return_wrong_metadata=False
    async def upload(self,key,data,mime,filename):
        if self.delay:await asyncio.sleep(self.delay)
        self.uploaded.append((key,bytes(data),mime,filename))
        fid='or_file_'+str(len(self.uploaded)).zfill(12)
        item=FileMetadata(fid,filename,mime,len(data))
        self.files[fid]=item
        if self.upload_error:
            if isinstance(self.upload_error,FileOperationError) and self.upload_error.definitely_not_created:self.files.pop(fid)
            raise self.upload_error
        if self.return_wrong_metadata:return FileMetadata(fid,filename,mime,len(data)+1)
        return item
    async def get(self,key,fid):
        self.gets.append((key,fid));return self.files.get(fid)
    async def delete(self,key,fid):
        self.deleted.append((key,fid));self.files.pop(fid,None)
        if self.delete_error:raise self.delete_error


def transport(tmp_path,backend=None,**kwargs):
    return ImageTransport(tmp_path/'cache',lambda:'test-key-a',backend or FakeFiles(),
        TransportPolicy(activate_above_bytes=1,verify_after_seconds=0),**kwargs)


@pytest.mark.asyncio
async def test_small_payload_uses_no_credentials_disk_or_files(tmp_path):
    backend=FakeFiles()
    def forbidden():raise AssertionError('credential lookup for small payload')
    tr=ImageTransport(tmp_path/'cache',forbidden,backend,TransportPolicy(10_000))
    original=payload()
    async with tr.prepare(original) as value:
        assert value.payload==original and value.body==encode_body(original)
        assert value.scope is None and value.references==()
    assert not backend.uploaded and not list((tmp_path/'cache').iterdir())


@pytest.mark.asyncio
async def test_lossless_order_detail_namespace_and_duplicate_identity(tmp_path):
    backend=FakeFiles();tr=transport(tmp_path,backend)
    first,second=png(),png((240,10,10))
    original=payload([part(first,'low'),part(second),part(first,'high')])
    original['input'].append({'type':'function_call_output','call_id':'call-actual','output':[part(second,'original'),{'type':'input_text','text':'result'}]})
    original['input'].append({'type':'function_call','call_id':'call-later','name':'ns.tool','arguments':json.dumps(part(first))})
    original['tools']=[{'type':'function','name':'ns.tool','parameters':{'example':part(first)}}]
    before=copy.deepcopy(original)
    async with tr.prepare(original) as value:
        assert original==before
        assert len(backend.uploaded)==2 and [x[1] for x in backend.uploaded]==[first,second]
        assert value.image_occurrences==4 and value.uploads==2
        after=value.payload
        assert [p['detail'] for p in after['input'][0]['content'][1:]]==['low','original','high']
        assert after['input'][0]['content'][1]==before['input'][0]['content'][1]
        assert 'file_id' not in after['input'][0]['content'][1]
        assert after['input'][0]['content'][3]['file_id'] in backend.files
        assert value.converted_occurrences==3
        assert after['input'][1]['call_id']=='call-actual'
        assert after['input'][1]['output'][0]['file_id']==after['input'][0]['content'][2]['file_id']
        assert after['input'][2]==original['input'][2] and after['tools']==original['tools']
        restored=copy.deepcopy(after)
        raw_by_id={r['file_id']:next(x[1] for x in backend.uploaded if hashlib.sha256(x[1]).hexdigest()==r['sha256']) for r in value.references}
        for item in restored['input']:
            field='content' if item.get('type')=='message' else 'output'
            for p in item.get(field,[]) if isinstance(item.get(field),list) else []:
                if p.get('type')=='input_image' and p.get('file_id') in raw_by_id:
                    p['image_url']='data:image/png;base64,'+base64.b64encode(raw_by_id[p.pop('file_id')]).decode()
        assert restored==original
        assert 'file_id' not in json.dumps(value.receipt())
        assert 'or_file_' in json.dumps(value.receipt(private=True))
        assert (await tr.diagnostics())['active_transport_pins']==2
    assert (await tr.diagnostics())['active_transport_pins']==0
    async with tr.prepare(original) as value:assert value.uploads==0 and value.reused==2
    assert len(backend.uploaded)==2
    for p in (tmp_path/'cache').rglob('*'):
        if p.is_file():
            data=p.read_bytes();assert first not in data and second not in data and b'test-key-a' not in data


@pytest.mark.asyncio
async def test_foreign_url_and_existing_file_id_not_owned_or_deleted(tmp_path):
    backend=FakeFiles();tr=transport(tmp_path,backend)
    original=payload([{'type':'input_image','detail':'high','image_url':'https://example.invalid/a.png'},
                      {'type':'input_image','detail':'original','file_id':'or_file_external111'}])
    async with tr.prepare(original) as value:assert value.payload==original and value.references==()
    result=await tr.cleanup(all_unpinned=True)
    assert result['deleted']==0 and not backend.gets and not backend.deleted


@pytest.mark.parametrize('bad',[
    {'type':'input_image','image_url':'data:image/png;base64,???','detail':'original'},
    {'type':'input_image','image_url':'data:image/png;base64,','detail':'original'},
    dict(part(),file_id='or_file_conflict')])
def test_bad_inline_data_fails_without_silent_drop(bad):
    with pytest.raises(ImageTransportError):image_plan(payload([bad]))


@pytest.mark.asyncio
async def test_concurrent_prepare_shares_upload_and_independent_pins(tmp_path):
    backend=FakeFiles();backend.delay=.04
    tr=transport(tmp_path,backend);entered=asyncio.Event();release=asyncio.Event()
    async def first():
        async with tr.prepare(payload()) as value:
            entered.set();await release.wait();return value
    first_task=asyncio.create_task(first());await entered.wait()
    async with tr.prepare(payload()) as second:
        assert second.reused==1
        assert (await tr.cleanup(all_unpinned=True))['pinned']==1
        assert (await tr.diagnostics())['active_transport_pins']==2
    assert (await tr.diagnostics())['active_transport_pins']==1
    release.set();await first_task
    result=await tr.cleanup(all_unpinned=True)
    assert result['complete'] and result['deleted']==1 and len(backend.uploaded)==1


@pytest.mark.asyncio
async def test_expiry_is_local_policy_and_deleted_readback_required(tmp_path):
    backend=FakeFiles();now=[1000.0]
    tr=ImageTransport(tmp_path/'cache',lambda:'test-key-a',backend,TransportPolicy(1,idle_ttl_seconds=10),clock=lambda:now[0])
    async with tr.prepare(payload()):pass
    assert (await tr.cleanup())['deleted']==0
    now[0]+=11
    result=await tr.cleanup();assert result['deleted']==1 and result['complete']
    assert len(backend.gets)==2 and len(backend.deleted)==1
    assert (await tr.diagnostics())['assets'][0]['state']=='deleted'


@pytest.mark.asyncio
async def test_unknown_upload_never_automatically_reuploaded_or_adopted(tmp_path):
    backend=FakeFiles();backend.upload_error=TimeoutError()
    tr=transport(tmp_path,backend)
    with pytest.raises(TimeoutError):
        async with tr.prepare(payload()):pass
    backend.upload_error=None
    with pytest.raises(UploadOutcomeUnknown):
        async with tr.prepare(payload()):pass
    assert len(backend.uploaded)==1 and len(backend.files)==1
    result=await tr.cleanup(all_unpinned=True)
    assert result['unknown_uploads']==1 and result['pending']==1 and not result['complete']
    assert not backend.deleted
    diag=await tr.diagnostics(private=True)
    assert diag['assets'][0]['state']=='upload_unknown' and diag['assets'][0]['file_id'] is None
    assert diag['active_transport_pins']==0


@pytest.mark.asyncio
async def test_definitely_rejected_upload_can_be_attempted_by_new_request(tmp_path):
    backend=FakeFiles();backend.upload_error=FileOperationError('upload',413,definitely_not_created=True)
    tr=transport(tmp_path,backend)
    with pytest.raises(FileOperationError):
        async with tr.prepare(payload()):pass
    backend.upload_error=None
    async with tr.prepare(payload()) as value:assert value.uploads==1
    assert len(backend.uploaded)==2 and backend.uploaded[0][3]!=backend.uploaded[1][3]


@pytest.mark.asyncio
async def test_lost_delete_reply_get_404_reconciles_without_second_delete(tmp_path):
    backend=FakeFiles();tr=transport(tmp_path,backend)
    async with tr.prepare(payload()):pass
    backend.delete_error=TimeoutError()
    first=await tr.cleanup(all_unpinned=True)
    assert first['pending']==1 and not first['complete']
    backend.delete_error=None
    second=await tr.cleanup(all_unpinned=True)
    assert second['deleted']==1 and second['complete'] and len(backend.deleted)==1


@pytest.mark.asyncio
async def test_external_file_deletion_reuploads_before_inference(tmp_path):
    backend=FakeFiles();tr=transport(tmp_path,backend)
    async with tr.prepare(payload()) as first:fid=first.references[0]['file_id']
    backend.files.pop(fid)
    async with tr.prepare(payload()) as second:
        assert second.uploads==1 and second.references[0]['file_id']!=fid
    assert len(backend.uploaded)==2


@pytest.mark.asyncio
async def test_rotation_never_reuses_or_deletes_other_credentials_files(tmp_path):
    backend=FakeFiles();key=['key-one']
    tr=ImageTransport(tmp_path/'cache',lambda:key[0],backend,TransportPolicy(1))
    async with tr.prepare(payload()) as first:scope1=first.scope;fid1=first.references[0]['file_id']
    key[0]='key-two'
    async with tr.prepare(payload()) as second:
        assert second.scope!=scope1 and second.uploads==1
    result=await tr.cleanup(all_unpinned=True)
    assert result['foreign_scopes']==[scope1] and not result['complete']
    assert fid1 in backend.files and all(k=='key-two' for k,_ in backend.deleted)


@pytest.mark.asyncio
async def test_unexpected_metadata_never_sent_and_never_silently_deleted(tmp_path):
    backend=FakeFiles();backend.return_wrong_metadata=True;tr=transport(tmp_path,backend)
    with pytest.raises(ImageTransportError):
        async with tr.prepare(payload()):pytest.fail('Untrusted file was admitted')
    result=await tr.cleanup(all_unpinned=True)
    # Returned metadata was wrong, but GET restored exact acknowledged metadata.
    assert result['deleted']==1 and result['complete']


@pytest.mark.asyncio
async def test_cancel_releases_own_pins_without_terminating_other_work(tmp_path):
    backend=FakeFiles();tr=transport(tmp_path,backend);entered=asyncio.Event()
    async def call():
        async with tr.prepare(payload()):entered.set();await asyncio.Future()
    task=asyncio.create_task(call());await entered.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert (await tr.diagnostics())['active_transport_pins']==0
    assert (await tr.cleanup(all_unpinned=True))['deleted']==1


@pytest.mark.asyncio
async def test_dead_transport_owner_pin_reaped_but_unknown_owner_kept(tmp_path):
    backend=FakeFiles();alive=[True];tr=transport(tmp_path,backend,owner_alive=lambda p,b:alive[0])
    async with tr.prepare(payload()) as value:
        scope=value.scope
    database=tmp_path/'cache'/scope/'files.sqlite'
    with closing(sqlite3.connect(database)) as db, db:db.execute('INSERT INTO pins(lease,digest,pid,birth) VALUES(?,?,?,?)',('simulated-crash',hashlib.sha256(png()).hexdigest(),999999,0))
    alive[0]=None
    assert (await tr.cleanup(all_unpinned=True))['pinned']==1
    alive[0]=False
    assert (await tr.cleanup(all_unpinned=True))['deleted']==1


@pytest.mark.asyncio
async def test_crashed_pending_upload_is_unknown_not_retried(tmp_path):
    backend=FakeFiles();tr=transport(tmp_path,backend)
    async with tr.prepare(payload()) as value:scope=value.scope
    database=tmp_path/'cache'/scope/'files.sqlite'
    with closing(sqlite3.connect(database)) as db, db:db.execute("UPDATE assets SET state='upload_pending',file_id=NULL")
    with pytest.raises(UploadOutcomeUnknown):
        async with tr.prepare(payload()):pass
    assert len(backend.uploaded)==1


@pytest.mark.asyncio
async def test_unicode_body_bytes_and_no_image_body_are_preserved(tmp_path):
    tr=transport(tmp_path);original=payload([{'type':'input_text','text':'中文'*1000}])
    async with tr.prepare(original) as result:
        assert result.before_bytes==len(encode_body(original)) and result.payload==original
        assert result.before_bytes>len(json.dumps(original,ensure_ascii=False))
        assert not result.references


@pytest.mark.asyncio
async def test_os_lock_across_real_processes_deduplicates(tmp_path):
    # This launches only Python transport clients, no native Codex/runtime/setup.
    script=tmp_path/'transport_client.py'
    script.write_text("""import asyncio,base64,hashlib,json,sys,time\nfrom pathlib import Path\nfrom codex_app_deepseek.image_transport import *\nroot=Path(sys.argv[1])\nclass SharedFiles:\n async def upload(self,key,data,mime,filename):\n  await asyncio.sleep(.15)\n  with (root/'uploads.txt').open('a') as f:f.write(hashlib.sha256(data).hexdigest()+'\\n')\n  meta=FileMetadata('or_file_concurrent001',filename,mime,len(data))\n  (root/'remote.json').write_text(json.dumps({'id':meta.file_id,'filename':filename,'mime_type':mime,'size_bytes':len(data)}))\n  return meta\n async def get(self,key,fid):return FileMetadata.parse(json.loads((root/'remote.json').read_text()))\n async def delete(self,key,fid):raise AssertionError('not part of concurrency test')\nasync def run():\n tr=ImageTransport(root/'cache',lambda:'shared-test-key',SharedFiles(),TransportPolicy(1,verify_after_seconds=0))\n p={'input':[{'role':'user','content':[{'type':'input_image','detail':'original','image_url':'data:image/png;base64,'+base64.b64encode(b'fixed synthetic bytes').decode()}]}]}\n async with tr.prepare(p) as result:await asyncio.sleep(.2);print(result.uploads)\nasyncio.run(run())\n""",encoding='utf-8')
    processes=[await asyncio.create_subprocess_exec(sys.executable,str(script),str(tmp_path),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE) for _ in range(2)]
    outputs=await asyncio.gather(*(p.communicate() for p in processes))
    assert all(p.returncode==0 for p in processes),outputs
    assert sorted(out.decode().strip() for out,_ in outputs)==['0','1']
    assert len((tmp_path/'uploads.txt').read_text().splitlines())==1


@pytest.mark.asyncio
async def test_rotation_cleanup_can_finish_when_each_scope_is_explicitly_cleaned(tmp_path):
    backend=FakeFiles();key=['first']
    tr=ImageTransport(tmp_path/'cache',lambda:key[0],backend,TransportPolicy(1))
    async with tr.prepare(payload()):pass
    key[0]='second'
    async with tr.prepare(payload()):pass
    assert (await tr.cleanup(all_unpinned=True))['foreign_scopes']
    key[0]='first'
    final=await tr.cleanup(all_unpinned=True)
    assert final['complete'] and final['all_owned_files_deleted'] and not final['foreign_scopes']


class ChunkResponse:
    def __init__(self,status,data,chunk_size=7):
        self.status=status;self.data=data;self.chunk_size=chunk_size;self.content=self
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def read(self,n):
        chunk=self.data[:min(n,self.chunk_size)];self.data=self.data[len(chunk):];return chunk

class HttpClient:
    def __init__(self,responses):self.responses=list(responses);self.calls=[]
    def request(self,method,url,**kwargs):
        self.calls.append((method,url,kwargs));return self.responses.pop(0)


@pytest.mark.asyncio
async def test_official_http_backend_handles_fragmented_json_and_enforces_no_redirects():
    meta={'id':'or_file_unit123456','filename':'synthetic.png','mime_type':'image/png','size_bytes':42}
    client=HttpClient([ChunkResponse(200,json.dumps(meta).encode()),ChunkResponse(404,b'{}'),
                       ChunkResponse(200,json.dumps({'id':meta['id'],'type':'file_deleted'}).encode())])
    backend=OpenRouterFilesBackend(client)
    assert await backend.upload('synthetic-test-key',b'original-bytes','image/png','synthetic.png')==FileMetadata.parse(meta)
    assert await backend.get('synthetic-test-key',meta['id']) is None
    await backend.delete('synthetic-test-key',meta['id'])
    assert all(call[2]['allow_redirects'] is False for call in client.calls)
    assert all(call[1].startswith('https://openrouter.ai/api/v1/files') for call in client.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('status,not_created',[(400,True),(401,True),(413,True),(429,True),(500,False),(302,False),(408,False)])
async def test_http_upload_errors_keep_ambiguous_outcome_distinct(status,not_created):
    client=HttpClient([ChunkResponse(status,b'{"error":"untrusted upstream body"}')])
    with pytest.raises(FileOperationError) as failure:
        await OpenRouterFilesBackend(client).upload('secret-key',b'data','image/png','synthetic.png')
    assert failure.value.definitely_not_created==not_created
    assert 'secret-key' not in str(failure.value) and 'untrusted upstream body' not in str(failure.value)
    assert len(client.calls)==1


@pytest.mark.asyncio
async def test_backend_rejects_metadata_overflow_before_parsing():
    client=HttpClient([ChunkResponse(200,b'x'*(1024*1024+1),chunk_size=65536)])
    with pytest.raises(ImageTransportError,match='metadata limit'):
        await OpenRouterFilesBackend(client).get('key','or_file_example1234')


@pytest.mark.asyncio
async def test_real_process_crash_releases_os_lock_but_upload_stays_unknown(tmp_path):
    p=payload();data=json.dumps(p)
    script=tmp_path/'crash_client.py'
    script.write_text("""import asyncio,json,os,sys\nfrom pathlib import Path\nfrom codex_app_deepseek.image_transport import *\nroot=Path(sys.argv[1])\nclass CrashFiles:\n async def upload(self,key,data,mime,filename):\n  (root/'upload-started.json').write_text(json.dumps({'filename':filename,'bytes':len(data)}))\n  os._exit(73)\n async def get(self,key,fid):raise AssertionError()\n async def delete(self,key,fid):raise AssertionError()\nasync def run():\n tr=ImageTransport(root/'cache',lambda:'test-key-a',CrashFiles(),TransportPolicy(1))\n async with tr.prepare(json.loads(sys.stdin.read())):pass\nasyncio.run(run())\n""",encoding='utf-8')
    child=await asyncio.create_subprocess_exec(sys.executable,str(script),str(tmp_path),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    out,err=await asyncio.wait_for(child.communicate(data.encode()),15)
    assert child.returncode==73,(out,err)
    tr=transport(tmp_path)
    with pytest.raises(UploadOutcomeUnknown):
        async with tr.prepare(p):pass
    assert (await tr.diagnostics())['assets'][0]['state']=='upload_unknown'


@pytest.mark.asyncio
async def test_synthetic_101_images_wire_reduction_preserves_all_original_bytes(tmp_path):
    import random
    rng=random.Random(13456403)
    # ~32 MB base64 image history. Bytes are synthetic test images, never real input.
    images=[]
    def chunk(kind,data):return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    for _ in range(101):
        raw=b''.join(b'\0'+rng.randbytes(384*3) for _ in range(206))
        images.append(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',384,206,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw,1))+chunk(b'IEND',b''))
    history=[{'type':'message','role':'user','content':[part(image)]} for image in images]
    history.extend({'type':'message','role':'user','content':[{'type':'input_text','text':'synthetic-identity-'+str(i)+'x'*1700}]} for i in range(1105))
    original={'model':'deepseek/deepseek-v4.1-flash-1017','input':history}
    backend=FakeFiles();tr=transport(tmp_path,backend)
    async with tr.prepare(original) as result:
        assert result.before_bytes>33_500_000 and len(result.body)<29_000_000
        assert result.image_occurrences==101 and result.uploads==16 and result.converted_occurrences==16
        assert len(result.payload['input'])==1206
        assert {hashlib.sha256(x[1]).digest() for x in backend.uploaded} <= {hashlib.sha256(x).digest() for x in images}
        restore=copy.deepcopy(result.payload)
        by_id={r['file_id']:next(image for image in images if hashlib.sha256(image).hexdigest()==r['sha256']) for r in result.references}
        for item in restore['input'][:101]:
            part_value=item['content'][0]
            if 'file_id' in part_value:part_value['image_url']='data:image/png;base64,'+base64.b64encode(by_id[part_value.pop('file_id')]).decode()
        assert restore==original
        assert all(p['input'][0]['content'][0]['detail']=='original' for p in [original,result.payload])
        assert result.payload['input'][101:]==original['input'][101:]
    assert (await tr.cleanup(all_unpinned=True))['deleted']==16


@pytest.mark.asyncio
async def test_disconnect_retains_upstream_pin_until_verified_terminal(tmp_path):
    backend=FakeFiles();alive=[True];tr=transport(tmp_path,backend,owner_alive=lambda p,b:alive[0])
    with pytest.raises(TimeoutError):
        async with tr.prepare(payload()) as prepared:
            await prepared.mark_upstream_started('request-001')
            raise TimeoutError('lost upstream stream')
    alive[0]=False
    result=await tr.cleanup(all_unpinned=True)
    assert result['pinned']==1 and result['unknown_upstream_calls']==1 and not result['complete']
    assert not backend.deleted
    with pytest.raises(ImageTransportError):await tr.settle_unknown('request-001','gen-synthetic','locally_cancelled')
    await tr.settle_unknown('request-001','gen-synthetic','completed')
    assert (await tr.cleanup(all_unpinned=True))['all_owned_files_deleted']


@pytest.mark.asyncio
async def test_provider_terminal_survives_later_client_delivery_error(tmp_path):
    backend=FakeFiles();tr=transport(tmp_path,backend)
    with pytest.raises(ValueError):
        async with tr.prepare(payload()) as prepared:
            await prepared.mark_upstream_started('request-002')
            await prepared.mark_upstream_settled('gen-synthetic-002','completed')
            raise ValueError('malformed data after provider terminal; client delivery fails')
    result=await tr.cleanup(all_unpinned=True)
    assert result['all_owned_files_deleted'] and result['unknown_upstream_calls']==0


@pytest.mark.asyncio
async def test_known_rejected_post_releases_file_but_500_is_unknown(tmp_path):
    backend=FakeFiles();tr=transport(tmp_path,backend)
    async with tr.prepare(payload()) as prepared:
        await prepared.mark_upstream_started('request-003')
        with pytest.raises(ImageTransportError):await prepared.mark_upstream_rejected(500)
        await prepared.mark_upstream_rejected(413)
    assert (await tr.cleanup(all_unpinned=True))['all_owned_files_deleted']


@pytest.mark.asyncio
async def test_started_receipt_is_unique_and_settle_requires_started(tmp_path):
    tr=transport(tmp_path)
    async with tr.prepare(payload()) as first:
        with pytest.raises(ImageTransportError):await first.mark_upstream_settled('gen-before-start','completed')
        await first.mark_upstream_started('request-004')
        async with tr.prepare(payload()) as second:
            with pytest.raises(ImageTransportError):await second.mark_upstream_started('request-004')
        await first.mark_upstream_settled('gen-after-start','failed')
    assert (await tr.diagnostics())['active_transport_pins']==0


@pytest.mark.asyncio
async def test_same_image_can_be_used_while_old_call_unknown_and_cleanup_waits(tmp_path):
    backend=FakeFiles();tr=transport(tmp_path,backend)
    async with tr.prepare(payload()) as first:await first.mark_upstream_started('request-005')
    async with tr.prepare(payload()) as second:
        assert second.reused==1
        await second.mark_upstream_started('request-006')
        await second.mark_upstream_settled('gen-six','completed')
    result=await tr.cleanup(all_unpinned=True)
    assert result['pinned']==1 and result['unknown_upstream_calls']==1
    await tr.settle_unknown('request-005','gen-five','failed')
    assert (await tr.cleanup(all_unpinned=True))['all_owned_files_deleted']


@pytest.mark.parametrize("duration",[float("nan"),float("inf"),True])
def test_cache_expiry_rejects_nonfinite_or_boolean_durations(duration):
    with pytest.raises(ValueError):TransportPolicy(1,idle_ttl_seconds=duration)


@pytest.mark.asyncio
async def test_101_file_reference_failure_regression_uses_at_most16_occurrences(tmp_path):
    from codex_app_deepseek.image_transport import count_file_references
    backend=FakeFiles();tr=transport(tmp_path,backend)
    original=payload([part() for _ in range(101)])
    async with tr.prepare(original) as result:
        assert result.image_occurrences==101 and result.converted_occurrences==16
        assert count_file_references(result.payload)==16 and result.uploads==1
        assert sum('image_url' in p for p in result.payload['input'][0]['content'][1:])==85
        assert result.receipt()['remaining_inline_images']==85
    assert len(backend.uploaded)==1


@pytest.mark.asyncio
async def test_mixed_selects_largest_net_savings_without_reordering(tmp_path):
    from codex_app_deepseek.image_transport import count_file_references
    backend=FakeFiles()
    tr=ImageTransport(tmp_path/'cache',lambda:'key',backend,TransportPolicy(1,max_file_references=2))
    originals=[part(png()+bytes([i])*n,detail=d) for i,n,d in [(1,100,'low'),(2,500,'high'),(3,1000,'original'),(4,50,'low')]]
    async with tr.prepare(payload(originals)) as result:
        parts=result.payload['input'][0]['content'][1:]
        assert 'image_url' in parts[0] and 'file_id' in parts[1] and 'file_id' in parts[2] and 'image_url' in parts[3]
        assert [p['detail'] for p in parts]==['low','high','original','low']
        assert parts[0]==originals[0] and parts[3]==originals[3]
        assert count_file_references(result.payload)==2


@pytest.mark.asyncio
async def test_existing_caller_owned_references_reserve_slots(tmp_path):
    from codex_app_deepseek.image_transport import count_file_references
    backend=FakeFiles();tr=transport(tmp_path,backend)
    original=payload([{'type':'input_file','file_id':'or_file_owned_elsewhere'}]+[part() for _ in range(20)])
    async with tr.prepare(original) as result:
        assert result.converted_occurrences==15 and result.existing_file_references==1
        assert count_file_references(result.payload)==16
        assert result.payload['input'][0]['content'][1]==original['input'][0]['content'][1]
    assert not backend.gets and not backend.deleted


@pytest.mark.asyncio
async def test_unsatisfied_byte_budget_fails_before_credentials_or_upload(tmp_path):
    from codex_app_deepseek.image_transport import PayloadBudgetExceeded
    backend=FakeFiles()
    def forbidden():raise AssertionError('budget failure must precede credential access')
    tr=ImageTransport(tmp_path/'cache',forbidden,backend,TransportPolicy(1,wire_budget_bytes=1000))
    original=payload([part() for _ in range(101)]);original['input'][0]['content'][0]['text']='x'*10000
    with pytest.raises(PayloadBudgetExceeded):
        async with tr.prepare(original):pass
    assert not backend.uploaded


@pytest.mark.asyncio
async def test_existing_reference_limit_failure_does_not_upload(tmp_path):
    from codex_app_deepseek.image_transport import FileReferenceLimitExceeded
    backend=FakeFiles();tr=transport(tmp_path,backend)
    original=payload([{'type':'input_file','file_id':'or_file_external'+str(i)} for i in range(17)]+[part()])
    with pytest.raises(FileReferenceLimitExceeded):
        async with tr.prepare(original):pass
    assert not backend.uploaded


@pytest.mark.asyncio
@pytest.mark.parametrize('raw',[
    b'{"id":"or_file_abcdefgh","id":"or_file_ijklmnop","filename":"a","mime_type":"image/png","size_bytes":3}',
    b'{"id":"or_file_abcdefgh","filename":"a","filename":"b","mime_type":"image/png","size_bytes":3}',
    b'{"id":"or_file_abcdefgh","filename":"a","mime_type":"image/png","size_bytes":3,"x":NaN}',
    b'{"id":"or_file_abcdefgh","filename":"a","mime_type":"image/png","size_bytes":3,"x":Infinity}',
    b'{"id":"or_file_abcdefgh","filename":"a","mime_type":"image/png","size_bytes":3,"x":1e99999}'
])
async def test_files_metadata_rejects_ambiguous_or_nonfinite_json(raw):
    backend=OpenRouterFilesBackend(HttpClient([ChunkResponse(200,raw)]))
    with pytest.raises(ImageTransportError,match='ambiguous or invalid'):
        await backend.get('key','or_file_abcdefgh')


@pytest.mark.asyncio
async def test_explicit_proxy_used_for_upload_get_and_delete():
    meta={'id':'or_file_abcdefgh','filename':'a','mime_type':'image/png','size_bytes':3}
    client=HttpClient([ChunkResponse(200,json.dumps(meta).encode()),ChunkResponse(200,json.dumps(meta).encode()),ChunkResponse(200,json.dumps({'id':meta['id'],'type':'file_deleted'}).encode())])
    proxy='http://proxyuser:proxypass@127.0.0.1:18888'
    backend=OpenRouterFilesBackend(client,proxy_url=proxy)
    await backend.upload('key',b'png','image/png','a');await backend.get('key',meta['id']);await backend.delete('key',meta['id'])
    assert all(call[2]['proxy']==proxy and call[2]['allow_redirects'] is False for call in client.calls)


@pytest.mark.asyncio
async def test_safe_http_error_metadata_and_cleanup_stop_on429(tmp_path):
    response=ChunkResponse(429,b'{"error":{"code":"rate_limit_exceeded","message":"arbitrary-secret-text"}}')
    response.headers={'Retry-After':'60','X-RateLimit-Remaining':'0','Authorization':'Bearer secret','Set-Cookie':'secret'}
    backend=OpenRouterFilesBackend(HttpClient([response]))
    with pytest.raises(FileOperationError) as failure:await backend.get('private-key','or_file_abcdefgh')
    assert failure.value.safe_headers=={'retry-after':'60','x-ratelimit-remaining':'0'}
    assert failure.value.safe_error_code=='rate_limit_exceeded' and 'arbitrary-secret-text' not in str(failure.value)
    class Limited(FakeFiles):
        async def delete(self,key,fid):
            self.deleted.append((key,fid));raise FileOperationError('delete',429,safe_headers={'retry-after':'60'})
    files=Limited();tr=transport(tmp_path,files)
    async with tr.prepare(payload([part(png((i,0,0))) for i in range(3)])):pass
    result=await tr.cleanup(all_unpinned=True)
    assert len(files.deleted)==1 and result['remaining_assets']==3 and not result['all_owned_files_deleted']
    assert result['halted_error']['http_status']==429 and result['halted_error']['headers']['retry-after']=='60'


class PagedFiles(FakeFiles):
    def __init__(self):
        super().__init__();self.list_calls=[];self.foreign=[];self.list_error=None;self.repeat_cursor=False;self.after_get=None
    async def list_page(self,key,*,limit,cursor):
        from codex_app_deepseek.image_transport import FilePage
        self.list_calls.append((limit,cursor))
        if self.list_error:raise self.list_error
        data=[{'id':m.file_id,'filename':m.filename,'mime_type':m.mime,'size_bytes':m.size_bytes} for m in self.files.values()]+self.foreign
        offset=int(cursor or '0') if not self.repeat_cursor else 0
        entries=data[offset:offset+limit];more=offset+limit<len(data)
        return FilePage(tuple(entries),more,('repeat' if self.repeat_cursor else str(offset+limit)) if more else None)
    async def get(self,key,fid):
        meta=await super().get(key,fid)
        if self.after_get:self.after_get()
        return meta


async def unknown_upload(tr,backend,data=None):
    backend.upload_error=TimeoutError('acknowledgement lost')
    with pytest.raises(TimeoutError):
        async with tr.prepare(payload([part(data or png())])):pass
    backend.upload_error=None


@pytest.mark.asyncio
async def test_unknown_upload_reconcile_one_scan_for_multiple_targets_and_no_foreign_logs(tmp_path):
    backend=PagedFiles();tr=transport(tmp_path,backend)
    await unknown_upload(tr,backend,png((1,2,3)));await unknown_upload(tr,backend,png((4,5,6)))
    backend.foreign=[{'id':'or_file_foreign_private','filename':'FOREIGN-private-document.txt','mime_type':'text/plain','size_bytes':99}]
    result=await tr.reconcile_unknown_uploads(page_size=1,max_pages=3)
    assert result['complete'] and result['resolved_count']==2 and result['pages_read']==3
    assert len(backend.list_calls)==3 and len(backend.deleted)==2 and len(backend.uploaded)==2
    assert result['nonmatching_entries_discarded']==1 and not result['file_contents_read']
    assert 'FOREIGN-private-document' not in json.dumps(result) and 'or_file_foreign_private' not in json.dumps(result)
    for file in (tmp_path/'cache').rglob('files.sqlite'):
        assert b'FOREIGN-private-document' not in file.read_bytes() and b'or_file_foreign_private' not in file.read_bytes()
    assert all(row['state']=='deleted' for row in (await tr.diagnostics())['assets'])
    async with tr.prepare(payload([part(png((1,2,3)))])) as again:assert again.uploads==1


@pytest.mark.asyncio
async def test_partial_listing_cannot_declare_unique_even_if_target_found(tmp_path):
    backend=PagedFiles();tr=transport(tmp_path,backend);await unknown_upload(tr,backend)
    backend.foreign=[{'id':'or_file_foreign1','filename':'other1','mime_type':'text/plain','size_bytes':1}]
    result=await tr.reconcile_unknown_uploads(page_size=1,max_pages=1)
    assert result['state']=='metadata_scan_incomplete' and not result['complete']
    assert not backend.deleted and not backend.gets
    assert (await tr.diagnostics())['assets'][0]['state']=='upload_unknown'


@pytest.mark.asyncio
async def test_duplicate_or_conflicting_nonce_match_never_deleted(tmp_path):
    backend=PagedFiles();tr=transport(tmp_path,backend);await unknown_upload(tr,backend)
    meta=next(iter(backend.files.values()))
    backend.foreign=[{'id':'or_file_duplicate000','filename':meta.filename,'mime_type':meta.mime,'size_bytes':meta.size_bytes}]
    result=await tr.reconcile_unknown_uploads()
    assert result['targets'][0]['state']=='ambiguous' and not backend.deleted and not backend.gets
    backend.foreign[0]['size_bytes']+=1
    result=await tr.reconcile_unknown_uploads()
    assert result['targets'][0]['state']=='metadata_conflict' and not backend.deleted


@pytest.mark.asyncio
async def test_empty_complete_scan_does_not_claim_unknown_upload_never_happened(tmp_path):
    backend=PagedFiles();tr=transport(tmp_path,backend);await unknown_upload(tr,backend);backend.files.clear()
    result=await tr.reconcile_unknown_uploads()
    assert result['enumeration_complete'] and result['targets'][0]['state']=='not_found' and not result['complete']
    with pytest.raises(UploadOutcomeUnknown):
        async with tr.prepare(payload()):pass
    assert len(backend.uploaded)==1


@pytest.mark.asyncio
async def test_repeated_cursor_and_rate_limit_stop_bounded_scan(tmp_path):
    backend=PagedFiles();tr=transport(tmp_path,backend);await unknown_upload(tr,backend)
    backend.foreign=[{'id':'or_file_foreign1','filename':'other1','mime_type':'text/plain','size_bytes':1}]
    backend.repeat_cursor=True
    result=await tr.reconcile_unknown_uploads(page_size=1,max_pages=4)
    assert result['state']=='metadata_scan_failed' and len(backend.list_calls)==2 and not backend.deleted
    backend.repeat_cursor=False;backend.list_calls=[];backend.list_error=FileOperationError('list',429,safe_headers={'retry-after':'120'})
    result=await tr.reconcile_unknown_uploads()
    assert len(backend.list_calls)==1 and result['error']['http_status']==429
    assert result['error']['headers']['retry-after']=='120' and not backend.deleted


@pytest.mark.asyncio
async def test_local_readonly_before_id_persist_never_deletes_and_is_recoverable(tmp_path,monkeypatch):
    from contextlib import asynccontextmanager
    backend=PagedFiles();tr=transport(tmp_path,backend);await unknown_upload(tr,backend)
    normal=tr._db
    @asynccontextmanager
    async def readonly(directory):
        async with normal(directory) as db:
            db.execute('PRAGMA query_only=ON');yield db
    monkeypatch.setattr(tr,'_db',readonly)
    result=await tr.reconcile_unknown_uploads()
    assert not result['complete'] and not backend.deleted
    monkeypatch.setattr(tr,'_db',normal)
    assert (await tr.diagnostics())['assets'][0]['state']=='upload_unknown'
    assert (await tr.reconcile_unknown_uploads())['complete']


@pytest.mark.asyncio
async def test_sqlite_failure_after_remote_delete_recovers_get_first_without_second_delete(tmp_path,monkeypatch):
    from contextlib import asynccontextmanager
    backend=PagedFiles();tr=transport(tmp_path,backend);await unknown_upload(tr,backend)
    normal_db=tr._db;normal_delete=backend.delete;holder={}
    @asynccontextmanager
    async def capture(directory):
        async with normal_db(directory) as db:holder['db']=db;yield db
    async def delete_then_disk_failure(key,fid):
        await normal_delete(key,fid);holder['db'].execute('PRAGMA query_only=ON')
    monkeypatch.setattr(tr,'_db',capture);monkeypatch.setattr(backend,'delete',delete_then_disk_failure)
    result=await tr.reconcile_unknown_uploads()
    assert result['state']=='cleanup_state_failure' and len(backend.deleted)==1 and not backend.files
    monkeypatch.setattr(tr,'_db',normal_db);monkeypatch.setattr(backend,'delete',normal_delete)
    recovered=await tr.reconcile_unknown_uploads()
    assert recovered['complete'] and recovered['known_cleanup_count']==1 and len(backend.deleted)==1
    assert (await tr.diagnostics())['assets'][0]['state']=='deleted'


@pytest.mark.asyncio
async def test_credential_rotation_during_reconciliation_never_cleans_other_scope(tmp_path):
    backend=PagedFiles();key=['first-key'];tr=ImageTransport(tmp_path/'cache',lambda:key[0],backend,TransportPolicy(1))
    await unknown_upload(tr,backend)
    backend.after_get=lambda:key.__setitem__(0,'second-key')
    result=await tr.reconcile_unknown_uploads()
    assert result['state']=='cleanup_state_failure' and not backend.deleted and backend.files
    backend.after_get=None;key[0]='first-key'
    assert (await tr.reconcile_unknown_uploads())['complete']


@pytest.mark.asyncio
async def test_files_list_uses_only_bounded_metadata_endpoint_and_proxy():
    body={'data':[{'id':'or_file_foreign1','filename':'foreign','mime_type':'text/plain','size_bytes':42,'extra':'not forwarded'}],'has_more':True,'cursor':'opaque'}
    client=HttpClient([ChunkResponse(200,json.dumps(body).encode())]);backend=OpenRouterFilesBackend(client,proxy_url='http://127.0.0.1:18999')
    page=await backend.list_page('key',limit=2,cursor='previous')
    assert page.has_more and page.cursor=='opaque' and 'extra' not in page.entries[0]
    method,url,args=client.calls[0]
    assert method=='GET' and url=='https://openrouter.ai/api/v1/files'
    assert args['params']=={'limit':'2','order':'asc','cursor':'previous'} and args['proxy']=='http://127.0.0.1:18999'
    assert 'foreign' not in repr(page)


@pytest.mark.asyncio
async def test_no_unknown_uploads_never_enumerate_workspace(tmp_path):
    backend=PagedFiles();tr=transport(tmp_path,backend)
    async with tr.prepare(payload()):pass
    result=await tr.reconcile_unknown_uploads()
    assert result['state']=='nothing_to_reconcile' and not backend.list_calls and not backend.deleted


@pytest.mark.asyncio
async def test_unwritable_cache_admission_has_no_cloud_effect_and_recovers(tmp_path,monkeypatch):
    backend=PagedFiles();tr=transport(tmp_path,backend);await unknown_upload(tr,backend)
    normal=tr._scope
    async def denied(key):raise PermissionError('synthetic cache directory is read-only')
    monkeypatch.setattr(tr,'_scope',denied)
    failure=await tr.reconcile_unknown_uploads()
    assert failure['state']=='local_state_unavailable' and not backend.list_calls and not backend.deleted
    monkeypatch.setattr(tr,'_scope',normal)
    assert (await tr.reconcile_unknown_uploads())['complete']


@pytest.mark.asyncio
async def test_prepare_captured_credential_binds_files_without_second_lookup(tmp_path):
    backend=FakeFiles()
    def forbidden():raise AssertionError('credential must be captured once by the calling HTTP request')
    tr=ImageTransport(tmp_path/'cache',forbidden,backend,TransportPolicy(1))
    async with tr.prepare(payload(),credential='captured-request-key') as first:
        assert backend.uploaded[0][0]=='captured-request-key'
        await first.mark_upstream_started('captured-key-attempt')
        await first.mark_upstream_settled('gen-captured-key','completed')
    tr.credentials=lambda:'new-request-key'
    async with tr.prepare(payload()) as second:
        assert backend.uploaded[1][0]=='new-request-key' and second.scope!=first.scope
    assert len(backend.uploaded)==2


@pytest.mark.asyncio
async def test_cleanup_max_assets_leaves_honest_deferred_count(tmp_path):
    backend=FakeFiles();tr=transport(tmp_path,backend)
    async with tr.prepare(payload([part(png((i,2,3))) for i in range(5)])):pass
    first=await tr.cleanup(all_unpinned=True,max_assets=2)
    assert first['deleted']==2 and first['remaining_assets']==3 and first['deferred_assets']==3 and not first['complete']
    second=await tr.cleanup(all_unpinned=True,max_assets=2)
    assert second['deleted']==2 and second['remaining_assets']==1
    final=await tr.cleanup(all_unpinned=True,max_assets=2)
    assert final['all_owned_files_deleted'] and final['remaining_assets']==0

@pytest.mark.asyncio
@pytest.mark.parametrize('terminal', ['completed', 'failed', 'incomplete', 'http_rejected'])
async def test_verified_terminal_pin_recovers_after_release_failure_with_live_owner(tmp_path, monkeypatch, terminal):
    from contextlib import asynccontextmanager
    backend = FakeFiles()
    tr = transport(tmp_path, backend, owner_alive=lambda *_: True)
    original_db = tr._db

    @asynccontextmanager
    async def unavailable_during_release(directory):
        raise OSError('temporary lease-release failure')
        yield  # pragma: no cover - retain the async context manager shape

    with pytest.raises(OSError, match='temporary lease-release failure'):
        async with tr.prepare(payload()) as prepared:
            await prepared.mark_upstream_started('request-settled-release-failure')
            if terminal == 'http_rejected':
                await prepared.mark_upstream_rejected(429)
            else:
                await prepared.mark_upstream_settled('gen-settled-release-failure', terminal)
            monkeypatch.setattr(tr, '_db', unavailable_during_release)
    monkeypatch.setattr(tr, '_db', original_db)

    # Prove the interrupted local release left the already-settled lease behind;
    # no dead-PID inference, new inference, re-upload, or replay is needed.
    _, directory = await tr._scope('test-key-a')
    with closing(sqlite3.connect((directory / 'files.sqlite').as_uri() + '?mode=ro', uri=True)) as db:
        assert db.execute('SELECT phase FROM pins').fetchall() == [('settled',)]
    assert len(backend.uploaded) == 1 and not backend.deleted
    assert tr.owner_alive(tr.pid, tr.birth) is True

    result = await tr.cleanup(all_unpinned=True)
    assert result['all_owned_files_deleted'] and result['pinned'] == 0
    assert result['deleted'] == 1 and len(backend.uploaded) == 1
    assert len(backend.deleted) == 1 and not backend.files
    assert (await tr.diagnostics())['active_transport_pins'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['prepared', 'upstream_unknown'])
async def test_live_unsettled_image_pin_survives_recovery_opens(tmp_path, phase):
    backend = FakeFiles()
    tr = transport(tmp_path, backend, owner_alive=lambda *_: True)
    async with tr.prepare(payload()) as prepared:
        if phase == 'upstream_unknown':
            await prepared.mark_upstream_started('request-unsettled-remains-protected')
        # A new transport facade and repeated ledger opens must not turn
        # elapsed time, a live owner, or a cleanup request into terminal proof.
        reopened = transport(tmp_path, backend, owner_alive=lambda *_: True)
        result = await reopened.cleanup(all_unpinned=True)
        assert result['pinned'] == 1 and not result['all_owned_files_deleted']
        assert not backend.deleted
        assert (await reopened.diagnostics())['active_transport_pins'] == 1
        if phase == 'upstream_unknown':
            await prepared.mark_upstream_settled('gen-unsettled-now-completed', 'completed')
    assert (await tr.cleanup(all_unpinned=True))['all_owned_files_deleted']

@pytest.mark.parametrize('location', [
    'function_parameters', 'function_output_schema', 'responses_schema',
    'openai_schema', 'metadata', 'client_metadata', 'namespaced_function',
])
def test_file_reference_count_ignores_opaque_schema_and_metadata(location):
    from codex_app_deepseek.image_transport import count_file_references
    examples = [{'file_id': 'or_file_schema_example_' + str(i).zfill(10)} for i in range(17)]
    schema = {'type': 'object', 'properties': {'file_id': {'type': 'string'}},
              'required': ['file_id'], 'additionalProperties': False, 'examples': examples}
    value = payload()
    function = {'type': 'function', 'name': 'lookup_file', 'parameters': {'type': 'object'}}
    if location == 'function_parameters':
        function['parameters'] = schema
        value['tools'] = [function]
    elif location == 'function_output_schema':
        function['output_schema'] = schema
        value['tools'] = [function]
    elif location == 'namespaced_function':
        function['parameters'] = schema
        value['tools'] = [{'type': 'namespace', 'name': 'files', 'tools': [function]}]
    elif location == 'responses_schema':
        value['text'] = {'format': {'type': 'json_schema', 'name': 'result', 'schema': schema}}
    elif location == 'openai_schema':
        value['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'result', 'schema': schema}}
    else:
        # A business tracking value is not an uploaded input-file reference.
        value[location] = {'file_id': 'business-record-only'}
    before = copy.deepcopy(value)
    assert count_file_references(value) == 0
    assert value == before


@pytest.mark.asyncio
async def test_schema_file_id_examples_do_not_block_actual_prepared_image_request(tmp_path):
    from codex_deepseek.protocol import MODEL, prepare
    from codex_app_deepseek.image_transport import count_file_references
    parameters = {'type': 'object', 'properties': {'file_id': {'type': 'string'}},
                  'examples': [{'file_id': 'or_file_schema_example_' + str(i).zfill(10)} for i in range(17)]}
    native = {'model': MODEL, 'stream': True,
              'input': [{'type': 'message', 'role': 'user', 'content': [part()]}],
              'tools': [{'type': 'function', 'name': 'lookup_file', 'parameters': parameters}]}
    original = copy.deepcopy(native)
    normalized, _ = prepare(native)
    backend = FakeFiles()
    tr = transport(tmp_path, backend)
    async with tr.prepare(normalized) as prepared:
        assert count_file_references(prepared.payload) == 1
        assert prepared.existing_file_references == 0 and prepared.converted_occurrences == 1
        assert prepared.payload['tools'][0]['parameters'] == parameters
        assert prepared.uploads == 1 and prepared.reused == 0
        assert prepared.references[0]['sha256'] == hashlib.sha256(png()).hexdigest()
    assert native == original
    assert (await tr.cleanup(all_unpinned=True))['all_owned_files_deleted']


def test_real_reference_occurrences_and_unclassified_file_fields_remain_reserved():
    from codex_app_deepseek.image_transport import count_file_references
    same_id = 'or_file_same_resource'
    value = payload([
        {'type': 'input_image', 'file_id': same_id},
        {'type': 'input_file', 'file_id': same_id},
    ])
    value['input'] += [
        {'type': 'function_call_output', 'call_id': 'first', 'output': [
            {'type': 'input_image', 'file_id': same_id}, {'type': 'input_file', 'file_id': same_id}]},
        {'type': 'custom_tool_call_output', 'call_id': 'second', 'output': [
            {'type': 'input_image', 'file_id': same_id}]},
    ]
    value['tools'] = [
        {'type': 'code_interpreter', 'container': {'type': 'auto', 'file_ids': [same_id, same_id]}},
        {'type': 'openrouter:bash', 'parameters': {'environment': {
            'type': 'container_auto', 'file_ids': [same_id, same_id]}}},
        {'type': 'openrouter:shell', 'parameters': {'environment': {
            'type': 'container_reference', 'id': 'session-owned-elsewhere', 'file_ids': [same_id]}}},
        {'type': 'image_generation', 'input_image_mask': {'file_id': same_id}},
    ]
    # Unknown locations remain conservatively counted rather than silently
    # treated as opaque data. This does not declare new access-program support.
    value['access_programs'] = {'future': {'file_ids': [same_id]}}
    value['future_extension'] = {'file_id': same_id}
    assert count_file_references(value) == 13


def test_file_reference_counter_never_parses_arguments_or_text_outputs():
    from codex_app_deepseek.image_transport import count_file_references
    text = json.dumps({'file_id': 'or_file_text_only', 'file_ids': ['or_file_text_only'] * 17})
    value = payload()
    value['input'] += [
        {'type': 'function_call', 'name': 'lookup_file', 'call_id': 'first', 'arguments': text},
        {'type': 'function_call_output', 'call_id': 'first', 'output': text},
        {'type': 'custom_tool_call_output', 'call_id': 'second', 'output': text},
    ]
    assert count_file_references(value) == 0
