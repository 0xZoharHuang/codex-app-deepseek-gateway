"""Bounded, image-file-only TTL maintenance for the gateway HTTP lifetime."""
from __future__ import annotations
import asyncio
from contextlib import suppress
from datetime import timezone
from email.utils import parsedate_to_datetime
import json
import math
from pathlib import Path
import time
from .app_config import _atomic_write

COUNTS = {'deleted','pending','pinned','unknown_uploads','unknown_upstream_calls',
          'retained','remaining_assets','foreign_scope_count'}
STATES = {'waiting','running','complete','pending','error','timed_out','interrupted'}
FIELDS = {'schema_version','status','last_attempt_at','last_finished_at','next_eligible_at',
          'counts','http_status','error_type','retry_after_seconds'}


class ImageMaintenanceError(RuntimeError):
    pass


def _decode(raw: str) -> dict:
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ImageMaintenanceError('Duplicate image maintenance state field')
            result[key]=value
        return result
    def constant(value):raise ImageMaintenanceError('Nonfinite image maintenance state')
    try:value=json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)
    except (ValueError,TypeError) as error:raise ImageMaintenanceError('Invalid image maintenance state JSON') from None
    if not isinstance(value,dict) or set(value)!=FIELDS or type(value.get('schema_version')) is not int or value.get('schema_version')!=1 or not isinstance(value.get('status'),str) or value.get('status') not in STATES:
        raise ImageMaintenanceError('Unsupported image maintenance state schema')
    for name in ('last_attempt_at','last_finished_at','next_eligible_at','retry_after_seconds'):
        v=value[name]
        if v is not None and (type(v) not in {float,int} or not math.isfinite(v) or v<0):
            raise ImageMaintenanceError('Invalid image maintenance time')
    if value['next_eligible_at'] is None:raise ImageMaintenanceError('Missing maintenance eligibility time')
    if not isinstance(value['counts'],dict) or not set(value['counts'])<=COUNTS or any(type(v) is not int or v<0 for v in value['counts'].values()):
        raise ImageMaintenanceError('Invalid image maintenance counts')
    if value['http_status'] is not None and (type(value['http_status']) is not int or not 100<=value['http_status']<=599):
        raise ImageMaintenanceError('Invalid image maintenance HTTP status')
    if value['error_type'] is not None and (not isinstance(value['error_type'],str) or not value['error_type'].isidentifier()):
        raise ImageMaintenanceError('Invalid maintenance failure category')
    return value


def retry_after_seconds(value: str | None, now: float) -> float | None:
    if value is None:return None
    if not isinstance(value,str) or not value.strip():raise ImageMaintenanceError('Invalid Retry-After advice')
    value=value.strip()
    if value.isascii() and value.isdigit():delay=float(value)
    else:
        try:
            stamp=parsedate_to_datetime(value)
            if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
            delay=max(0,stamp.timestamp()-now)
        except (ValueError,TypeError,OverflowError):raise ImageMaintenanceError('Invalid Retry-After advice') from None
    if not math.isfinite(delay):raise ImageMaintenanceError('Nonfinite Retry-After advice')
    return delay


class ImageMaintenance:
    def __init__(self,path: Path,factory,*,interval_seconds: float=60,timeout_seconds: float=10,max_assets: int=2,clock=time.time):
        if any(type(v) not in {float,int} or not math.isfinite(v) or v<=0 for v in (interval_seconds,timeout_seconds)) or type(max_assets) is not int or not 1<=max_assets<=2:
            raise ValueError('Image maintenance limits are invalid')
        self.path=Path(path)
        if not self.path.is_absolute() or any(p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()) for p in (self.path,*self.path.parents)):
            raise ImageMaintenanceError('Image maintenance state must be a private regular path')
        self.factory=factory;self.interval=interval_seconds;self.timeout=timeout_seconds;self.max_assets=max_assets;self.clock=clock
        self.task=None;self.failure=None
        if self.path.exists():
            if self.path.stat().st_nlink != 1 or self.path.stat().st_size > 65536:
                raise ImageMaintenanceError('Image maintenance state is linked or oversized')
            self.state=_decode(self.path.read_text('utf-8'))
        else:
            self.state={'schema_version':1,'status':'waiting','last_attempt_at':None,'last_finished_at':None,
                'next_eligible_at':clock()+interval_seconds,'counts':{},'http_status':None,'error_type':None,'retry_after_seconds':None}
            self._save(self.state)

    def _save(self,value):
        # Validate our own output too; no private asset/request IDs are admitted.
        raw=json.dumps(value,ensure_ascii=False,allow_nan=False)
        _decode(raw)
        _atomic_write(self.path,(raw+'\n').encode('utf-8'))
        self.state=value

    def snapshot(self):
        result=dict(self.state)
        result['counts']=dict(result['counts'])
        result['enabled']=True
        if self.failure:
            result['status']='error';result['error_type']=self.failure
            result['state_persistence_failed']=True
        return result

    async def run_once(self):
        now=self.clock()
        if now<self.state['next_eligible_at']:return False
        running={**self.state,'status':'running','last_attempt_at':now,'error_type':None,'http_status':None,'retry_after_seconds':None}
        self._save(running)
        result=None;error_type=None;status='pending';http_status=None;advice=None
        try:
            async with asyncio.timeout(self.timeout):
                transport=self.factory()
                result=await transport.cleanup(max_assets=self.max_assets)
            halted=result.get('halted_error') or {}
            http_status=halted.get('http_status')
            error_type=halted.get('type')
            advice=retry_after_seconds((halted.get('headers') or {}).get('retry-after'),self.clock())
            status='complete' if result.get('complete') and not result.get('deferred_assets') else 'pending'
            if halted:status='error'
        except asyncio.CancelledError:
            self._save({**running,'status':'interrupted','last_finished_at':self.clock(),'next_eligible_at':self.clock()+self.interval,'error_type':'CancelledError'})
            raise
        except TimeoutError:
            status='timed_out';error_type='TimeoutError'
        except Exception as error:
            status='error';error_type=type(error).__name__
        counts={}
        if isinstance(result,dict):
            for name in COUNTS-{'foreign_scope_count'}:
                if name in result and type(result[name]) is int and result[name]>=0:counts[name]=result[name]
            counts['foreign_scope_count']=len(result.get('foreign_scopes') or [])
        end=self.clock()
        self._save({**running,'status':status,'last_finished_at':end,'next_eligible_at':end+max(self.interval,advice or 0),
                    'counts':counts,'http_status':http_status,'error_type':error_type,'retry_after_seconds':advice})
        return True

    async def _run(self):
        try:
            while True:
                await asyncio.sleep(max(0,self.state['next_eligible_at']-self.clock()))
                await self.run_once()
        except asyncio.CancelledError:raise
        except Exception as error:
            # Persistence failure is visible in health; never retain a stale ready claim.
            self.failure=type(error).__name__

    def start(self):
        if self.task is not None:raise ImageMaintenanceError('Image maintenance already started')
        self.task=asyncio.create_task(self._run(),name='gateway-image-file-maintenance')

    async def stop(self):
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):await self.task
