"""Real in-place Doc writes must not make an otherwise valid refresh fail."""
import json,re,threading
from pathlib import Path
import pytest
from test_codex_transcripts import rollout,ui
import publish_transcripts as pt
import publish_library as lib
from transcript_store import Snapshot

OWNER = lib.OWNER_NAME   # the owner's history folder, from the test rules

@pytest.fixture
def stores(tmp_path,monkeypatch):
    claude=tmp_path/'claude/project';claude.mkdir(parents=True)
    for i in range(10):
        (claude/f'{i}.jsonl').write_text('\n'.join(json.dumps(r) for r in [
            {'type':'user','timestamp':'2026-11-07T10:00:00Z','message':{'content':'CLAUDE-QUESTION'}},
            {'type':'assistant','timestamp':'2026-11-07T10:01:00Z','message':{'content':'CLAUDE-ANSWER'}}]))
    history=tmp_path/'history'/OWNER;history.mkdir(parents=True)
    chats=[]
    for i in range(5):
        cid=f'chat-{i}';chats.append({'id':cid,'title':'Guide','created_at':'2026-11-07T10:00:00Z','updated_at':'2026-11-07T10:01:00Z'})
        (history/f'{cid}.json').write_text(json.dumps([{'role':'user','content':'DOC-QUESTION'},{'role':'assistant','content':'DOC-ANSWER'}]))
    (history/'index.json').write_text(json.dumps({'chats':chats}))
    (history.parent/'CodexFixture.json').write_text(json.dumps([{'role':'user','content':'LEGACY-QUESTION'},{'role':'assistant','content':'LEGACY-ANSWER'}]))
    (tmp_path/'rag-hide.json').write_text('{"tasks":[]}')
    home=tmp_path/'codex';home.mkdir()
    rollout(home,[ui({'type':'UserMessage','id':'u','content':'CODEX-QUESTION'}),ui({'type':'AgentMessage','id':'a','content':'CODEX-ANSWER'},2)])
    monkeypatch.setattr(lib,'locker_pattern',lambda:(re.compile('LOCKERSECRETVALUE42'),5))
    out=tmp_path/'published'
    args=[str(out),'--claude',str(claude.parent),'--history',str(history.parent),'--codex',str(home)]
    pt.main(args)
    return tmp_path,out,args

@pytest.mark.parametrize('relative',[f'history/{OWNER}/index.json',f'history/{OWNER}/chat-0.json','history/CodexFixture.json','rag-hide.json'])
def test_refresh_survives_real_partial_source_write(stores,monkeypatch,relative):
    root,out,args=stores;target=root/relative;complete=target.read_bytes()
    ready=threading.Event();read_partial=threading.Event();finished=threading.Event()
    def writer():
        with target.open('wb') as stream:
            stream.write(complete[:8]);stream.flush();ready.set()
            assert read_partial.wait(5)
            stream.seek(0);stream.write(complete);stream.truncate();stream.flush()
        finished.set()
    load=json.load
    def observed(stream,*a,**kw):
        try:return load(stream,*a,**kw)
        except json.JSONDecodeError:
            if Path(stream.name)==target:
                read_partial.set();assert finished.wait(5)
            raise
    worker=threading.Thread(target=writer);worker.start();assert ready.wait(5)
    monkeypatch.setattr(json,'load',observed)
    try:
        receipt=pt.main(args)
        assert receipt['status']=='completed'
        files=Snapshot(out).read()['files']
        assert any('DOC-ANSWER' in files[n] for n in files if n.startswith('doc--'))
        assert any('CODEX-ANSWER' in files[n] for n in files if n.startswith('codex--'))
        assert target.read_bytes()==complete
    finally:
        read_partial.set();worker.join(5);assert not worker.is_alive()

@pytest.mark.parametrize('relative',[f'history/{OWNER}/chat-0.json','rag-hide.json'])
def test_permanently_invalid_source_keeps_prior_atomic_snapshot(stores,monkeypatch,relative):
    root,out,args=stores;before=(out/'transcripts.snapshot.zip').read_bytes()
    (root/relative).write_text('{partial')
    monkeypatch.setattr(pt.time,'sleep',lambda _:None)
    with pytest.raises(Exception):pt.main(args)
    assert (out/'transcripts.snapshot.zip').read_bytes()==before

def test_source_permission_denial_is_not_retried_or_bypassed(monkeypatch):
    import builtins
    calls=[]
    def denied(*a,**kw):calls.append(a);raise PermissionError('denied')
    monkeypatch.setattr(builtins,'open',denied)
    with pytest.raises(PermissionError):pt.read_source_json('protected')
    assert len(calls)==1

def test_source_retry_is_bounded_and_never_returns_empty_fallback(tmp_path,monkeypatch):
    target=tmp_path/'incomplete.json';target.write_text('{')
    delays=[];monkeypatch.setattr(pt.time,'sleep',delays.append)
    with pytest.raises(json.JSONDecodeError):pt.read_source_json(target)
    assert len(delays)==pt.SOURCE_READ_ATTEMPTS-1
    assert sum(delays)==pytest.approx(2.0)
