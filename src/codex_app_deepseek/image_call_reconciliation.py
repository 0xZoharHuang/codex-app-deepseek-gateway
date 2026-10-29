"""Explicit bounded recovery of image consumption leases; never replays inference.

Only request IDs already pinned in this cache may join this project's schema-3
receipts. Read-only SQLite does not acquire/reconcile ProtocolState ownership.
"""
from __future__ import annotations
from .paths import _is_link_or_reparse, _sqlite_filename
import asyncio
from contextlib import closing
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from codex_deepseek.protocol import MODEL
from .app_config import _atomic_write
from .image_maintenance import retry_after_seconds
from .image_transport import _file_lock, _strict_json, _private_directory
from .upstream_evidence import read_generation_evidence

PROVIDERS = {'deepseek': 'DeepSeek', 'morph/fp8': 'Morph', 'primeintellect': 'PrimeIntellect'}
RESPONSE_ID = re.compile(r'(?:gen|resp)[-_][A-Za-z0-9_-]{1,220}\Z')
HEX = re.compile(r'[0-9a-f]{64}\Z')

class CallReconciliationError(RuntimeError):
    pass


def _regular_path(path: Path, *, existing=False):
    if not path.is_absolute() or any(_is_link_or_reparse(p) for p in (path,*path.parents)):
        raise CallReconciliationError('Reconciliation requires private regular absolute paths')
    if existing and not path.is_file():
        raise CallReconciliationError('Project receipt database is unavailable')
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        raise CallReconciliationError('Reconciliation state cannot be linked')


def read_project_receipt(path: Path, request_ref: str):
    """One bounded selected row; never read prompts/history or construct ProtocolState."""
    path=Path(path);_regular_path(path,existing=True)
    with closing(sqlite3.connect(_sqlite_filename(path, read_only=True),uri=True,timeout=.1)) as db:
        db.row_factory=sqlite3.Row
        if db.execute('PRAGMA user_version').fetchone()[0] != 3:
            raise CallReconciliationError('Unsupported project receipt schema')
        row=db.execute('SELECT id,status,response_id,requested_model,requested_upstream_model,requested_provider,adaptations FROM receipts WHERE id=?',(request_ref,)).fetchone()
    if row is None:return None
    value=dict(row)
    if not isinstance(value['adaptations'],str) or len(value['adaptations'])>1024*1024:
        raise CallReconciliationError('Project adaptation receipt is oversized')
    value['adaptations']=_strict_json(value['adaptations'])
    if not isinstance(value['adaptations'],dict):
        raise CallReconciliationError('Project adaptation receipt is invalid')
    return value


def _load_state(path):
    _regular_path(path)
    if not path.exists():return {'schema_version':1,'scope':None,'cursor':None,'next_eligible_at':0.0}
    if path.stat().st_size>65536:raise CallReconciliationError('Reconciliation state is oversized')
    value=_strict_json(path.read_text('utf-8'))
    if (not isinstance(value,dict) or set(value)!={'schema_version','scope','cursor','next_eligible_at'}
        or type(value['schema_version']) is not int or value['schema_version']!=1
        or any(v is not None and (not isinstance(v,str) or not HEX.fullmatch(v)) for v in (value['scope'],value['cursor']))
        or type(value['next_eligible_at']) not in {int,float} or not math.isfinite(value['next_eligible_at']) or value['next_eligible_at']<0):
        raise CallReconciliationError('Invalid reconciliation state schema')
    return value


def _save_state(path,value):
    _regular_path(path)
    _atomic_write(path,(json.dumps(value,allow_nan=False)+'\n').encode())


def generation_terminal(evidence, receipt):
    """Conservative verified stop/length only; null/error/tool-loop outcomes stay pinned."""
    if not isinstance(evidence,dict) or evidence.get('lookup_status')!='found' or type(evidence.get('inference_requests')) is not int or evidence.get('inference_requests')!=0:
        return None
    if evidence.get('receipt_id')!=receipt['id'] or evidence.get('response_id')!=receipt['response_id']:
        return None
    observed=evidence.get('observed');identity=evidence.get('model_identity');requested=evidence.get('requested')
    if not all(isinstance(v,dict) for v in (observed,identity,requested)):
        return None
    if (requested.get('upstream_model')!=receipt['requested_upstream_model']
        or requested.get('provider')!=receipt['requested_provider']
        or observed.get('id')!=receipt['response_id'] or identity.get('matches_requested') is not True
        or observed.get('provider_name')!=PROVIDERS.get(receipt['requested_provider'])
        or observed.get('cancelled') is not False):
        return None
    if identity.get('status')=='exact_id':
        if observed.get('model')!=MODEL:return None
    elif identity.get('status')=='verified_canonical_alias':
        proof=identity.get('proof');metadata=proof.get('metadata') if isinstance(proof,dict) else None
        if (not isinstance(metadata,dict) or proof.get('http_status')!=200
            or metadata.get('id')!=MODEL or not isinstance(metadata.get('canonical_slug'),str)
            or not metadata['canonical_slug'] or observed.get('model')!=metadata['canonical_slug']
            or not isinstance(proof.get('raw_sha256'),str) or not HEX.fullmatch(proof['raw_sha256'])):
            return None
    else:return None
    # Metadata describing a tool-call subgeneration is not proof that a hosted
    # tool loop ended. Do not derive completion from usage, cost or elapsed time.
    pair=(observed.get('finish_reason'),observed.get('native_finish_reason'))
    return {('stop','stop'):'completed',('length','length'):'incomplete'}.get(pair)


def _persist_proof(state_path,scope,receipt,terminal,evidence):
    # Retain an auditable, private proof before releasing the pin. Deliberately
    # select scalars; never write arbitrary provider payloads or prompt content.
    observed=evidence.get('observed') or {}
    identity=evidence.get('model_identity') or {}
    identity_proof=identity.get('proof') or {}
    metadata=identity_proof.get('metadata') or {}
    proof={'schema_version':1,'credential_scope':scope,'request_ref':receipt['id'],
        'response_id':receipt['response_id'],'terminal':terminal,
        'source':'saved_provider_terminal' if evidence.get('source')=='saved_provider_terminal' else 'generation_metadata',
        'requested_model':receipt['requested_upstream_model'],'requested_provider':receipt['requested_provider'],
        'observed':{name:observed.get(name) for name in ('id','model','provider_name','cancelled','finish_reason','native_finish_reason')},
        'model_identity':{'status':identity.get('status'),'matches_requested':identity.get('matches_requested'),
                         'metadata_sha256':identity_proof.get('raw_sha256'),
                         'model_id':metadata.get('id'),'canonical_slug':metadata.get('canonical_slug')}}
    for section in (proof['observed'],proof['model_identity']):
        if any(value is not None and type(value) not in {str,bool,int,float} for value in section.values()):
            raise CallReconciliationError('Invalid terminal evidence scalar')
    raw=(json.dumps(proof,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()
    digest=hashlib.sha256(raw).hexdigest()
    folder=state_path.with_name(state_path.name+'.proofs');_private_directory(folder)
    path=folder/(digest+'.json');_regular_path(path)
    if path.exists():
        if path.read_bytes()!=raw:raise CallReconciliationError('Reconciliation proof changed')
    else:_atomic_write(path,raw)
    return digest


def _http_error(evidence):
    candidates=list((evidence.get('generation_lookup') or {}).get('attempts') or [])
    proof=(evidence.get('model_identity') or {}).get('proof')
    if isinstance(proof,dict):candidates.append(proof)
    for candidate in candidates:
        status=candidate.get('http_status')
        if type(status) is int and status!=200:
            error=candidate.get('error') or {}
            return status,(error.get('headers') or {}).get('retry-after')
    return None,None


async def reconcile_image_calls(transport, receipt_path: Path, state_path: Path, credential_provider,
        *, max_calls: int=2, timeout_seconds: float=10, proxy_url=None, ssl_context=None,
        generation_reader=read_generation_evidence, clock=time.time):
    """Explicit command; at most two known generation GETs (plus public model GETs).

    Only releases transport pins. TTL cleanup remains a separate bounded action.
    The outer timeout includes locking and metadata calls. A persisted minimum
    60-second cooldown prevents repeated manual calls from hammering the account.
    """
    if type(max_calls) is not int or not 1<=max_calls<=2 or type(timeout_seconds) not in {int,float} or not 0<timeout_seconds<=10:
        raise ValueError('Reconciliation is limited to two calls and ten seconds')
    receipt_path,state_path=Path(receipt_path),Path(state_path)
    _regular_path(state_path);_regular_path(receipt_path,existing=True)
    result={'processed':0,'released':0,'unknown':0,'deferred':0,'service_errors':0,
            'generation_reads':0,'inference_requests':0,'http_status':None,'error_type':None,
            'next_eligible_at':None,'remaining_unknown':None,'status':'pending'}
    try:
        async with asyncio.timeout(timeout_seconds):
            async with _file_lock(state_path.with_suffix(state_path.suffix+'.lock'),min(timeout_seconds,1)):
                state=_load_state(state_path);now=clock()
                result['next_eligible_at']=state['next_eligible_at']
                if now<state['next_eligible_at']:
                    result['status']='cooldown';return result
                credential=credential_provider()
                snapshot=await transport.diagnostics(private=True,credential=credential)
                scope=snapshot['scope']
                if not isinstance(scope,str) or not HEX.fullmatch(scope):raise CallReconciliationError('Invalid image credential scope')
                if state['scope']!=scope:state.update(scope=scope,cursor=None)
                calls=snapshot['unknown_upstream_calls']
                if any(not isinstance(v.get('request_ref'),str) or not v['request_ref'] for v in calls):
                    raise CallReconciliationError('Unknown image lease has no receipt reference')
                refs={v['request_ref'] for v in calls}
                result['remaining_unknown']=len(refs)
                keyed=sorted((hashlib.sha256(ref.encode()).hexdigest(),ref) for ref in refs)
                cursor=state['cursor'] or ''
                ordered=[v for v in keyed if v[0]>cursor]+[v for v in keyed if v[0]<=cursor]
                # Write before GET: process death cannot bypass cooldown or repeat
                # the first unresolved record forever. This stores hashes, no IDs.
                state['next_eligible_at']=now+60;_save_state(state_path,state)
                result['next_eligible_at']=state['next_eligible_at']
                for digest,ref in ordered[:max_calls]:
                    state['cursor']=digest;_save_state(state_path,state)
                    result['processed']+=1
                    receipt=read_project_receipt(receipt_path,ref)
                    if not receipt or receipt['id']!=ref:
                        result['unknown']+=1;continue
                    image=(receipt.get('adaptations') or {}).get('image_transport')
                    if not isinstance(image,dict) or not isinstance(image.get('preparation'),dict) or image['preparation'].get('credential_scope')!=scope:
                        result['unknown']+=1;continue
                    if receipt['requested_upstream_model']!=MODEL or receipt['requested_provider'] not in PROVIDERS:
                        result['unknown']+=1;continue
                    response_id=image.get('provider_response_id') or receipt.get('response_id')
                    if not isinstance(response_id,str) or not RESPONSE_ID.fullmatch(response_id):
                        result['unknown']+=1;continue
                    if receipt.get('response_id') and receipt['response_id']!=response_id:
                        result['unknown']+=1;continue
                    receipt['response_id']=response_id
                    terminal=image.get('provider_terminal') if image.get('consumption_status')=='provider_terminal' else None
                    if terminal not in {'completed','failed','incomplete','cancelled_confirmed'}:terminal=None
                    evidence={'source':'saved_provider_terminal','terminal':terminal}
                    if terminal is None:
                        result['generation_reads']+=1
                        evidence=await generation_reader(receipt,lambda:credential,proxy_url=proxy_url,
                            ssl_context=ssl_context,max_attempts=1,timeout=timeout_seconds)
                        terminal=generation_terminal(evidence,receipt)
                        if evidence.get('lookup_status') not in {'found','not_found','http_error'}:
                            result['service_errors']+=1;result['unknown']+=1
                            result['error_type']='GenerationMetadataUnavailable';break
                        status,advice=_http_error(evidence)
                        if status is not None:
                            result['http_status']=status
                            # 404 may be delayed metadata, never proof of stopping.
                            if status!=404:
                                result['service_errors']+=1
                                delay=retry_after_seconds(advice,clock())
                                state['next_eligible_at']=max(state['next_eligible_at'],clock()+max(60,delay or 0))
                                _save_state(state_path,state);result['next_eligible_at']=state['next_eligible_at']
                                result['unknown']+=1;break
                    if terminal is None:
                        result['unknown']+=1;continue
                    proof_hash=_persist_proof(state_path,scope,receipt,terminal,evidence)
                    await transport.settle_unknown(ref,response_id,terminal,credential=credential,evidence_sha256=proof_hash)
                    result['released']+=1
                    result['remaining_unknown']-=1
                result['deferred']=len(refs)-result['processed']
                result['status']='complete' if not result['unknown'] and not result['deferred'] else 'pending'
                if result['service_errors']:result['status']='service_error'
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        result['status']='timed_out';result['error_type']='TimeoutError'
    except Exception as error:
        # No exception text, request IDs, raw provider error or proxy credential.
        result['status']='error';result['error_type']=type(error).__name__
    return result
