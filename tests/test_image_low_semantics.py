from __future__ import annotations
import base64,copy,json,struct,zlib
import pytest
from codex_app_deepseek.image_transport import ImageTransport,TransportPolicy,PayloadBudgetExceeded,encode_body
from test_image_transport import FakeFiles,png,part,payload

def large_png():
    raw=png()
    text=b"synthetic-padding\0"+b"x"*4096
    chunk=struct.pack("!I",len(text))+b"tEXt"+text+struct.pack("!I",zlib.crc32(b"tEXt"+text)&0xffffffff)
    return raw[:-12]+chunk+raw[-12:]

def container(kind,parts):
    body=payload(parts)
    if kind!="message":
        body["input"]=[{"type":kind,"call_id":"call-preserve-exact","output":[{"type":"input_text","text":"synthetic-before"},*parts,{"type":"input_text","text":"synthetic-after"}]}]
    return body

@pytest.mark.asyncio
@pytest.mark.parametrize("kind",["message","function_call_output","custom_tool_call_output"])
async def test_explicit_low_stays_inline_in_every_native_container(tmp_path,kind):
    backend=FakeFiles();credentials=[]
    def key():credentials.append(True);return "synthetic-no-vault-key"
    tr=ImageTransport(tmp_path/"cache",key,backend,TransportPolicy(1))
    original=container(kind,[part(detail="low")]);before=copy.deepcopy(original)
    async with tr.prepare(original) as prepared:
        assert prepared.payload==before
        assert prepared.body==encode_body(before)
        assert prepared.image_occurrences==1 and prepared.converted_occurrences==0
        assert prepared.receipt()["remaining_inline_images"]==1
    assert original==before and not credentials and not backend.uploaded

@pytest.mark.asyncio
async def test_same_digest_low_and_high_keep_distinct_native_semantics(tmp_path):
    backend=FakeFiles()
    tr=ImageTransport(tmp_path/"cache",lambda:"synthetic-no-vault-key",backend,TransportPolicy(1))
    image=png()
    original=container("custom_tool_call_output",[part(image,"low"),part(image,"high")])
    before=copy.deepcopy(original)
    async with tr.prepare(original) as prepared:
        parts=prepared.payload["input"][0]["output"]
        assert parts[1]==before["input"][0]["output"][1] and "file_id" not in parts[1]
        assert "image_url" not in parts[2] and parts[2]["detail"]=="high"
        assert prepared.payload["input"][0]["call_id"]=="call-preserve-exact"
        assert parts[0]==before["input"][0]["output"][0] and parts[3]==before["input"][0]["output"][3]
        assert prepared.payload["provider"]==before["provider"]
        assert prepared.image_occurrences==2 and prepared.converted_occurrences==1
        assert prepared.receipt()["remaining_inline_images"]==1
        assert len(backend.uploaded)==1 and backend.uploaded[0][1]==image
    assert original==before

@pytest.mark.asyncio
async def test_low_does_not_take_file_slot_from_eligible_high(tmp_path):
    backend=FakeFiles()
    tr=ImageTransport(tmp_path/"cache",lambda:"synthetic-no-vault-key",backend,TransportPolicy(1,max_file_references=1))
    original=payload([part(large_png(),"low"),part(png(),"high")]);before=copy.deepcopy(original)
    async with tr.prepare(original) as prepared:
        parts=prepared.payload["input"][0]["content"]
        assert parts[1]==before["input"][0]["content"][1]
        assert "file_id" in parts[2] and parts[2]["detail"]=="high"
        assert prepared.converted_occurrences==1 and len(backend.uploaded)==1
        assert backend.uploaded[0][1]==png()
    assert original==before

@pytest.mark.asyncio
async def test_low_budget_failure_happens_before_credentials_or_upload(tmp_path):
    backend=FakeFiles();credentials=[]
    def key():credentials.append(True);return "synthetic-no-vault-key"
    tr=ImageTransport(tmp_path/"cache",key,backend,TransportPolicy(1,wire_budget_bytes=1000))
    original=payload([part(large_png(),"low")]);before=copy.deepcopy(original)
    with pytest.raises(PayloadBudgetExceeded):
        async with tr.prepare(original):pytest.fail("explicit low cannot silently become a file")
    assert original==before and not credentials and not backend.uploaded

@pytest.mark.asyncio
@pytest.mark.parametrize("detail",["high","original","auto",None])
async def test_other_native_precision_modes_still_use_files(tmp_path,detail):
    backend=FakeFiles()
    tr=ImageTransport(tmp_path/"cache",lambda:"synthetic-no-vault-key",backend,TransportPolicy(1))
    image=part()
    if detail is None:image.pop("detail")
    else:image["detail"]=detail
    original=payload([image]);before=copy.deepcopy(original)
    async with tr.prepare(original) as prepared:
        after=prepared.payload["input"][0]["content"][1]
        assert "file_id" in after and "image_url" not in after
        assert ("detail" in after)==("detail" in image)
        if detail is not None:assert after["detail"]==detail
        assert prepared.converted_occurrences==1 and len(backend.uploaded)==1
        assert backend.uploaded[0][1]==png()
    assert original==before
