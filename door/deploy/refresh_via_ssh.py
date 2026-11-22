"""Forced-command adapter to the EXISTING SYSTEM task; never runs a publisher."""
import os
import json
from pathlib import Path
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent))
from transcript_completion import event_log, publish_request, request_existing_task

def command(original):
    match=re.fullmatch(r'(refresh-transcripts|trigger-transcripts|transcript-status) ([0-9a-f]{32})',original or '')
    if not match:raise ValueError('Only the fixed transcript task request is permitted')
    return match[1],match[2]

def main():
    try:action,request_id=command(os.environ.get('SSH_ORIGINAL_COMMAND',''))
    except ValueError:
        print('Only transcript publication is permitted.',file=sys.stderr);return 2
    try:
        # Compatibility waits use the same task, never the retired direct spawn.
        result=(publish_request('mcp',request_id) if action=='refresh-transcripts' else
                request_existing_task(request_id,'mcp',start=action=='trigger-transcripts'))
    except Exception as error:
        event_log('transcript_trigger_failed','mcp',request_id,error_type=type(error).__name__)
        print('Transcript task request failed; publication is not confirmed.',file=sys.stderr)
        return 1
    print(json.dumps(result),flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
