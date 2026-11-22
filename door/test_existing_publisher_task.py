"""The manual/native adapters trigger the installed task; only its snapshot completes a request."""
import asyncio
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import patch
import pytest
import transcript_store as store

RID='a'*32
T0='2026-11-09T22:00:00+00:00'
T1='2026-11-09T22:00:01+00:00'

def ack(**changes):
    return dict(status='requested',request_id=RID,requested_utc=T0,task='Uhura Transcripts',
                task_state='Running',instance_id='existing-task-instance',started_new_instance=True,
                task_last_result=267009,task_last_run_utc=T1,**changes)

def test_long_running_normal_task_does_not_hit_bridge_work_timeout(tmp_path):
    now=[0];calls=[];reader=store.Snapshot(tmp_path)
    async def request(action):calls.append(action);return ack()
    async def sleep(_):
        now[0]+=100
        if now[0]==500:store.commit(tmp_path,{'codex--one.md':'LATEST'}, {},'b'*32,T1,'timer')
    result=asyncio.run(store.wait_for_publication(RID,request,reader.read,sleep=sleep,clock=lambda:now[0]))
    assert result['status']=='completed' and result['request_id']==RID
    assert result['publication_request_id']=='b'*32 and result['publisher_source']=='timer'
    assert result['generation']==result['visible_generation'] and calls.count('trigger')==1

def test_inflight_older_publication_is_not_mistaken_for_requested_freshness(tmp_path):
    old=store.commit(tmp_path,{'codex--one.md':'OLD'}, {},'c'*32,'2026-11-09T21:59:00+00:00','timer')
    now=[0];calls=[];reader=store.Snapshot(tmp_path)
    async def request(action):
        calls.append(action)
        if calls.count('trigger')==1:
            result=ack();result['started_new_instance']=False
            if action=='status':result.update(task_state='Ready',instance_id='',task_last_result=0)
            return result
        store.commit(tmp_path,{'codex--one.md':'FRESH'}, {},'d'*32,T1,'timer')
        return ack()
    async def sleep(_):now[0]+=5
    result=asyncio.run(store.wait_for_publication(RID,request,reader.read,sleep=sleep,clock=lambda:now[0]))
    assert result['generation']!=old['generation'] and result['publication_request_id']=='d'*32
    assert calls.count('trigger')==2

def test_failed_task_is_reported_without_retry_or_empty_snapshot(tmp_path):
    before=store.commit(tmp_path,{'codex--one.md':'KEPT'}, {},'c'*32,'2026-11-09T21:59:00+00:00','timer')
    now=[0];calls=[];reader=store.Snapshot(tmp_path)
    async def request(action):
        calls.append(action);result=ack()
        if action=='status':result.update(task_state='Ready',instance_id='',task_last_result=1)
        return result
    async def sleep(_):now[0]+=5
    with pytest.raises(RuntimeError,match='task failed'):
        asyncio.run(store.wait_for_publication(RID,request,reader.read,sleep=sleep,clock=lambda:now[0]))
    assert calls.count('trigger')==1 and reader.read()['receipt']['generation']==before['generation']

def test_disabled_task_is_never_enabled_or_replaced(tmp_path):
    async def request(action):
        result=ack();result.update(task_state='Disabled',instance_id='');return result
    with pytest.raises(RuntimeError,match='Disabled'):
        asyncio.run(store.wait_for_publication(RID,request,store.Snapshot(tmp_path).read))

def module(name):
    p=Path(__file__).parent/'deploy'/(name+'.py')
    spec=importlib.util.spec_from_file_location(name,p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_fixed_task_call_cannot_register_task_or_launch_another_publisher():
    m=module('transcript_completion')
    response=ack();response.pop('status');response.pop('request_id');response.pop('requested_utc')
    fake=type('Result',(),{'returncode':0,'stdout':json.dumps(response)})()
    with patch.object(m.subprocess,'run',return_value=fake) as run,patch.object(m,'event_log'):
        result=m.request_existing_task(RID,start=True)
    args=run.call_args.args[0];options=run.call_args.kwargs
    assert args==['powershell.exe','-NoProfile','-NonInteractive','-Command','-']
    assert options['input']==m.TASK_SCRIPT and options['env']['UHURA_TRIGGER_START']=='1'
    assert "GetTask('Uhura Transcripts')" in m.TASK_SCRIPT
    for forbidden in ['Register-ScheduledTask','New-ScheduledTask','publish_transcripts.py','Set-ScheduledTask','Enable-ScheduledTask']:
        assert forbidden not in m.TASK_SCRIPT
    assert result['status']=='requested' and result['request_id']==RID

def test_task_exit_zero_without_status_is_not_a_success():
    m=module('transcript_completion')
    fake=type('Result',(),{'returncode':0,'stdout':''})()
    with patch.object(m.subprocess,'run',return_value=fake),pytest.raises(RuntimeError,match='no task status'):
        m.request_existing_task(RID,start=False)

def test_receiver_trigger_and_status_are_only_fixed_task_adapters(monkeypatch,capsys):
    m=module('refresh_via_ssh')
    for verb,start in [('trigger-transcripts',True),('transcript-status',False)]:
        monkeypatch.setenv('SSH_ORIGINAL_COMMAND',verb+' '+RID)
        with patch.object(m,'request_existing_task',return_value=ack()) as request,patch.object(m,'publish_request') as wait:
            assert m.main()==0;request.assert_called_once_with(RID,'mcp',start=start);wait.assert_not_called()
        assert json.loads(capsys.readouterr().out)['status']=='requested'

def test_native_wait_and_manual_wait_share_one_completion_derivation(tmp_path):
    m=module('transcript_completion');sent=[]
    store.commit(tmp_path,{'codex--one.md':'NEW'}, {},'b'*32,T1,'timer')
    with patch.object(m,'OUT',str(tmp_path)),patch.object(m,'request_existing_task',return_value=ack()) as task,patch.object(m,'event_log') as log:
        result=m.publish_request('codex',RID)
    assert result['trigger_source']=='codex' and result['publisher_source']=='timer'
    task.assert_called_once_with(RID,'codex',True)
    assert log.call_args.kwargs['publication_request_id']=='b'*32

def test_liaison_only_forwards_trigger_status_not_publication_work():
    path=Path(__file__).parent/'liaison/liaison.py';spec=importlib.util.spec_from_file_location('liaison_task',path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.SECRET='synthetic-token'
    class Request:
        path='/transcripts/refresh'
        def __init__(self,action):
            body={'request_id':RID}
            if action:body['action']=action
            raw=json.dumps(body).encode();self.headers={'Content-Length':str(len(raw)),'X-Uhura-Liaison':m.SECRET};self.rfile=io.BytesIO(raw)
        def authorized(self):return m.Handler.authorized(self)
        def reply(self,status,body):self.result=(status,body)
    for action in ('trigger','status'):
        r=Request(action)
        with patch.object(m.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(ack()).encode())) as upstream:
            m.Handler.do_POST(r)
        assert r.result[0]==200 and r.result[1]['status']=='requested'
        assert json.loads(upstream.call_args.args[0].data)=={'request_id':RID,'action':action}
        assert upstream.call_args.kwargs['timeout']==20
