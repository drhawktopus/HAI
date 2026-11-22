"""One writer and one atomic filtered snapshot; no original transcript records."""
import contextlib
import datetime
import json
import io
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import uuid
import zipfile
import asyncio
from collections.abc import Mapping

CATALOG = 'transcripts.snapshot.zip'
NAME = re.compile(r'^(?:claude|doc|codex)--[^/\\\x00]+\.md$')

@contextlib.contextmanager
def publication_lock(root, timeout=180):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    path=root/'.publish.lock'
    if path.is_symlink():raise ValueError('Publication lock must not be a symlink')
    with path.open('a+b') as handle:
        if path.stat().st_size==0:handle.write(b'0');handle.flush()
        deadline=time.monotonic()+timeout
        while True:
            try:
                handle.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except (OSError,BlockingIOError):
                if time.monotonic()>=deadline:raise TimeoutError('Transcript publisher is busy; no overlapping refresh started')
                time.sleep(.1)
        try:yield
        finally:
            handle.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(handle,fcntl.LOCK_UN)

def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _instant(value):
    parsed=datetime.datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:raise ValueError('Publication time must include UTC offset')
    return parsed


async def wait_for_publication(request_id, request_task, snapshot, source='mcp',
                               visibility_seconds=20, poll_seconds=.2, status_seconds=5,
                               sleep=asyncio.sleep, clock=time.monotonic):
    """Thin trigger/wait adapter for the EXISTING SYSTEM task, never a publisher.

    Task Scheduler owns execution/IgnoreNew. The atomic snapshot proves completion.
    A task already running before the request must finish before a fresh run is
    requested. No queue, replacement writer, task timeout or automatic failed-run
    retry is introduced. Manual and native completion requests share this logic.
    """
    if not re.fullmatch(r'[0-9a-f]{32}',request_id):raise ValueError('Invalid refresh request ID')
    ack=await request_task('trigger')
    if ack.get('request_id')!=request_id:raise ValueError('Refresh acknowledgement belongs to another request')
    legacy=ack.get('status')=='completed'
    if legacy:
        # Rollout compatibility: old adapters returned the completed receipt.
        if not re.fullmatch(r'[0-9a-f]{32}',str(ack.get('generation',''))):
            raise ValueError('Invalid completed publication receipt')
        _instant(ack['completed_utc'])
        deadline=clock()+visibility_seconds
    elif ack.get('status')=='requested' and ack.get('task')=='Uhura Transcripts':
        cutoff=_instant(ack['requested_utc']);started=ack.get('started_new_instance') is True
        deadline=None;next_status=clock()+status_seconds
    else:raise ValueError('Invalid refresh acknowledgement')
    while True:
        data=snapshot() or {};visible=data.get('receipt') or {}
        if legacy:
            valid=(visible.get('generation')==ack.get('generation') or
                   (visible.get('snapshot_started_utc','')>=ack.get('completed_utc','~')))
            if valid:
                return dict(ack,visible_generation=visible['generation'])
        elif (visible.get('status')=='completed' and visible.get('source')=='timer'
              and _instant(visible['snapshot_started_utc'])>=cutoff):
            return {**{key:visible[key] for key in ('generation','snapshot_started_utc','completed_utc','documents','stats')},
                    'status':'completed','request_id':request_id,'publication_request_id':visible['request_id'],
                    'trigger_source':source,'publisher_source':visible['source'],
                    'visible_generation':visible['generation'],'publisher_task':'Uhura Transcripts'}
        if deadline is not None and clock()>=deadline:
            raise RuntimeError('Publisher completed, but its publication is not yet visible; completion was not confirmed.')
        if not legacy:
            state=ack.get('task_state')
            if state in ('Disabled','Unknown'):raise RuntimeError('Existing transcript task is '+state)
            if not ack.get('instance_id') and state=='Ready':
                if ack.get('task_last_result')!=0:
                    raise RuntimeError('Existing transcript task failed (result %s); publication was not confirmed.' % ack.get('task_last_result'))
                if started:
                    if deadline is None:deadline=clock()+visibility_seconds
                else:
                    ack=await request_task('trigger')
                    if ack.get('request_id')!=request_id:raise ValueError('Invalid task acknowledgement')
                    started=ack.get('started_new_instance') is True
                    if not started and not ack.get('instance_id'):
                        raise RuntimeError('Existing transcript task did not accept the refresh')
                    next_status=clock()+status_seconds
            if clock()>=next_status:
                ack=await request_task('status')
                if ack.get('request_id')!=request_id or ack.get('task')!='Uhura Transcripts':
                    raise ValueError('Invalid task status')
                next_status=clock()+status_seconds
        await sleep(poll_seconds)

def commit(root, files, stats, request_id, started, source):
    if not re.fullmatch(r'[0-9a-f]{32}',request_id):raise ValueError('Invalid refresh request ID')
    if not all(NAME.fullmatch(n) and isinstance(t,str) for n,t in files.items()):raise ValueError('Invalid filtered transcript set')
    receipt={'status':'completed','request_id':request_id,'generation':uuid.uuid4().hex,
             'snapshot_started_utc':started,'completed_utc':utc(),'source':source,'documents':len(files),'stats':stats}
    fd,temp=tempfile.mkstemp(prefix='.transcripts-',suffix='.tmp',dir=root)
    os.close(fd)
    try:
        with zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=3) as archive:
            archive.writestr('__publication__.json',json.dumps({'version':1,'receipt':receipt}))
            for name,text in files.items():archive.writestr(name,text)
        with open(temp,'r+b') as f:f.flush();os.fsync(f.fileno())
        os.replace(temp,Path(root)/CATALOG)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    return receipt

class ZipTexts(Mapping):
    """Compressed bytes pin one publication through an entire reader operation.

    No filesystem handle remains open, so Windows writers can atomically replace
    the snapshot. Only compressed bytes and requested text are kept, rather than
    eagerly decoding every transcript into wide Unicode strings.
    """
    def __init__(self,archive,names):self.archive=archive;self.names=tuple(names)
    def __iter__(self):return iter(self.names)
    def __len__(self):return len(self.names)
    def __getitem__(self,name):
        if name not in self.names:raise KeyError(name)
        return self.archive.read(name).decode('utf-8')

class Snapshot:
    def __init__(self,root):self.root=Path(root);self._lock=threading.Lock();self._fingerprint=None;self._data=None
    def read(self):
        p=self.root/CATALOG
        if p.is_symlink():raise ValueError('Transcript snapshot must not be a symlink')
        try:s=p.stat()
        except FileNotFoundError:return None
        fingerprint=(s.st_mtime_ns,s.st_size,s.st_ino)
        with self._lock:
            if fingerprint!=self._fingerprint:
                archive=None
                try:
                    archive=zipfile.ZipFile(io.BytesIO(p.read_bytes()))
                    names=archive.namelist()
                    if len(names)!=len(set(names)) or '__publication__.json' not in names:
                        raise ValueError('Invalid publication members')
                    names=[n for n in names if n!='__publication__.json']
                    if not all(NAME.fullmatch(n) for n in names):raise ValueError('Invalid transcript names')
                    data=json.loads(archive.read('__publication__.json'))
                    if (data.get('version')!=1 or not re.fullmatch(r'[0-9a-f]{32}',data.get('receipt',{}).get('generation',''))):
                        raise ValueError('Invalid publication metadata')
                    data['files']=ZipTexts(archive,names)
                except (OSError,ValueError,KeyError,zipfile.BadZipFile,TypeError,AttributeError):
                    if archive:archive.close()
                    raise ValueError('Invalid committed transcript snapshot') from None
                self._data=data;self._fingerprint=fingerprint
            return self._data
