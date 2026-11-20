"""Native peer deliveries survive CDS projection exactly once and in place."""
import copy,json
import pytest
from codex_deepseek.protocol import MODEL,ProtocolError,prepare
from codex_app_deepseek.bridge import adapt_reasoning_context


def request(*,reasoning=True):
    payload={'model':MODEL,'client_metadata':{'x-codex-turn-metadata':json.dumps({'request_kind':'prewarm'})},
             'tools':[{'type':'function','name':'sample','parameters':{'type':'object'}}],
             'input':[{'type':'message','role':'user','content':'before'},
                      {'type':'agent_message','id':'peer_1','author':'child','recipient':'parent',
                       'content':[{'type':'input_text','text':'synthetic child result'}]},
                      {'type':'message','role':'user','content':'after'},
                      {'type':'reasoning','id':'r','summary':[],
                       'content':[{'type':'reasoning_text','text':'synthetic thought'}]}]}
    if reasoning:payload['reasoning']={'context':'all_turns'}
    return payload


@pytest.mark.parametrize('all_turns',[True,False])
def test_valid_peer_projection_passes_with_and_without_all_turns(all_turns):
    body=request(reasoning=all_turns);payload,registry=prepare(body)
    assert len(registry.peer_history_bindings)==1
    assert adapt_reasoning_context(body,payload,registry=registry)==(
        {'reasoning_context':{'requested':'all_turns','mapping':'native_reasoning_replay','preserved_items':1}} if all_turns else {})


@pytest.mark.parametrize('mutation',['drop','move','content','frame','duplicate','missing_binding','forged_digest'])
def test_peer_projection_rejects_missing_forged_or_moved_delivery(mutation):
    body=request();payload,registry=prepare(body);payload=copy.deepcopy(payload)
    index=registry.peer_history_bindings[0]['prepared_input_index']
    if mutation=='drop':payload['input'].pop(index)
    elif mutation=='move':payload['input'][index],payload['input'][index+1]=payload['input'][index+1],payload['input'][index]
    elif mutation=='content':payload['input'][index]['content'][1]['text']='forged'
    elif mutation=='frame':payload['input'][index]['content'][0]['text']='{}'
    elif mutation=='duplicate':payload['input'].insert(index,copy.deepcopy(payload['input'][index]))
    elif mutation=='missing_binding':registry.peer_history_bindings=[]
    elif mutation=='forged_digest':registry.peer_history_bindings[0]['native_peer_sha256']='0'*64
    with pytest.raises(ProtocolError,match='Native peer history'):
        adapt_reasoning_context(body,payload,registry=registry)


def test_equal_original_user_message_and_repeated_peer_delivery_are_preserved():
    body=request();first,first_registry=prepare(body)
    peer_wire=copy.deepcopy(first['input'][first_registry.peer_history_bindings[0]['prepared_input_index']])
    peer=copy.deepcopy(body['input'][1]);peer['id']='peer_2'
    body['input'].insert(2,peer_wire)
    body['input'].insert(3,peer)
    payload,registry=prepare(body)
    assert len(registry.peer_history_bindings)==2
    assert adapt_reasoning_context(body,payload,registry=registry)['reasoning_context']['preserved_items']==1


@pytest.mark.asyncio
@pytest.mark.parametrize('corrupt',[False,True])
async def test_bridge_peer_projection_is_checked_before_upstream(tmp_path,monkeypatch,corrupt):
    from aiohttp import web
    import codex_app_deepseek.bridge as bridge
    from test_bridge import HEADERS,harness,terminal
    seen=[]
    async def upstream(incoming):
        seen.append(await incoming.json())
        return web.json_response(terminal()['response'])
    if corrupt:
        original_prepare=bridge.prepare
        def drop_peer(*args,**kwargs):
            payload,registry=original_prepare(*args,**kwargs)
            index=registry.peer_history_bindings[0]['prepared_input_index']
            payload['input'].pop(index)
            return payload,registry
        monkeypatch.setattr(bridge,'prepare',drop_peer)
    async with harness(tmp_path,upstream) as (client,state):
        response=await client.post('/v1/responses',json=request(),headers=HEADERS)
        if corrupt:
            assert response.status==400 and not seen and state.list_receipts()==[]
            assert 'Native peer history' in await response.text()
        else:
            assert response.status==200 and len(seen)==1
            assert any('synthetic child result' in json.dumps(item) for item in seen[0]['input'])
            assert state.list_receipts()[0]['status']=='completed'


def test_original_user_equal_to_additional_tools_projection_remains_distinct():
    extra={'type':'additional_tools','role':'user','tools':[]}
    plain={'type':'message','role':'user','content':[{'type':'input_text','text':json.dumps(extra,ensure_ascii=False)}]}
    body=request();body['input'].insert(0,plain);body['input'].insert(0,extra)
    payload,registry=prepare(body)
    assert adapt_reasoning_context(body,payload,registry=registry)['reasoning_context']['preserved_items']==1


def test_many_peer_deliveries_only_compare_each_bound_frame_and_content(monkeypatch):
    import codex_app_deepseek.bridge as bridge

    body = request(reasoning=False)
    original = body['input'][1]
    body['input'][1:2] = [{**original, 'id': f'peer_{index}',
                          'content': [{'type': 'input_text', 'text': f'result {index}'}]}
                         for index in range(512)]
    payload, registry = prepare(body)
    compare = bridge.json_evidence_equal
    comparisons = 0

    def counted(left, right):
        nonlocal comparisons
        comparisons += 1
        return compare(left, right)

    monkeypatch.setattr(bridge, 'json_evidence_equal', counted)
    assert adapt_reasoning_context(body, payload, registry=registry) == {}
    assert comparisons == 2 * len(registry.peer_history_bindings) == 1024
