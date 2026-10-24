import asyncio,copy,hashlib,json,sqlite3
from contextlib import closing
from pathlib import Path
import pytest
from codex_deepseek.protocol import MODEL
from codex_app_deepseek.image_call_reconciliation import reconcile_image_calls,read_project_receipt,generation_terminal,CallReconciliationError
from codex_app_deepseek.image_transport import ImageTransport,TransportPolicy
from test_image_transport import FakeFiles,payload

SCOPE='1'*64
KEY='synthetic-secret-never-real'


def receipt(ref='request-one',**extra):
    value={'id':ref,'status':'owner_lost','response_id':'gen-own-'+ref,'requested_model':MODEL,
      'requested_upstream_model':MODEL,'requested_provider':'morph/fp8',
      'adaptations':{'image_transport':{'preparation':{'credential_scope':SCOPE},'consumption_status':'unknown'}}}
    value.update(extra);return value


def database(path,receipts):
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('PRAGMA user_version=3')
        db.execute('CREATE TABLE receipts(id TEXT PRIMARY KEY,status TEXT,response_id TEXT,requested_model TEXT,requested_upstream_model TEXT,requested_provider TEXT,adaptations TEXT)')
        for value in receipts:
            row=dict(value);row['adaptations']=json.dumps(row['adaptations'])
            db.execute('INSERT INTO receipts VALUES (:id,:status,:response_id,:requested_model,:requested_upstream_model,:requested_provider,:adaptations)',row)
    return path


def evidence(value,**updates):
    result={'lookup_status':'found','inference_requests':0,'receipt_id':value['id'],'response_id':value['response_id'],
      'requested':{'upstream_model':MODEL,'provider':value['requested_provider']},
      'observed':{'id':value['response_id'],'provider_name':'Morph','model':MODEL,'cancelled':False,'finish_reason':'stop','native_finish_reason':'stop'},
      'model_identity':{'matches_requested':True,'status':'exact_id'},'generation_lookup':{'attempts':[{'http_status':200}]}}
    result.update(updates);return result


class Transport:
    def __init__(self,refs):self.refs=list(refs);self.settled=[];self.keys=[]
    async def diagnostics(self,**kwargs):
        self.keys.append(kwargs['credential'])
        return {'scope':SCOPE,'unknown_upstream_calls':[{'request_ref':ref} for ref in self.refs]}
    async def settle_unknown(self,*args,**kwargs):
        self.settled.append((args,kwargs));self.refs.remove(args[0])


@pytest.mark.asyncio
async def test_saved_terminal_joins_scope_and_reads_schema3_without_reconciling_owner(tmp_path):
    value=receipt();value['status']='running'
    value['adaptations']['image_transport'].update(consumption_status='provider_terminal',provider_terminal='completed',provider_response_id=value['response_id'])
    db=database(tmp_path/'receipts.sqlite',[value]);transport=Transport([value['id']])
    async def forbidden(*a,**kw):raise AssertionError('saved terminal needs no network')
    result=await reconcile_image_calls(transport,db,tmp_path/'calls.json',lambda:KEY,generation_reader=forbidden)
    assert result['released']==1 and result['status']=='complete' and result['generation_reads']==0
    assert read_project_receipt(db,value['id'])['status']=='running'
    assert transport.keys==[KEY] and transport.settled[0][1]['credential']==KEY
    assert len(transport.settled[0][1]['evidence_sha256'])==64
    assert value['id'] not in json.dumps(result) and value['response_id'] not in json.dumps(result)
    assert KEY not in json.dumps(result) and SCOPE not in json.dumps(result)


@pytest.mark.asyncio
async def test_generation_one_get_contract_captured_key_and_proxy(tmp_path):
    value=receipt();db=database(tmp_path/'receipts.sqlite',[value]);transport=Transport([value['id']]);calls=[];keys=[]
    def credentials():keys.append(True);return KEY if len(keys)==1 else 'rotated-key'
    async def reader(r,credential,**kwargs):
        calls.append(kwargs);assert credential()==KEY;return evidence(r)
    result=await reconcile_image_calls(transport,db,tmp_path/'calls.json',credentials,proxy_url='http://127.0.0.1:8123',generation_reader=reader)
    assert result['released']==1 and result['generation_reads']==1 and len(keys)==1
    assert calls[0]['max_attempts']==1 and calls[0]['timeout']==10 and calls[0]['proxy_url']=='http://127.0.0.1:8123'
    assert transport.settled[0][1]['credential']==KEY


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['no_receipt','no_scope','wrong_scope','no_id','wrong_model','wrong_provider','conflicting_id'])
async def test_unjoined_or_ambiguous_receipt_never_calls_network(tmp_path,change):
    value=receipt()
    if change=='no_scope':value['adaptations']={}
    if change=='wrong_scope':value['adaptations']['image_transport']['preparation']['credential_scope']='2'*64
    if change=='no_id':value['response_id']=None
    if change=='wrong_model':value['requested_upstream_model']='other/model'
    if change=='wrong_provider':value['requested_provider']='unknown'
    if change=='conflicting_id':value['adaptations']['image_transport']['provider_response_id']='gen-other'
    db=database(tmp_path/'receipts.sqlite',[] if change=='no_receipt' else [value]);transport=Transport([value['id']])
    async def forbidden(*a,**kw):raise AssertionError('unjoined metadata')
    result=await reconcile_image_calls(transport,db,tmp_path/'calls.json',lambda:KEY,generation_reader=forbidden)
    assert result['unknown']==1 and result['released']==0 and result['generation_reads']==0 and not transport.settled


@pytest.mark.parametrize('mutation', [
 lambda e:e['observed'].update(finish_reason=None),
 lambda e:e['observed'].update(native_finish_reason=None),
 lambda e:e['observed'].update(finish_reason='tool_calls',native_finish_reason='tool_calls'),
 lambda e:e['observed'].update(native_finish_reason='length'),
 lambda e:e['observed'].update(cancelled=True),
 lambda e:e['observed'].update(provider_name='DeepSeek'),
 lambda e:e['observed'].update(id='gen-other'),
 lambda e:e['model_identity'].update(matches_requested=None),
 lambda e:e['requested'].update(provider='deepseek'),
 lambda e:e.update(response_id='gen-other'),
 lambda e:e.update(receipt_id='other'),
 lambda e:e.update(inference_requests=1),
])
def test_unknown_or_mismatched_generation_not_terminal(mutation):
    value=receipt();data=evidence(value);mutation(data);assert generation_terminal(data,value) is None


def test_length_is_terminal_consumption_but_not_completed_response():
    value=receipt();data=evidence(value);data['observed'].update(finish_reason='length',native_finish_reason='length')
    assert generation_terminal(data,value)=='incomplete'


@pytest.mark.asyncio
@pytest.mark.parametrize('status',[409,429,500])
async def test_service_failure_stops_round_and_persists_retry_after(tmp_path,status):
    values=[receipt('one'),receipt('two')];db=database(tmp_path/'receipts.sqlite',values);transport=Transport([v['id'] for v in values]);calls=[];now=[1000.0]
    async def reader(value,*a,**kw):
        calls.append(value['id']);return evidence(value,lookup_status='http_error',observed=None,generation_lookup={'attempts':[{'http_status':status,'error':{'headers':{'retry-after':'300'}}}]})
    state=tmp_path/'calls.json'
    result=await reconcile_image_calls(transport,db,state,lambda:KEY,generation_reader=reader,clock=lambda:now[0])
    assert len(calls)==1 and result['service_errors']==1 and result['deferred']==1 and result['next_eligible_at']==1300
    now[0]=1299
    result=await reconcile_image_calls(transport,db,state,lambda:KEY,generation_reader=reader,clock=lambda:now[0])
    assert result['status']=='cooldown' and len(calls)==1


@pytest.mark.asyncio
async def test_no_id_does_not_starve_later_calls_and_only_two_per_round(tmp_path):
    values=[receipt(str(i),response_id=None) for i in range(5)];db=database(tmp_path/'receipts.sqlite',values);transport=Transport([v['id'] for v in values]);state=tmp_path/'calls.json';now=[1000.]
    seen=[]
    for _ in range(3):
        result=await reconcile_image_calls(transport,db,state,lambda:KEY,clock=lambda:now[0])
        assert result['processed']==2 and result['deferred']==3 and result['released']==0
        seen.append(json.loads(state.read_text())['cursor']);now[0]+=60
    assert len(set(seen))==3


@pytest.mark.asyncio
async def test_timeout_keeps_unknown_and_persisted_cooldown(tmp_path):
    value=receipt();db=database(tmp_path/'receipts.sqlite',[value]);transport=Transport([value['id']]);calls=[]
    async def reader(*a,**kw):calls.append(True);await asyncio.Future()
    result=await reconcile_image_calls(transport,db,tmp_path/'calls.json',lambda:KEY,generation_reader=reader,timeout_seconds=.03)
    assert result['status']=='timed_out' and not transport.settled and calls==[True]
    assert json.loads((tmp_path/'calls.json').read_text())['next_eligible_at']>0


@pytest.mark.asyncio
async def test_state_write_failure_prevents_get_and_release(tmp_path,monkeypatch):
    value=receipt();db=database(tmp_path/'receipts.sqlite',[value]);transport=Transport([value['id']]);calls=[]
    def fail(*a):raise PermissionError('PRIVATE-secret')
    monkeypatch.setattr('codex_app_deepseek.image_call_reconciliation._atomic_write',fail)
    async def reader(*a,**kw):calls.append(True)
    result=await reconcile_image_calls(transport,db,tmp_path/'calls.json',lambda:KEY,generation_reader=reader)
    assert result['status']=='error' and result['error_type']=='PermissionError' and not calls and not transport.settled
    assert 'PRIVATE' not in json.dumps(result)


@pytest.mark.asyncio
async def test_actual_image_pin_settles_only_after_verified_own_receipt(tmp_path):
    backend=FakeFiles();tr=ImageTransport(tmp_path/'cache',lambda:KEY,backend,TransportPolicy(1))
    async with tr.prepare(payload()) as prepared:
        scope=prepared.scope;await prepared.mark_upstream_started('own-call')
    value=receipt('own-call');value['adaptations']['image_transport']['preparation']['credential_scope']=scope
    db=database(tmp_path/'receipts.sqlite',[value])
    async def reader(r,*a,**kw):return evidence(r)
    result=await reconcile_image_calls(tr,db,tmp_path/'calls.json',lambda:KEY,generation_reader=reader)
    assert result['released']==1 and not (await tr.diagnostics())['unknown_upstream_calls']
    cleanup=await tr.cleanup(all_unpinned=True)
    assert cleanup['deleted']==1 and cleanup['all_owned_files_deleted'] and not backend.files


def test_read_only_receipt_cannot_create_missing_db_or_accept_wrong_schema(tmp_path):
    path=tmp_path/'missing.sqlite'
    with pytest.raises(CallReconciliationError):read_project_receipt(path,'one')
    assert not path.exists()
    database(path,[receipt()])
    with closing(sqlite3.connect(path)) as db, db:db.execute('PRAGMA user_version=4')
    with pytest.raises(CallReconciliationError):read_project_receipt(path,'one')


def test_sanitized_actual_own_generation_schema_fixture():
    # Captured from our 2026-10-22 small Morph probe; IDs replaced, no network.
    value=receipt();data=evidence(value)
    data['observed'].update(api_type='completions',model='deepseek/deepseek-v4.1-flash-20261017',cancelled=False,finish_reason='stop',native_finish_reason='stop')
    data['model_identity'].update(status='verified_canonical_alias',matches_requested=True,proof={'http_status':200,'raw_sha256':'a'*64,'metadata':{'id':MODEL,'canonical_slug':'deepseek/deepseek-v4.1-flash-20261017'}})
    assert generation_terminal(data,value)=='completed'


@pytest.mark.asyncio
async def test_private_terminal_proof_written_before_release_and_no_provider_prompt(tmp_path):
    value=receipt();db=database(tmp_path/'receipts.sqlite',[value]);tr=Transport([value['id']]);state=tmp_path/'calls.json'
    async def reader(r,*a,**kw):
        data=evidence(r);data['prompt']='NEVER-PERSIST-PROMPT';data['observed']['prompt']='NEVER-PERSIST-PROMPT';return data
    result=await reconcile_image_calls(tr,db,state,lambda:KEY,generation_reader=reader)
    digest=tr.settled[0][1]['evidence_sha256'];proof=state.with_name(state.name+'.proofs')/(digest+'.json')
    raw=proof.read_bytes();assert hashlib.sha256(raw).hexdigest()==digest
    document=json.loads(raw);assert document['request_ref']==value['id'] and document['response_id']==value['response_id']
    assert b'NEVER-PERSIST' not in raw and KEY.encode() not in raw and result['remaining_unknown']==0


@pytest.mark.asyncio
async def test_proof_write_failure_keeps_pin_and_next_round_can_recover(tmp_path,monkeypatch):
    from codex_app_deepseek import image_call_reconciliation as module
    value=receipt();db=database(tmp_path/'receipts.sqlite',[value]);tr=Transport([value['id']]);state=tmp_path/'calls.json';now=[1000.]
    async def reader(r,*a,**kw):return evidence(r)
    original=module._atomic_write
    def fail(path,data):
        if path.parent.name.endswith('.proofs'):raise PermissionError('read-only proof directory')
        return original(path,data)
    monkeypatch.setattr(module,'_atomic_write',fail)
    result=await reconcile_image_calls(tr,db,state,lambda:KEY,generation_reader=reader,clock=lambda:now[0])
    assert result['status']=='error' and result['remaining_unknown']==1 and not tr.settled
    monkeypatch.setattr(module,'_atomic_write',original);now[0]+=60
    result=await reconcile_image_calls(tr,db,state,lambda:KEY,generation_reader=reader,clock=lambda:now[0])
    assert result['released']==1 and result['remaining_unknown']==0


@pytest.mark.asyncio
async def test_generation_transport_error_stops_round_safe_summary(tmp_path):
    values=[receipt('one'),receipt('two')];db=database(tmp_path/'receipts.sqlite',values);tr=Transport([v['id'] for v in values]);calls=[]
    async def reader(*a,**kw):calls.append(True);return {'lookup_status':'transport_error','error':{'detail':KEY}}
    result=await reconcile_image_calls(tr,db,tmp_path/'calls.json',lambda:KEY,generation_reader=reader)
    assert len(calls)==1 and result['service_errors']==1 and result['remaining_unknown']==2 and not tr.settled
    assert result['error_type']=='GenerationMetadataUnavailable' and KEY not in json.dumps(result)


@pytest.mark.asyncio
async def test_404_and_null_finish_remain_pinned_without_replay(tmp_path):
    values=[receipt('one'),receipt('two')];db=database(tmp_path/'receipts.sqlite',values);tr=Transport([v['id'] for v in values]);calls=[]
    async def reader(r,*a,**kw):
        calls.append(True)
        if len(calls)==1:return evidence(r,lookup_status='not_found',observed=None,generation_lookup={'attempts':[{'http_status':404}]})
        result=evidence(r);result['observed'].update(finish_reason=None,native_finish_reason=None);return result
    result=await reconcile_image_calls(tr,db,tmp_path/'calls.json',lambda:KEY,generation_reader=reader)
    assert len(calls)==2 and result['released']==0 and result['unknown']==2 and result['remaining_unknown']==2 and not tr.settled


@pytest.mark.parametrize('case',['model_without_identity','alias_without_proof','alias_wrong_model','alias_bad_digest'])
def test_claimed_model_match_must_have_matching_exact_or_alias_evidence(case):
    value=receipt();data=evidence(value)
    if case=='model_without_identity':data['observed']['model']='other/model'
    else:
        data['observed']['model']='canonical/model'
        data['model_identity'].update(status='verified_canonical_alias',proof={'http_status':200,'raw_sha256':'a'*64,'metadata':{'id':MODEL,'canonical_slug':'canonical/model'}})
        if case=='alias_without_proof':data['model_identity'].pop('proof')
        if case=='alias_wrong_model':data['model_identity']['proof']['metadata']['id']='other/model'
        if case=='alias_bad_digest':data['model_identity']['proof']['raw_sha256']='invalid'
    assert generation_terminal(data,value) is None
