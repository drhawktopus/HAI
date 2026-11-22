"""Response-completion adapter; preserves Codex's existing notification command.

Only the existing SYSTEM task is triggered; this adapter never starts a publisher.
Hook payloads are never logged or passed to it. Existing notifier/Stop hooks stay.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
import asyncio
import datetime
import re

ROOT=Path(__file__).resolve().parent
OUT=os.environ.get('UHURA_TRANSCRIPTS_DIR', '')   # the published transcripts folder (the door mounts it)
LOG=ROOT/'publish_transcripts.log'
sys.path.insert(0,str(ROOT.parent))
from transcript_store import Snapshot, wait_for_publication

# The installed task is the authority for principal, interpreter, action and
# IgnoreNew. No registration/settings/ACL change and no command from a caller.
TASK_SCRIPT=r'''
$ErrorActionPreference = 'Stop'
$task = Get-ScheduledTask -TaskName 'Uhura Transcripts' -ErrorAction Stop
if ([string]$task.State -in @('Disabled','Unknown')) { throw 'Existing publisher task is unavailable' }
$service = New-Object -ComObject 'Schedule.Service'
$service.Connect()
$registered = $service.GetFolder('\').GetTask('Uhura Transcripts')
$instance = $null
foreach ($candidate in $registered.GetInstances(0)) { $instance = $candidate; break }
$started = $false
if ($env:UHURA_TRIGGER_START -eq '1' -and $null -eq $instance) {
  $instance = $registered.Run($null)
  $started = $true
}
$instanceId = if ($null -ne $instance) { [string]$instance.InstanceGuid } else { '' }
$task = Get-ScheduledTask -TaskName 'Uhura Transcripts' -ErrorAction Stop
$info = $task | Get-ScheduledTaskInfo
@{task='Uhura Transcripts';task_state=[string]$task.State;instance_id=$instanceId;
  started_new_instance=$started;task_last_result=$info.LastTaskResult;
  task_last_run_utc=$info.LastRunTime.ToUniversalTime().ToString('o')} | ConvertTo-Json -Compress

'''


def event_log(event,source,request_id,**fields):
    try:
        with LOG.open('a',encoding='utf-8',newline='\n') as log:
            log.write(json.dumps({'event':event,'trigger_source':source,'request_id':request_id,
                                 'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),**fields})+'\n')
    except OSError:pass


def request_existing_task(request_id,source='mcp',start=True):
    if not re.fullmatch(r'[0-9a-f]{32}',request_id):raise ValueError('Invalid request ID')
    if source not in ('mcp','codex','claude'):raise ValueError('Invalid trigger source')
    requested=datetime.datetime.now(datetime.timezone.utc).isoformat()
    env=dict(os.environ,UHURA_TRIGGER_START='1' if start else '0')
    result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command','-'],
                          input=TASK_SCRIPT,capture_output=True,text=True,encoding='utf-8',env=env,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise RuntimeError('Existing publisher task request failed')
    if not result.stdout.strip():raise RuntimeError('Existing publisher task returned no task status')
    ack=json.loads(result.stdout.strip().splitlines()[-1])
    if ack.get('task')!='Uhura Transcripts':raise ValueError('Wrong publisher task')
    ack.update(status='requested',request_id=request_id,requested_utc=requested)
    if start:event_log('transcript_task_requested',source,request_id,
                       instance_id=ack.get('instance_id'),started_new_instance=ack.get('started_new_instance'))
    return ack


def publish_request(source,request_id):
    if not OUT:
        sys.exit("transcript_completion: set UHURA_TRANSCRIPTS_DIR (the published transcripts folder)")
    reader=Snapshot(OUT)
    async def request(action):
        return await asyncio.to_thread(request_existing_task,request_id,source,action=='trigger')
    receipt=asyncio.run(wait_for_publication(request_id,request,reader.read,source=source))
    event_log('transcript_trigger_completed',source,request_id,generation=receipt['generation'],
              publication_request_id=receipt.get('publication_request_id',receipt['request_id']),
              publisher_source=receipt.get('publisher_source'),publisher_task='Uhura Transcripts')
    return receipt

def spawn(args,output=None,low_priority=False):
    flags=(getattr(subprocess,'DETACHED_PROCESS',0)|getattr(subprocess,'CREATE_NO_WINDOW',0)) if os.name=='nt' else 0
    if low_priority:flags |= getattr(subprocess,'BELOW_NORMAL_PRIORITY_CLASS',0)
    return subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=output or subprocess.DEVNULL,
                            stderr=output or subprocess.DEVNULL,creationflags=flags,start_new_session=os.name!='nt')

def completed(source,payload,notify=None):
    if notify:
        try:spawn(list(notify)+[payload])
        except OSError:pass  # preserve publication even when the old desktop notifier is unavailable
    event=json.loads(payload or '{}')
    if not isinstance(event,dict):raise ValueError('Expected completion event')
    if source=='codex' and event.get('type') not in (None,'agent-turn-complete','turn-ended'):
        return None
    request_id=uuid.uuid4().hex
    with LOG.open('a',encoding='utf-8',newline='\n') as log:
        spawn([sys.executable,'-I',str(Path(__file__).resolve()),'publish',source,request_id],log,low_priority=True)
    return request_id

def main(argv):
    if argv and argv[0]=='publish':
        if len(argv)!=3 or argv[1] not in ('codex','claude','mcp') or not re.fullmatch(r'[0-9a-f]{32}',argv[2]):
            return 2
        try:publish_request(argv[1],argv[2]);return 0
        except Exception as error:
            event_log('transcript_trigger_failed',argv[1],argv[2],error_type=type(error).__name__)
            return 1
    parser=argparse.ArgumentParser();parser.add_argument('source',choices=['codex','claude']);parser.add_argument('--notify-target')
    parser.add_argument('payload',nargs='?');args=parser.parse_args(argv)
    payload=args.payload if args.source=='codex' else sys.stdin.read()
    notify=json.loads(Path(args.notify_target).read_text(encoding='utf-8-sig')) if args.notify_target else None
    try:completed(args.source,payload,notify)
    except (OSError,ValueError,TypeError):
        print(json.dumps({'systemMessage':'Uhura transcript publication could not start; the existing periodic refresh remains.'}))
        return 0
    print('{}')
    return 0

if __name__=='__main__':raise SystemExit(main(sys.argv[1:]))
