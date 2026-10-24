import asyncio,json,time
from email.utils import formatdate
import pytest
from codex_app_deepseek.image_maintenance import ImageMaintenance,ImageMaintenanceError,retry_after_seconds


def result(**updates):
    value={'deleted':2,'pending':0,'pinned':0,'unknown_uploads':0,'unknown_upstream_calls':0,'remaining_assets':0,'retained':0,'foreign_scopes':[],'complete':True,'all_owned_files_deleted':True,'deferred_assets':0}
    value.update(updates);return value


@pytest.mark.asyncio
async def test_bounded_gc_counts_only_and_private_persisted_state(tmp_path):
    now=[1000.0];calls=[]
    class Transport:
        async def cleanup(self,**kwargs):calls.append(kwargs);return result(scope='private-scope',request_id='private-request',file_id='or_file_private')
        async def reconcile_unknown_uploads(self,**kwargs):raise AssertionError('background GC must not list')
    path=tmp_path/'maintenance.json';gc=ImageMaintenance(path,lambda:Transport(),clock=lambda:now[0])
    assert not await gc.run_once() and calls==[]
    now[0]+=60;assert await gc.run_once() and calls==[{'max_assets':2}]
    state=gc.snapshot();assert state['status']=='complete' and state['counts']['deleted']==2
    text=path.read_text('utf-8');assert 'private' not in text and 'or_file' not in text
    assert 'request_id' not in text and 'file_id' not in text
    assert gc.snapshot()['next_eligible_at']==1120


@pytest.mark.asyncio
@pytest.mark.parametrize('status',[409,429])
async def test_retry_after_cooldown_survives_cold_reopen(tmp_path,status):
    now=[1000.0];calls=[]
    class Transport:
        async def cleanup(self,**kwargs):
            calls.append(kwargs);return result(complete=False,pending=1,remaining_assets=3,halted_error={'type':'FileOperationError','http_status':status,'headers':{'retry-after':'300'}})
    path=tmp_path/'maintenance.json';gc=ImageMaintenance(path,lambda:Transport(),clock=lambda:now[0])
    now[0]=1060;await gc.run_once()
    assert gc.snapshot()['next_eligible_at']==1360 and gc.snapshot()['status']=='error'
    restored=ImageMaintenance(path,lambda:Transport(),clock=lambda:now[0])
    now[0]=1359;assert not await restored.run_once() and len(calls)==1
    now[0]=1360;assert await restored.run_once() and len(calls)==2


def test_retry_after_http_date_and_invalid_advice():
    assert retry_after_seconds(formatdate(2000,usegmt=True),1500)==500
    assert retry_after_seconds('0',1000)==0
    with pytest.raises(ImageMaintenanceError):retry_after_seconds('not-a-time',1000)


@pytest.mark.asyncio
async def test_timeout_is_pending_and_does_not_forge_file_cleanup(tmp_path):
    now=[1000.0];cancelled=[]
    class Transport:
        async def cleanup(self,**kwargs):
            try:await asyncio.Future()
            finally:cancelled.append(True)
    gc=ImageMaintenance(tmp_path/'maintenance.json',lambda:Transport(),clock=lambda:now[0],timeout_seconds=.02)
    now[0]+=60;await gc.run_once()
    assert gc.snapshot()['status']=='timed_out' and cancelled==[True]
    assert gc.snapshot()['counts']=={} and gc.snapshot()['next_eligible_at']==1120


@pytest.mark.asyncio
async def test_shutdown_cancels_only_own_background_task(tmp_path):
    started=asyncio.Event();cancelled=asyncio.Event()
    class Transport:
        async def cleanup(self,**kwargs):
            started.set()
            try:await asyncio.Future()
            finally:cancelled.set()
    gc=ImageMaintenance(tmp_path/'maintenance.json',lambda:Transport(),interval_seconds=.02)
    unrelated=asyncio.create_task(asyncio.sleep(10))
    gc.start();await asyncio.wait_for(started.wait(),2);await gc.stop()
    assert cancelled.is_set() and not unrelated.done() and gc.snapshot()['status']=='interrupted'
    unrelated.cancel()
    try:await unrelated
    except asyncio.CancelledError:pass


@pytest.mark.parametrize('mutation',[
    lambda s:s.update(next_eligible_at=float('nan')),
    lambda s:s.update(schema_version=2),
    lambda s:s.update(schema_version=True),
    lambda s:s.update(status=[]),
    lambda s:s.update(unrecognized='value'),
    lambda s:s.update(counts={'file_id':'or_file_private'}),
])
def test_corrupt_maintenance_state_fails_explicitly(tmp_path,mutation):
    path=tmp_path/'maintenance.json';gc=ImageMaintenance(path,lambda:None)
    state=dict(gc.state);mutation(state);path.write_text(json.dumps(state),'utf-8')
    with pytest.raises(ImageMaintenanceError):ImageMaintenance(path,lambda:None)


@pytest.mark.asyncio
async def test_persistence_failure_visible_in_health_not_stale_success(tmp_path,monkeypatch):
    class Transport:
        async def cleanup(self,**kwargs):return result()
    gc=ImageMaintenance(tmp_path/'maintenance.json',lambda:Transport(),interval_seconds=.01)
    def fail(value):raise PermissionError('synthetic read-only state')
    monkeypatch.setattr(gc,'_save',fail);gc.start()
    for _ in range(30):
        if gc.failure:break
        await asyncio.sleep(.01)
    assert gc.snapshot()['status']=='error' and gc.snapshot()['state_persistence_failed']
    await gc.stop()


def test_hardlinked_maintenance_state_is_rejected(tmp_path):
    import os
    path=tmp_path/'maintenance.json';ImageMaintenance(path,lambda:None)
    os.link(path,tmp_path/'linked.json')
    with pytest.raises(ImageMaintenanceError):ImageMaintenance(path,lambda:None)
