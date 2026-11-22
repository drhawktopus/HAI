"""Publication concurrency, atomicity, authenticated completion and legacy access."""
import asyncio
import importlib.util
import io
import urllib.error
import json
import os
from pathlib import Path
import subprocess
import sys
import types
from unittest.mock import patch
import pytest
import uhura
import transcript_store as store
from test_uhura import ENV

def publish(root,files=None,rid='a'*32):
    return store.commit(root,files or {'codex--example.md':'KEPT'}, {'codex':1},rid,store.utc(),'manual')

def test_one_atomic_snapshot_exposes_complete_set_and_legacy_names(tmp_path):
    (tmp_path/'claude--old.md').write_text('LEGACY')
    reader=uhura.TranscriptLibrary(tmp_path)
    assert reader.text('claude--old.md')=='LEGACY'
    first=publish(tmp_path,{'codex--one.md':'ONE','doc--two.md':'TWO'})
    assert set(reader.listing())=={'codex--one.md','doc--two.md'}
    assert reader.text('claude--old.md') is None
    assert reader.text('../secrets') is None and reader.search('ONE',10)[0]['document']=='codex--one.md'
    second=publish(tmp_path,{'codex--three.md':'THREE'})
    assert first['generation']!=second['generation'] and set(reader.listing())=={'codex--three.md'}
    assert (tmp_path/'claude--old.md').read_text()=='LEGACY','old exports retained for rollback, never edited'

def test_commit_failure_leaves_previous_snapshot_whole(tmp_path):
    publish(tmp_path);before=(tmp_path/store.CATALOG).read_bytes()
    with patch.object(store.os,'replace',side_effect=OSError('control failure')):
        with pytest.raises(OSError):publish(tmp_path,{'codex--new.md':'NEW'})
    assert (tmp_path/store.CATALOG).read_bytes()==before
    assert not list(tmp_path.glob('.transcripts-*.tmp'))

def test_invalid_catalog_fails_closed_without_serving_stale_flat_copy(tmp_path):
    (tmp_path/'claude--old.md').write_text('STALE')
    (tmp_path/store.CATALOG).write_text('{partial')
    with pytest.raises(Exception,match='unreadable'):uhura.TranscriptLibrary(tmp_path).listing()

def test_catalog_refuses_arbitrary_paths_and_unfiltered_payload_types(tmp_path):
    for name,body in [('../secret.md','NO'),('plain.md','NO'),('codex--bad.md',{'raw':'NO'})]:
        with pytest.raises(ValueError):publish(tmp_path,{name:body})
    assert not (tmp_path/store.CATALOG).exists()

def test_process_lock_excludes_overlapping_writers_and_releases_after_exit(tmp_path):
    program=('import sys,time;from pathlib import Path;from transcript_store import publication_lock;'
             'root=Path(sys.argv[1]);\nwith publication_lock(root):\n (root/"entered").write_text("yes");time.sleep(1.5)')
    env=dict(os.environ,PYTHONPATH=str(Path(__file__).parent))
    process=subprocess.Popen([sys.executable,'-c',program,str(tmp_path)],env=env)
    try:
        import time
        deadline=time.monotonic()+8
        while not (tmp_path/'entered').exists():
            assert process.poll() is None and time.monotonic()<deadline
            time.sleep(.02)
        with pytest.raises(TimeoutError):
            with store.publication_lock(tmp_path,timeout=.1):pass
        process.wait(timeout=8)
        with store.publication_lock(tmp_path,timeout=.1):pass
    finally:
        if process.poll() is None:process.terminate();process.wait(timeout=8)

def application(tmp_path,monkeypatch,owner=True):
    for key,value in ENV.items():monkeypatch.setenv(key,value)
    for key in ['UHURA_STATE_DIR','UHURA_PUBLISHED_DIR','UHURA_DESK_DIR','UHURA_TRANSCRIPTS_DIR','FASTMCP_HOME']:
        monkeypatch.setenv(key,str(tmp_path))
    monkeypatch.setenv('UHURA_LIAISON_URL','http://liaison.test')
    monkeypatch.setenv('UHURA_LIAISON_SECRET','synthetic-liaison-token')
    monkeypatch.setattr(uhura,'get_access_token',lambda:types.SimpleNamespace(claims={'email':ENV['UHURA_OWNER_EMAIL'] if owner else 'wrong@example.com','email_verified':True}))
    return uhura.build_app()[0]

def tool(mcp):return next(t for t in asyncio.run(mcp.list_tools()) if t.name=='refresh_transcripts')

def test_refresh_requires_existing_verified_owner(tmp_path,monkeypatch):
    mcp=application(tmp_path,monkeypatch,owner=False)
    with patch.object(uhura.urllib.request,'urlopen') as request:
        with pytest.raises(Exception,match='Not authorized'):asyncio.run(tool(mcp).fn())
        request.assert_not_called()

def test_refresh_confirms_actual_visible_completed_publication(tmp_path,monkeypatch):
    mcp=application(tmp_path,monkeypatch)
    def response(request,timeout):
        payload=json.loads(request.data);assert set(payload)=={'request_id'} and timeout==240
        assert request.headers['X-uhura-liaison']=='synthetic-liaison-token'
        receipt=publish(tmp_path,rid=payload['request_id'])
        return io.BytesIO(json.dumps(receipt).encode())
    with patch.object(uhura.urllib.request,'urlopen',side_effect=response):result=asyncio.run(tool(mcp).fn())
    assert result['status']=='completed' and result['generation']==result['visible_generation']
    assert store.Snapshot(tmp_path).read()['receipt']['request_id']==result['request_id']

def test_refresh_owner_waits_for_normal_task_snapshot_not_trigger_ack(tmp_path,monkeypatch):
    mcp=application(tmp_path,monkeypatch)
    def response(request,timeout):
        rid=json.loads(request.data)['request_id']
        started=store.utc()
        publication=store.commit(tmp_path,{'codex--normal-task.md':'NORMAL TASK'}, {},'d'*32,store.utc(),'timer')
        assert request.headers['X-uhura-liaison']=='synthetic-liaison-token'
        return io.BytesIO(json.dumps({'status':'requested','request_id':rid,'requested_utc':started,
            'task':'Uhura Transcripts','task_state':'Running','started_new_instance':True,'instance_id':'normal-task',
            'task_last_result':267009}).encode())
    with patch.object(uhura.urllib.request,'urlopen',side_effect=response):result=asyncio.run(tool(mcp).fn())
    assert result['status']=='completed' and result['publication_request_id']=='d'*32
    assert result['publisher_task']=='Uhura Transcripts' and result['publisher_source']=='timer'
    assert result['generation']==result['visible_generation']


def test_slow_refresh_returns_request_then_same_mcp_action_confirms_without_retrigger(tmp_path,monkeypatch):
    from fastmcp import Client
    mcp=application(tmp_path,monkeypatch);calls=[];cutoff=store.utc()
    monkeypatch.setattr(uhura,'TRANSCRIPT_REFRESH_REPLY_S',.2)
    def response(request,timeout):
        payload=json.loads(request.data);calls.append(payload.get('action','trigger'))
        return io.BytesIO(json.dumps({'status':'requested','request_id':payload['request_id'],
            'requested_utc':cutoff,'task':'Uhura Transcripts','task_state':'Running',
            'started_new_instance':True,'instance_id':'normal-task','task_last_result':267009}).encode())
    async def run():
        async with Client(mcp) as client:
            first=await client.call_tool('refresh_transcripts',{},timeout=2)
            pending=first.data
            assert pending['status']=='requested' and 'generation' not in pending
            receipt=store.commit(tmp_path,{'codex--normal.md':'REAL COMMITTED PIXELS'}, {},'e'*32,store.utc(),'timer')
            # The existing zero-argument interface must work even when a connector
            # still has its old tool schema and drops the new optional argument.
            second=await client.call_tool('refresh_transcripts',{},timeout=2)
            assert second.data['status']=='completed'
            assert second.data['generation']==receipt['generation']==second.data['visible_generation']
            assert second.data['publication_request_id']=='e'*32
            assert second.data['request_id']==pending['request_id']
    with patch.object(uhura.urllib.request,'urlopen',side_effect=response):asyncio.run(run())
    assert calls==['trigger','status'],'Checking an accepted request must not launch a replacement publication'


def test_refresh_request_lookup_is_durable_and_bound_to_the_existing_caller(tmp_path,monkeypatch):
    mcp=application(tmp_path,monkeypatch);monkeypatch.setattr(uhura,'TRANSCRIPT_REFRESH_REPLY_S',.2)
    def response(request,timeout):
        return io.BytesIO(json.dumps({'status':'requested','request_id':json.loads(request.data)['request_id'],
            'requested_utc':store.utc(),'task':'Uhura Transcripts','task_state':'Running',
            'started_new_instance':True,'instance_id':'normal-task','task_last_result':267009}).encode())
    with patch.object(uhura.urllib.request,'urlopen',side_effect=response):pending=asyncio.run(tool(mcp).fn())
    # Rebuild the reader to model a service restart; no new request store or worker.
    restarted=application(tmp_path,monkeypatch)
    original_who=uhura.who
    monkeypatch.setattr(uhura,'who',lambda token:dict(original_who(token),app='different-app'))
    with patch.object(uhura.urllib.request,'urlopen') as request:
        with pytest.raises(Exception,match='No such refresh request'):
            asyncio.run(tool(restarted).fn(request_id=pending['request_id']))
        request.assert_not_called()
    monkeypatch.setattr(uhura,'who',original_who)
    receipt=store.commit(tmp_path,{'codex--normal.md':'LATEST'}, {},'e'*32,store.utc(),'timer')
    with patch.object(uhura.urllib.request,'urlopen',side_effect=response):
        completed=asyncio.run(tool(restarted).fn(request_id=pending['request_id']))
    assert completed['generation']==receipt['generation']


def test_unknown_refresh_request_never_reaches_the_task_adapter(tmp_path,monkeypatch):
    mcp=application(tmp_path,monkeypatch)
    with patch.object(uhura.urllib.request,'urlopen') as request:
        for rid in ('invalid','a'*32):
            with pytest.raises(Exception,match='No such refresh request'):
                asyncio.run(tool(mcp).fn(request_id=rid))
        request.assert_not_called()


def test_default_refresh_finds_only_this_callers_unsettled_request(tmp_path):
    audit=uhura.Audit(tmp_path/'audit.jsonl');identity={'email':'owner@example.com','app':'codex','client_id':'client'}
    first={'request_id':'a'*32};second={'request_id':'b'*32}
    assert audit.pending_refresh(identity) is None
    audit.record('transcript_refresh_requested',**identity,request_id=first['request_id'],ack=first)
    assert audit.pending_refresh(identity)==first
    assert audit.pending_refresh(dict(identity,client_id='other')) is None
    audit.record('transcript_refresh',**identity,request_id=first['request_id'])
    assert audit.pending_refresh(identity) is None
    audit.record('transcript_refresh_requested',**identity,request_id=second['request_id'],ack=second)
    audit.record('transcript_refresh_failed',**identity,request_id=second['request_id'])
    assert audit.pending_refresh(identity) is None

def test_requested_or_invisible_publication_is_never_reported_completed(tmp_path,monkeypatch):
    mcp=application(tmp_path,monkeypatch);monkeypatch.setattr(uhura,'TRANSCRIPT_REFRESH_VISIBLE_S',0)
    def response(request,timeout):
        receipt={'status':'completed','request_id':json.loads(request.data)['request_id'],'generation':'c'*32,
                 'completed_utc':store.utc(),'documents':1,'stats':{}}
        return io.BytesIO(json.dumps(receipt).encode())
    with patch.object(uhura.urllib.request,'urlopen',side_effect=response):
        with pytest.raises(Exception,match='not yet visible'):asyncio.run(tool(mcp).fn())

def test_refresh_owner_upstream_502_reports_publication_failure_not_authorization(tmp_path,monkeypatch):
    mcp=application(tmp_path,monkeypatch)
    def unavailable(request,timeout):
        assert request.headers['X-uhura-liaison']=='synthetic-liaison-token'
        raise urllib.error.HTTPError(request.full_url,502,'Bad Gateway',{},io.BytesIO(b'PRIVATE-REMOTE-DETAIL'))
    with patch.object(uhura.urllib.request,'urlopen',side_effect=unavailable):
        with pytest.raises(Exception,match='Transcript publication did not complete') as error:
            asyncio.run(tool(mcp).fn())
    assert 'PRIVATE-REMOTE-DETAIL' not in str(error.value)
    assert "Doc's side refused" not in str(error.value)

def liaison_module():
    path=Path(__file__).parent/'liaison/liaison.py'
    spec=importlib.util.spec_from_file_location('refresh_liaison',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

def test_liaison_auth_and_fixed_payload_remain_narrow():
    m=liaison_module();m.SECRET='synthetic-token'
    class Request:
        path='/transcripts/refresh';headers={'Content-Length':'2'};rfile=io.BytesIO(b'{}')
        def authorized(self):return m.Handler.authorized(self)
        def reply(self,status,payload):self.result=(status,payload)
    r=Request()
    with patch.object(m.urllib.request,'urlopen') as upstream:
        m.Handler.do_POST(r);assert r.result[0]==401;upstream.assert_not_called()
        r=Request();r.headers={'Content-Length':'2','X-Uhura-Liaison':m.SECRET};r.rfile=io.BytesIO(b'{}')
        m.Handler.do_POST(r);assert r.result[0]==400;upstream.assert_not_called()
