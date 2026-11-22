"""Offline native-Codex ingestion, privacy and interface regressions."""
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import pytest

sys.path.insert(0, str(Path(__file__).parent / 'deploy'))
import codex_transcripts as cx
import publish_transcripts as pt
import publish_library as lib

SID = '11111111-1111-4111-8111-111111111111'
CHILD = '22222222-2222-4222-8222-222222222222'


@pytest.fixture
def home(tmp_path, monkeypatch):
    p = tmp_path / 'codex'; p.mkdir()
    monkeypatch.setattr(lib, 'LOCKER', re.compile('LOCKERSECRETVALUE42'))
    return p


def rollout(home, records, archived=False, sid=SID):
    folder = home / ('archived_sessions' if archived else 'sessions/2026/11/08')
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ('rollout-' + sid + '.jsonl')
    meta = {'timestamp':'2026-11-08T10:00:00Z','type':'session_meta','ordinal':0,
            'payload':{'id':sid,'timestamp':'2026-11-08T10:00:00Z','cwd':'/code/project',
                       'source':'cli','originator':'Codex Desktop','cli_version':'fixture'}}
    path.write_bytes(('\n'.join(json.dumps(r) for r in [meta]+records)+'\n').encode())
    return path


def ui(item, ordinal=1, turn='turn-a'):
    return {'timestamp':'2026-11-08T10:00:01Z','ordinal':ordinal,'type':'event_msg','payload':{
        'type':'item_completed','turn_id':turn,'started_at_ms':1794132000000,
        'completed_at_ms':1794132001000,'item':item}}


def build(home, hidden=()):
    return cx.build(home,pt.clean_counted,pt.safe_title,pt.PERSON_SESSION_LIMIT,hidden)


def text_of(home):
    files, stats=build(home)
    assert stats['codex']==1
    return next(iter(files.values()))


def state_db(home, ids=(SID,)):
    path=home/'state_5.sqlite'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE threads(id TEXT, source TEXT, thread_source TEXT, title TEXT, created_at INTEGER, updated_at INTEGER, cwd TEXT, originator TEXT, model TEXT)')
        c.executemany('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?)',[(sid,'vscode','user','Engineering',1794132000,1794132001,'/code/project','Codex Desktop','test-model') for sid in ids])
    return path


def item_db(home, rows):
    path=home/'thread_history_1.sqlite'
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE thread_items(thread_id TEXT, turn_id TEXT, item_id TEXT, rollout_ordinal INTEGER, created_at_ms INTEGER, item_json TEXT, updated_at_ordinal INTEGER, started_at_ms INTEGER, completed_at_ms INTEGER)')
        for sid,item,ordinal in rows:
            c.execute('INSERT INTO thread_items VALUES(?,?,?,?,?,?,?,?,?)',(sid,'turn-a',item['id'],ordinal,1794132001000,json.dumps(item),ordinal,1794132000000,1794132001000))
    return path


def test_current_ui_has_turns_tools_timestamps_without_response_mirrors(home):
    raw={'timestamp':'2026-11-08T10:00:00Z','type':'response_item','payload':{'type':'message','id':'raw-u','role':'user','content':[{'type':'input_text','text':'KEEP-USER'}]}}
    rollout(home,[raw,ui({'type':'UserMessage','id':'u','content':[{'type':'Text','text':'KEEP-USER'}]}),
                  ui({'type':'AgentMessage','id':'a','content':'KEEP-ASSISTANT','phase':'final_answer'},2),
                  ui({'type':'CommandExecution','id':'c','command':'git status --short','cwd':'/code/project','aggregated_output':'KEEP-OUTPUT','exit_code':0,'status':'completed'},3)])
    text=text_of(home)
    assert text.count('KEEP-USER')==1 and text.count('KEEP-ASSISTANT')==1
    assert all(x in text for x in ['git status --short','KEEP-OUTPUT','2026-11-08T10:00:01','turn-a','exec_command'])


def test_historical_sqlite_is_read_only_and_deduplicates_live_projection(home):
    state=state_db(home);history=item_db(home,[(SID,{'type':'agentMessage','id':'a','text':'OLD'},1)])
    rollout(home,[ui({'type':'AgentMessage','id':'a','content':'LATEST'},2)])
    fingerprints={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (state,history)}
    text=text_of(home)
    assert 'LATEST' in text and '\nOLD\n' not in text and text.count('item `a`')==1
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==sha for p,sha in fingerprints.items())


def test_historical_without_rollout_preserves_command_and_mcp_result(home):
    state_db(home)
    item_db(home,[(SID,{'type':'userMessage','id':'u','content':[{'type':'text','text':'History question'}]},1),
                 (SID,{'type':'commandExecution','id':'c','command':'python -m pytest','cwd':'/code/project','aggregatedOutput':'3 passed','exitCode':0},2),
                 (SID,{'type':'mcpToolCall','id':'m','server':'lab','tool':'read','arguments':'{"name":"guide"}','result':{'content':[{'type':'text','text':'KEEP-MCP'}]}},3)])
    text=text_of(home)
    assert all(s in text for s in ['Codex migrated','python -m pytest','3 passed','KEEP-MCP','lab.read'])


def test_archived_rollout_is_supported(home):
    rollout(home,[ui({'type':'AgentMessage','id':'a','content':'ARCHIVED'})],archived=True)
    assert 'ARCHIVED' in text_of(home)


def test_completed_items_upsert_and_partial_tail_waits_without_duplicates(home):
    p=rollout(home,[ui({'type':'UserMessage','id':'u','content':'SAME'})])
    with p.open('ab') as f:f.write(json.dumps(ui({'type':'AgentMessage','id':'a','content':'FIRST'},2)).encode())
    files,stats=build(home)
    assert stats['codex_partial_tail_records']==1 and 'FIRST' not in next(iter(files.values()))
    with p.open('ab') as f:f.write(b'\n'+json.dumps(ui({'type':'AgentMessage','id':'a','content':'UPDATED'},3)).encode()+b'\n')
    text=text_of(home)
    assert text.count('item `a`')==1 and 'UPDATED' in text and 'FIRST' not in text
    assert text_of(home)==text


def test_identical_words_in_distinct_turns_are_not_dropped(home):
    rollout(home,[ui({'type':'UserMessage','id':'u1','content':'repeat'},1,'one'),ui({'type':'UserMessage','id':'u2','content':'repeat'},2,'two')])
    assert text_of(home).count('\nrepeat\n')==2


def test_child_guardian_and_external_claude_imports_are_not_native_codex(home):
    p=rollout(home,[ui({'type':'UserMessage','id':'u','content':'DROP-CHILD'})],sid=CHILD)
    rows=[json.loads(x) for x in p.read_text().splitlines()];rows[0]['payload']['source']={'subagent':{'other':'guardian'}}
    p.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
    rollout(home,[ui({'type':'UserMessage','id':'x','content':'DROP-IMPORTED'})])
    (home/'external_agent_session_imports.json').write_text(json.dumps({'records':[{'imported_thread_id':SID}]}))
    files,stats=build(home)
    assert not files and stats['codex_imported_external_excluded']==1


def test_system_reasoning_and_binary_media_never_publish(home):
    rollout(home,[{'type':'response_item','payload':{'type':'message','role':'system','content':'DROP-SYSTEM'}},
                  ui({'type':'Reasoning','id':'r','raw_content':'DROP-REASONING'}),
                  ui({'type':'UserMessage','id':'u','content':[{'type':'image','data':'DROP-IMAGE'},{'type':'text','text':'KEEP-TEXT'}]},2)])
    text=text_of(home)
    assert 'KEEP-TEXT' in text and not any(s in text for s in ['DROP-SYSTEM','DROP-REASONING','DROP-IMAGE'])


@pytest.mark.parametrize('arguments,result',[
    ({'command':'Get-Content C:\\Users\\Me\\.codex\\auth.json'},'UNKNOWN-SECRET-DUMP'),
    ({'path':'Z:/Documentation/Reference/Credentials/lab.md'},'UNKNOWN-SECRET-DUMP'),
])
def test_sensitive_read_context_withholds_its_bare_result(home,arguments,result):
    rollout(home,[ui({'type':'McpToolCall','id':'m','server':'files','tool':'read','arguments':arguments,'result':result})])
    files,_=build(home,hidden=('a'*32,));text=next(iter(files.values()))
    assert result not in text and cx.WITHHELD in text


def test_shared_secret_rules_and_nested_json_keys_apply_to_outputs(home):
    secret='ghp_'+'A'*30
    result={'content':[{'type':'text','text':'LOCKERSECRETVALUE42'},{'type':'text','text':secret}],
            'nested':json.dumps({'access_token':'BARE_UNKNOWN_SECRET'}),'image':{'type':'image','data':'BINARY-MARKER'}}
    rollout(home,[ui({'type':'McpToolCall','id':'m','server':'lab','tool':'status','arguments':{},'result':result})])
    text=text_of(home)
    assert not any(s in text for s in ['LOCKERSECRETVALUE42',secret,'BARE_UNKNOWN_SECRET','BINARY-MARKER'])


def test_legacy_raw_calls_pair_by_call_id(home):
    rows=[{'timestamp':'2026-11-08T10:00:01Z','ordinal':1,'type':'response_item','payload':{'type':'message','id':'u','role':'user','content':[{'type':'input_text','text':'LEGACY'}]}},
          {'timestamp':'2026-11-08T10:00:02Z','ordinal':2,'type':'response_item','payload':{'type':'function_call','id':'f','call_id':'call-1','name':'exec_command','arguments':'{"cmd":"git status"}'}},
          {'timestamp':'2026-11-08T10:00:03Z','ordinal':3,'type':'response_item','payload':{'type':'function_call_output','call_id':'call-1','output':'LEGACY-OUTPUT'}}]
    rollout(home,rows);text=text_of(home)
    assert 'LEGACY-OUTPUT' in text and text.count('item `call:call-1`')==1


def test_malformed_record_is_counted_without_exporting_text(home):
    p=rollout(home,[ui({'type':'UserMessage','id':'u','content':'KEEP'})])
    with p.open('ab') as f:f.write(b'{"broken":"DROP-MALFORMED"\n')
    files,stats=build(home)
    assert stats['codex_malformed_records']==1 and 'DROP-MALFORMED' not in next(iter(files.values()))


def test_metadata_and_generated_filename_obey_projection_boundary(home):
    path=state_db(home)
    with sqlite3.connect(path) as conn:
        conn.execute('UPDATE threads SET title=?',('Z:/Documentation/Reference/Credentials/PRIVATE-TITLE',))
    p=rollout(home,[ui({'type':'UserMessage','id':'u','content':'KEEP'})])
    rows=[json.loads(line) for line in p.read_text().splitlines()]
    rows[0]['payload']['timestamp']='../../unsafe'
    p.write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
    files,_=build(home)
    assert list(files)==['codex--undated--'+SID+'.md']
    assert 'PRIVATE-TITLE' not in next(iter(files.values()))


def test_compound_secret_keys_withhold_unknown_values(home):
    rollout(home,[ui({'type':'McpToolCall','id':'m','server':'lab','tool':'read','arguments':{},
                      'result':{'client_secret':'UNKNOWN-CLIENT-VALUE','apiToken':'UNKNOWN-API-VALUE','jwt_key':'UNKNOWN-JWT-VALUE'}})])
    text=text_of(home)
    assert not any(s in text for s in ['UNKNOWN-CLIENT-VALUE','UNKNOWN-API-VALUE','UNKNOWN-JWT-VALUE'])


def test_missing_storage_fails_before_publisher_replaces_previous_export(home,tmp_path,monkeypatch):
    monkeypatch.setattr(lib,'locker_pattern',lambda:(re.compile('LOCKERSECRETVALUE42'),5))
    out=tmp_path/'published';out.mkdir()
    existing=out/'codex--index.md';existing.write_text('PREVIOUS-EXPORT')
    with pytest.raises(RuntimeError,match='No supported Codex'):
        pt.main([str(out),'--codex',str(home)])
    assert existing.read_text()=='PREVIOUS-EXPORT'


def test_full_publisher_keeps_claude_doc_and_stable_codex(tmp_path,home,monkeypatch):
    claude=tmp_path/'claude/project';claude.mkdir(parents=True)
    for i in range(10):
        (claude/(str(i)+'.jsonl')).write_text('\n'.join(json.dumps(r) for r in [
            {'type':'user','timestamp':'2026-11-07T10:00:00Z','message':{'content':'CLAUDE-QUESTION'}},
            {'type':'assistant','timestamp':'2026-11-07T10:01:00Z','message':{'content':'CLAUDE-ANSWER'}}]))
    history=tmp_path/'history'/lib.OWNER_NAME;history.mkdir(parents=True);chats=[]
    for i in range(5):
        cid='chat-'+str(i);chats.append({'id':cid,'title':'Guide','created_at':'2026-11-07T10:00:00Z','updated_at':'2026-11-07T10:01:00Z'})
        (history/(cid+'.json')).write_text(json.dumps([{'role':'user','content':'DOC-QUESTION'},{'role':'assistant','content':'DOC-ANSWER'}]))
    (history/'index.json').write_text(json.dumps({'chats':chats}))
    rollout(home,[ui({'type':'UserMessage','id':'u','content':'CODEX-QUESTION'}),ui({'type':'AgentMessage','id':'a','content':'CODEX-ANSWER'},2)])
    monkeypatch.setattr(lib,'locker_pattern',lambda:(re.compile('LOCKERSECRETVALUE42'),5))
    out=tmp_path/'published';args=[str(out),'--claude',str(claude.parent),'--history',str(history.parent),'--codex',str(home)]
    pt.main(args)
    from transcript_store import Snapshot
    files=Snapshot(out).read()['files']
    assert len([n for n in files if n.startswith('claude--')])==10 and len([n for n in files if n.startswith('doc--')])==5
    name='codex--2026-11-08--'+SID+'.md';first=files[name]
    pt.main(args);assert Snapshot(out).read()['files'][name]==first
    assert any('CLAUDE-ANSWER' in body for n,body in files.items() if n.startswith('claude--'))
    assert any('DOC-ANSWER' in body for n,body in files.items() if n.startswith('doc--'))
    assert 'codex--index.md' in files


def test_existing_reader_can_list_search_read_codex_without_server_change(tmp_path,home):
    rollout(home,[ui({'type':'UserMessage','id':'u','content':'PERMITTED-ENGINEERING-MARKER'})])
    files,_=build(home);out=tmp_path/'out';out.mkdir()
    for name,body in files.items():(out/name).write_text(body)
    import ast
    src=Path(__file__).with_name('uhura.py');tree=ast.parse(src.read_text(encoding='utf-8'))
    node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='Library')
    ns={'Path':Path,'os':os,'re':re,'time':__import__('time'),'SNIPPET_CHARS':300}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(src),'exec'),ns)
    reader=ns['Library'](out);name=next(iter(files))
    assert name in reader.listing() and reader.search('PERMITTED-ENGINEERING-MARKER',10)
    assert 'PERMITTED-ENGINEERING-MARKER' in reader.text(name)
    assert reader.text('../outside.md') is None
