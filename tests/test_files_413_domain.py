"""Do not treat a Files upload rejection as model context capacity."""
import pytest
from aiohttp import web
from codex_app_deepseek.image_transport import FileOperationError
from test_bridge import HEADERS,ImageFiles,harness,image_factory,image_input


@pytest.mark.asyncio
@pytest.mark.parametrize('stream',[False,True])
async def test_files_413_remains_files_failure_not_native_context_capacity(tmp_path,stream):
    class RejectUpload(ImageFiles):
        async def upload(self,key,data,mime,filename):
            self.uploads.append((key,bytes(data),filename))
            raise FileOperationError('upload',413,definitely_not_created=True,
                                     safe_error_code='payload_too_large',safe_headers={'retry-after':'7'})
    backend=RejectUpload();factory,_=image_factory(tmp_path,backend);calls=[]
    async def upstream(request):
        calls.append(1);return web.json_response({'error':{'message':'unexpected'}},status=500)
    async with harness(tmp_path,upstream,image_transport_factory=factory) as (client,state):
        response=await client.post('/v1/responses',json=image_input(stream=stream),headers=HEADERS)
        body=await response.json()
        assert response.status==400
        assert response.headers['Retry-After']=='7'
        assert body['error']['file_http_status']==413
        assert body['error']['code']=='provider_compatibility' and 'Files API' in body['error']['message']
        assert len(backend.uploads)==1 and not calls
        receipt=state.receipt(response.headers['X-Gateway-Request-Id'])
        assert receipt['status']=='adapter_error' and receipt['upstream_status'] is None
        assert receipt['adaptations']['image_transport']['consumption_status']=='not_sent'


@pytest.mark.asyncio
async def test_model_responses_413_stays_413_with_retry_after(tmp_path):
    calls=[]
    async def upstream(request):
        calls.append(1);return web.json_response({'error':{'message':'synthetic model capacity'}},status=413,headers={'Retry-After':'7'})
    async with harness(tmp_path,upstream) as (client,state):
        response=await client.post('/v1/responses',json={'model':'deepseek/deepseek-v4.1-flash-1017','input':'synthetic'},headers=HEADERS)
        assert response.status==413 and response.headers['Retry-After']=='7'
        assert (await response.json())['error']['message']=='synthetic model capacity'
        assert calls==[1] and state.list_receipts()[0]['status']=='upstream_error'


@pytest.mark.asyncio
@pytest.mark.parametrize('error_domain',['files','responses'])
async def test_owned_native_cli_does_not_compact_on_files_413(tmp_path,error_domain):
    """Opt-in real CLI, local fake provider; this is not an App GUI check."""
    import asyncio,json,os
    from pathlib import Path
    binary=os.environ.get('CADSG_TEST_NATIVE_BINARY')
    catalog=os.environ.get('CADSG_TEST_MODEL_CATALOG')
    if not binary or not catalog:
        pytest.skip('requires an explicitly selected owned native CLI and catalog')
    binary=str(Path(binary).resolve(strict=True));catalog=str(Path(catalog).resolve(strict=True))
    from test_bridge import TOKEN,terminal
    from test_image_transport import png
    class RejectUpload(ImageFiles):
        async def upload(self,key,data,mime,filename):
            if error_domain=='responses':
                return await super().upload(key,data,mime,filename)
            self.uploads.append((key,bytes(data),filename))
            raise FileOperationError('upload',413,definitely_not_created=True,safe_headers={'retry-after':'1'})
    backend=RejectUpload();factory,made=image_factory(tmp_path,backend);calls=[]
    async def upstream(request):
        from codex_deepseek.protocol import encode_sse
        calls.append(await request.json())
        if error_domain=='responses' and len(calls)>1:
            return web.json_response({'error':{'message':'synthetic forbidden'}},status=403)
        final=terminal();final['response']['usage']['total_tokens']=18;item=final['response']['output'][0]
        frames=[{'type':'response.created','response':{'id':'resp_one','status':'in_progress','output':[]}},
                {'type':'response.output_item.done','output_index':0,'item':item},final]
        return web.Response(body=b''.join(encode_sse(frame) for frame in frames),content_type='text/event-stream')
    from codex_deepseek.platforms import private_runtime_home
    home = tmp_path / "owned-home"
    private_runtime_home(home)
    policy = home / "runtime-policy"
    policy.mkdir()
    (home/'temp').mkdir();work=tmp_path/'owned-work';work.mkdir();image=work/'owned.png';image.write_bytes(png())
    async with harness(tmp_path,upstream,image_transport_factory=factory) as (client,state):
        base=str(client.make_url('/v1')).rstrip('/')
        cfg=(f'model={json.dumps("deepseek/deepseek-v4.1-flash-1017")}\n'
             f'model_provider="deepseek_runtime"\nmodel_catalog_json={json.dumps(catalog)}\n'
             f'sqlite_home={json.dumps(str(home))}\nlog_dir={json.dumps(str(home/"log"))}\n'
             f'model_providers.deepseek_runtime={{name="Owned synthetic",base_url={json.dumps(base)},'
             'env_key="CODEX_DEEPSEEK_BRIDGE_TOKEN",wire_api="responses",supports_websockets=false,'
             'capabilities={remote_compaction="unsupported"}}\n')
        (policy/'config.toml').write_text(cfg)
        # The App native binary reads this catalog and loopback route from
        # CODEX_HOME. The CDS CLI build may also load the runtime-policy copy.
        (home/'config.toml').write_text(cfg + 'sandbox_mode="read-only"\ncheck_for_update_on_startup=false\nweb_search="disabled"\n'
            '[features]\napps=false\nplugins=false\nmemories=false\n[skills]\ninclude_instructions=false\n')
        env={'HOME':os.environ['HOME'],'PATH':'/usr/bin:/bin','LANG':'C.UTF-8',
             'NO_PROXY':'localhost,127.0.0.1,::1','no_proxy':'localhost,127.0.0.1,::1',
             'GIT_CONFIG_GLOBAL':os.devnull,'GIT_CONFIG_NOSYSTEM':'1','GIT_CEILING_DIRECTORIES':str(tmp_path),
             'OTEL_SDK_DISABLED':'true','CODEX_HOME':str(home),'CODEX_DEEPSEEK_HOME':str(home),
             'CODEX_DEEPSEEK_POLICY_HOME':str(home),'CODEX_DEEPSEEK_BRIDGE_PORT':str(client.server.port),
             'CODEX_DEEPSEEK_BRIDGE_TOKEN':TOKEN}
        if os.name == "nt":
            system_root = os.environ["SystemRoot"]
            env.update(SystemRoot=system_root, PATH=str(Path(system_root) / "System32"),
                       HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / "roaming"),
                       LOCALAPPDATA=str(home / "local"), TEMP=str(home / "temp"), TMP=str(home / "temp"))
        async def run(*args):
            proc=await asyncio.create_subprocess_exec(binary,*args,cwd=work,env=env,
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,start_new_session=True)
            try:out,err=await asyncio.wait_for(proc.communicate(),30)
            finally:
                if proc.returncode is None:
                    proc.terminate();await proc.wait()
            return proc.returncode,out.decode(),err.decode()
        seed,seed_out,seed_err=await run('exec','--json','--skip-git-repo-check','--','Seed the owned synthetic task.')
        (tmp_path/"seed.stdout.jsonl").write_text(seed_out, encoding="utf-8")
        (tmp_path/"seed.stderr.txt").write_text(seed_err, encoding="utf-8")
        assert seed==0,(seed_out,seed_err)
        checkpoints=list((home/'cache'/'request-capacity-v1').glob('*.json'))
        assert len(checkpoints)==1
        seeded_checkpoint=checkpoints[0].read_bytes()
        assert json.loads(seeded_checkpoint)['checkpoint']['origin']=='ordinary_completed'
        rc,out,err=await run('exec','--json','--skip-git-repo-check','resume','--last',
                             '--image='+str(image),'--','Continue the owned synthetic task.')
        (tmp_path/'native.stdout.jsonl').write_text(out)
        (tmp_path/'native.stderr.txt').write_text(err)
        events=[json.loads(line) for line in out.splitlines() if line.strip()]
        assert rc!=0 and sum(e.get('type')=='turn.failed' for e in events)==1
        seed_events=[json.loads(line) for line in seed_out.splitlines() if line.strip()]
        assert events[0]['thread_id']==seed_events[0]['thread_id']
        failure=next(e['error']['message'] for e in events if e.get('type')=='turn.failed')
        assert 'Request-byte capacity was exceeded' not in (out+err)
        if error_domain=='files':
            native_error=json.loads(failure)['error']
            assert native_error['code']=='provider_compatibility' and native_error['file_http_status']==413
            assert 'Files API' in native_error['message']
            assert len(backend.uploads)==1 and len(calls)==1
        else:
            assert 'synthetic forbidden' in failure
            # Native may retry the 403. The cached image must not be uploaded again.
            assert len(backend.uploads)==1 and len(calls)>=2
            result=await made[-1].cleanup(all_unpinned=True)
            assert result['pinned']==1 and not result['all_owned_files_deleted']
            assert not backend.deletes
            receipts=[r for r in state.list_receipts() if r['status']=='upstream_error']
            assert len(receipts)==len(calls)-1
            assert all(r['upstream_status']==403 and
                r['adaptations']['image_transport']['consumption_status']=='unknown' for r in receipts)
        assert checkpoints[0].read_bytes()==seeded_checkpoint
        assert all(r['upstream_status'] is None for r in state.list_receipts() if r['status']=='adapter_error')
