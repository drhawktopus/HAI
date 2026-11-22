"""Fixed publication route never delegates command/host choice to Uhura."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import hostops_sidecar as h

RID='a'*32
RECEIPT={'status':'completed','request_id':RID,'generation':'b'*32,'snapshot_started_utc':'2026-11-09T06:00:00+00:00',
         'completed_utc':'2026-11-09T06:00:01+00:00','documents':20,'stats':{'codex':3}}

class FixedTranscriptRefresh(unittest.TestCase):
    def test_trigger_and_status_only_address_the_existing_fixed_task(self):
        receipt={'status':'requested','request_id':RID,'requested_utc':'2026-11-09T06:00:00+00:00',
                 'task':'Uhura Transcripts','task_state':'Running','instance_id':'fixed-instance',
                 'started_new_instance':True,'task_last_result':267009,'task_last_run_utc':'2026-11-09T06:00:00+00:00'}
        for action,verb in [('trigger','trigger-transcripts'),('status','transcript-status')]:
            with self.subTest(action=action),patch.object(h,'_load_hosts',return_value={'softserve-transcripts':{'host':'fixed'}}),patch.object(h,'_ssh_exec',return_value=(0,json.dumps(receipt))) as run:
                self.assertEqual(h._refresh_transcripts({'request_id':RID,'action':action}),receipt)
                self.assertEqual(run.call_args.args[1],verb+' '+RID)

    def test_task_action_cannot_select_a_command_or_fake_completed_publication(self):
        with patch.object(h,'_ssh_exec') as run:
            for action in ['whoami','trigger & whoami','../other','wait',12,[]]:
                with self.subTest(action=action),self.assertRaises(ValueError):
                    h._refresh_transcripts({'request_id':RID,'action':action})
            run.assert_not_called()
        with patch.object(h,'_load_hosts',return_value={'softserve-transcripts':{'host':'fixed'}}),patch.object(h,'_ssh_exec',return_value=(0,json.dumps(RECEIPT))):
            with self.assertRaises(RuntimeError):h._refresh_transcripts({'request_id':RID,'action':'trigger'})

    def test_only_fixed_softserve_publisher_can_execute(self):
        entry={'host':'box','user':'admin','key':'held-by-broker'}
        with patch.object(h,'_load_hosts',return_value={'softserve-transcripts':entry}),patch.object(h,'_ssh_exec',return_value=(0,json.dumps(RECEIPT))) as run:
            self.assertEqual(h._refresh_transcripts({'request_id':RID}),RECEIPT)
        args=run.call_args.args
        self.assertIs(args[0],entry);self.assertEqual(args[1],'refresh-transcripts '+RID)

    def test_payload_cannot_select_host_command_or_path(self):
        with patch.object(h,'_ssh_exec') as run:
            for extra in ['host','command','out','source']:
                with self.subTest(extra=extra),self.assertRaises(ValueError):h._refresh_transcripts({'request_id':RID,extra:'evil'})
            run.assert_not_called()

    def test_request_id_cannot_inject_remote_shell(self):
        with patch.object(h,'_ssh_exec') as run:
            for value in ['',None,12,'a'*31,'a'*32+' & whoami','../token']:
                with self.subTest(value=value),self.assertRaises(ValueError):h._refresh_transcripts({'request_id':value})
            run.assert_not_called()

    def test_missing_registered_softserve_fails_closed(self):
        with patch.object(h,'_load_hosts',return_value={}),patch.object(h,'_ssh_exec') as run:
            with self.assertRaises(RuntimeError):h._refresh_transcripts({'request_id':RID})
            run.assert_not_called()

    def test_failed_publisher_is_not_a_completed_refresh(self):
        with patch.object(h,'_load_hosts',return_value={'softserve-transcripts':{'host':'fixed'}}),patch.object(h,'_ssh_exec',return_value=(1,'failed')) as run:
            with self.assertRaises(RuntimeError):h._refresh_transcripts({'request_id':RID})
            run.assert_called_once()

    def test_receipt_must_prove_this_request_completed(self):
        for reply in ['{}','not json',json.dumps(dict(RECEIPT,status='working')),json.dumps(dict(RECEIPT,request_id='c'*32))]:
            with self.subTest(reply=reply),patch.object(h,'_load_hosts',return_value={'softserve-transcripts':{'host':'fixed'}}),patch.object(h,'_ssh_exec',return_value=(0,reply)) as run:
                with self.assertRaises(RuntimeError):h._refresh_transcripts({'request_id':RID})
                run.assert_called_once()

    def test_refresh_auth_is_configured_nonempty_and_exact(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'token'
            with patch.object(h,'TRANSCRIPT_REFRESH_TOKEN_FILE',str(p)):
                self.assertFalse(h._transcript_refresh_authed({}))
                p.write_text('');self.assertFalse(h._transcript_refresh_authed({'X-Uhura-Liaison':''}))
                p.write_text('fixed-test-token')
                self.assertFalse(h._transcript_refresh_authed({'x-hostops-token':'fixed-test-token'}))
                self.assertFalse(h._transcript_refresh_authed({'X-Uhura-Liaison':'wrong'}))
                self.assertTrue(h._transcript_refresh_authed({'X-Uhura-Liaison':'fixed-test-token'}))

    def test_refresh_route_refuses_unauthed_before_any_execution(self):
        class Request:
            path='/transcripts/refresh';headers={};rfile=io.BytesIO(b'{}')
            def _json(self,status,payload):self.result=(status,payload)
            def _authed(self):raise AssertionError('refresh must not fall into general command authentication')
        request=Request()
        with patch.object(h,'_transcript_refresh_authed',return_value=False),patch.object(h,'_refresh_transcripts') as run:
            h.Handler.do_POST(request)
            self.assertEqual(request.result[0],401);run.assert_not_called()
