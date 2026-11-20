"""CDS continuation projection must preserve exact all_turns reasoning evidence."""
import copy,json
import pytest

from codex_deepseek.protocol import MODEL,INTERRUPTED_CONTEXT_PREFIX,ProtocolError,encode_sse,prepare
from codex_app_deepseek.bridge import adapt_reasoning_context
from test_bridge import HEADERS,harness,message,read_events,terminal
from aiohttp import web


def body():
    return {'model':MODEL,'reasoning':{'effort':'high','context':'all_turns'},
            'input':[{'type':'reasoning','id':'r1','content':[{'type':'reasoning_text','text':'first'}]},
                     message('answer'),{'type':'message','role':'user','content':'next'},
                     {'type':'reasoning','id':'r2','content':[{'type':'reasoning_text','text':'second'}]},
                     {'type':'message','role':'user','content':'continue'},
                     {'type':'reasoning','id':'r3','content':[{'type':'reasoning_text','text':'third'}]}],
            'tools':[{'type':'function','name':'sample','parameters':{'type':'object'}}]}


def projection(payload):
    text=payload['input'][0]['content'][0]['text']
    assert text.startswith(INTERRUPTED_CONTEXT_PREFIX)
    return json.loads(text[len(INTERRUPTED_CONTEXT_PREFIX):])


def replace_projection(payload,records):
    payload['input'][0]['content'][0]['text']=INTERRUPTED_CONTEXT_PREFIX+json.dumps(records)


def test_all_turns_verifies_mixed_retained_and_moved_reasoning():
    request=body();payload,registry=prepare(request)
    records=projection(payload)
    assert [r['input_index'] for r in records]==[3,5]
    assert adapt_reasoning_context(request,payload,registry=registry)['reasoning_context']['preserved_items']==3


def test_all_turns_rejects_forged_nonreasoning_assistant_in_projection():
    request={'model':MODEL,'reasoning':{'context':'all_turns'},'input':[
        {'type':'message','role':'user','content':'synthetic'},
        {'type':'reasoning','id':'r','content':[{'type':'reasoning_text','text':'legitimate thought'}]},
        message('legitimate prior answer')]}
    payload,registry=prepare(request);records=projection(payload)
    assert [x['input_index'] for x in records]==[1,2]
    records[1]['item']['content'][0]['text']='FORGED PRIOR ASSISTANT CONTENT'
    replace_projection(payload,records)
    with pytest.raises(ProtocolError,match='item identity mismatch'):
        adapt_reasoning_context(request,payload,registry=registry)


def test_all_turns_rejects_dropped_nonreasoning_assistant_in_projection():
    request={'model':MODEL,'reasoning':{'context':'all_turns'},'input':[
        {'type':'message','role':'user','content':'synthetic'},
        {'type':'reasoning','id':'r','content':[{'type':'reasoning_text','text':'legitimate thought'}]},
        message('legitimate first'),{**message('legitimate second'),'id':'msg_second'}]}
    payload,registry=prepare(request);records=projection(payload)
    assert [x['input_index'] for x in records]==[1,2,3]
    records.pop();replace_projection(payload,records)
    with pytest.raises(ProtocolError,match='lost, added or reordered an assistant message'):
        adapt_reasoning_context(request,payload,registry=registry)


def test_all_turns_rejects_added_assistant_outside_projection():
    request=body();payload,registry=prepare(request)
    payload['input'].append(message('FORGED NEW ASSISTANT MESSAGE'))
    with pytest.raises(ProtocolError,match='lost, added or reordered an assistant message'):
        adapt_reasoning_context(request,payload,registry=registry)


def test_all_turns_rejects_assistant_moved_across_user_messages():
    request={'model':MODEL,'reasoning':{'context':'all_turns'},'input':[
        message('FIRST'),{'type':'message','role':'user','content':'u1'},
        {**message('SECOND'),'id':'msg_second'},{'type':'message','role':'user','content':'u2'},
        {'type':'reasoning','id':'r','content':[{'type':'reasoning_text','text':'third'}]}]}
    payload,registry=prepare(request)
    payload['input'][1],payload['input'][3]=payload['input'][3],payload['input'][1]
    with pytest.raises(ProtocolError,match='reordered'):
        adapt_reasoning_context(request,payload,registry=registry)


def test_all_turns_rejects_reasoning_moved_across_user_message():
    request=body();payload,registry=prepare(request)
    # r1 remains before r2, but must not move across the intervening user.
    first=payload['input'].pop(1);payload['input'].insert(3,first)
    with pytest.raises(ProtocolError,match='reordered original history anchors'):
        adapt_reasoning_context(request,payload,registry=registry)


def test_all_turns_preserves_legitimate_assistant_additional_tools_projection():
    request={'model':MODEL,'reasoning':{'context':'all_turns'},'input':[
        {'type':'message','role':'user','content':'synthetic'},
        {'type':'additional_tools','role':'assistant','tools':[]},
        {'type':'reasoning','id':'r','content':[{'type':'reasoning_text','text':'synthetic reasoning'}]},
        {'type':'message','role':'user','content':'continue'}],
        'tools':[{'type':'function','name':'sample','parameters':{'type':'object'}}]}
    payload,registry=prepare(request)
    assert adapt_reasoning_context(request,payload,registry=registry)['reasoning_context']['preserved_items']==1


def test_all_turns_large_image_anchor_does_not_serialize_or_copy_image_again():
    import tracemalloc
    image='data:image/png;base64,'+'A'*(4*1024*1024)
    request={'model':MODEL,'reasoning':{'context':'all_turns'},'input':[
        {'type':'reasoning','id':'r','content':[{'type':'reasoning_text','text':'synthetic'}]},
        {'type':'message','role':'user','content':[{'type':'input_image','image_url':image}]}],
        'tools':[{'type':'function','name':'sample','parameters':{'type':'object'}}]}
    payload,registry=prepare(request)
    tracemalloc.start()
    try:
        assert adapt_reasoning_context(request,payload,registry=registry)['reasoning_context']['preserved_items']==1
        _,peak=tracemalloc.get_traced_memory()
    finally:tracemalloc.stop()
    assert peak<2*1024*1024


def test_all_turns_long_history_does_not_scan_every_original_for_each_anchor(monkeypatch):
    import codex_app_deepseek.bridge as bridge

    request = body()
    history = [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": f"turn {index}"}]}
        for index in range(2048)
    ]
    request["input"][:0] = history
    payload, registry = prepare(request)
    compare = bridge.json_evidence_equal
    comparisons = 0

    def counted(left, right):
        nonlocal comparisons
        comparisons += 1
        return compare(left, right)

    monkeypatch.setattr(bridge, "json_evidence_equal", counted)
    assert adapt_reasoning_context(request, payload, registry=registry)["reasoning_context"]["preserved_items"] == 3
    assert comparisons < 16


def test_unsupported_legacy_cds_cannot_start_bridge(tmp_path,monkeypatch):
    import codex_app_deepseek.bridge as module
    from codex_app_deepseek.protocol_state import ProtocolState
    class LegacyEvents:
        def __init__(self,registry):pass
    monkeypatch.setattr(module,'Events',LegacyEvents)
    with ProtocolState(tmp_path/'protocol.sqlite') as state:
        settings=module.BridgeSettings(token='fixture-token-with-at-least-24-chars')
        with pytest.raises(RuntimeError,match='Incompatible CDS protocol'):
            module.create_app(settings,lambda:'synthetic',state)


@pytest.mark.parametrize('mutation',['drop','duplicate','reorder','text','index','ordinal','wrong_envelope'])
def test_all_turns_rejects_lost_forged_or_reordered_projection(mutation):
    request=body();payload,registry=prepare(request);payload=copy.deepcopy(payload)
    records=projection(payload)
    if mutation=='drop':records.pop()
    elif mutation=='duplicate':records.append(copy.deepcopy(records[-1]))
    elif mutation=='reorder':records.reverse()
    elif mutation=='text':records[0]['item']['content'][0]['text']='tampered'
    elif mutation=='index':records[0]['input_index']=0
    elif mutation=='ordinal':records[0]['reasoning_index']=True
    else:payload['input'][0]['role']='user'
    if mutation!='wrong_envelope':replace_projection(payload,records)
    with pytest.raises(ProtocolError):adapt_reasoning_context(request,payload,registry=registry)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['function','message'])
async def test_snapshot_only_does_not_invent_a_committed_item(tmp_path,kind):
    value=({'type':'function_call','id':'fc','call_id':'call','name':'execute','arguments':'{}'}
           if kind=='function' else message())
    async def handler(request):return web.Response(body=encode_sse(terminal([value])),content_type='text/event-stream')
    async with harness(tmp_path,handler) as (client,state):
        response=await client.post('/v1/responses',headers=HEADERS,json={'model':MODEL,'stream':True,
            'tools':[{'type':'function','name':'execute','parameters':{'type':'object'}}]})
        events=read_events(await response.text())
        assert events[-1]['type']=='response.failed' and 'output_item.done' in events[-1]['response']['error']['message']
        assert not any(x['type'] in ('response.output_item.done','response.completed') for x in events)
        assert state.list_receipts()[0]['status']=='adapter_error'


@pytest.mark.asyncio
async def test_nonstream_reasoning_missing_native_summary_fails_without_attestation(tmp_path):
    invalid={'type':'reasoning','id':'rs_invalid','content':[{'type':'reasoning_text','text':'synthetic'}]}
    async def handler(request):return web.json_response(terminal([invalid,message()])['response'])
    async with harness(tmp_path,handler) as (client,state):
        response=await client.post('/v1/responses',headers=HEADERS,json={'model':MODEL,'input':'Start'})
        assert response.status==502
        assert 'reasoning summary' in (await response.json())['error']['message']
        assert state.list_receipts()[0]['status']=='adapter_error'
