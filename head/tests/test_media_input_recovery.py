"""The actual box2-m rejection becomes agent-visible only for a proven NEW tool batch."""
import base64
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import httpx
import model_input_error as mie
import ollama_client as oc
import run_ledger

BODY = json.dumps({'error':{'code':400,'message':'Failed to load image or audio file','type':'invalid_request_error'}})

class MediaRecovery(unittest.TestCase):
    def test_only_the_exact_recorded_decoder_error_before_output_is_classified(self):
        self.assertTrue(mie.window_media_rejection({'error':'RuntimeError: openai server HTTP 400: '+BODY,'stream_chunks':0}))
        for row in [{'error':'RuntimeError: openai server HTTP 500: '+BODY},
                    {'error':'RuntimeError: openai server HTTP 400: '+BODY[:-2]},
                    {'error':'RuntimeError: openai server HTTP 400: '+BODY,'stream_chunks':1},
                    {'error':'RuntimeError: openai server HTTP 400: {"error":"bad grammar"}'}]:
            with self.subTest(row=row):self.assertFalse(mie.window_media_rejection(row))

    def test_repair_preserves_prefix_and_raw_rejected_bytes_and_refuses_uncertainty(self):
        prefix=[{'role':'user','content':'earlier','images':['accepted-before']}]
        messages=copy.deepcopy(prefix);oc._add_pictures(messages,[('new.png','new-bytes')])
        state={'pending':[{'index':1,'message':copy.deepcopy(messages[1])}],'rejected':[]}
        self.assertTrue(oc._repair_rejected_tool_media(messages,state,2))
        self.assertEqual(messages[:1],prefix);self.assertNotIn('images',messages[1])
        self.assertEqual(state['rejected'][0]['offered'][0]['message']['images'],['new-bytes'])
        self.assertFalse(oc._repair_rejected_tool_media(messages,state,2))
        changed=copy.deepcopy(state);changed['pending']=[{'index':1,'message':{'different':'request'}}]
        before=copy.deepcopy(messages);self.assertFalse(oc._repair_rejected_tool_media(messages,changed,3));self.assertEqual(messages,before)

    def _exercise(self, restart):
        class Crash(BaseException):pass
        raw=(Path(__file__).parent/'fixtures/media/box2-m-favicon-mislabeled.png').read_bytes()
        bad=base64.b64encode(raw).decode();good='real-converted-png'
        calls=[];tools=[];events=[]
        def tool_turn(path):return {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':'inspect_image','arguments':{'path':path}}}]}}
        sequence=[tool_turn('bad.png'),mie.MediaInputRejected('rejected')]
        if restart:sequence.append(Crash())
        sequence.extend([tool_turn('converted.png'),{'message':{'role':'assistant','content':'recovered'}}])
        def once(_h,_m,messages,*a,**kw):
            calls.append(copy.deepcopy(messages));out=sequence.pop(0)
            if isinstance(out,BaseException):raise out
            return out
        def tool(_fn,args,*a,**kw):
            tools.append(args['path']);self.assertTrue(oc.offer_picture(args['path'],bad if args['path']=='bad.png' else good));return 'image offered'
        prefix=[{'role':'system','content':'SYS'},{'role':'user','content':'task'},{'role':'user','content':'old accepted image','images':['old-unchanged']}]
        with tempfile.TemporaryDirectory() as temp, patch.object(run_ledger,'ROOT',Path(temp)/'runs'), \
             patch.object(oc,'_chat_once',side_effect=once),patch.object(oc.tools,'run_tool',side_effect=tool), \
             patch.object(oc,'can_see',return_value=True),patch.object(oc.tool_selection,'select',side_effect=lambda ts,*a,**kw:(ts,None)), \
             patch.object(oc.time,'sleep',lambda *a:None),patch.object(oc,'_delta',side_effect=lambda x:events.append(x)):
            led=run_ledger.RunLedger.create('media-test','stream',{})
            def run(ledger):
                with oc.run_ledger(ledger),oc.call_context(job=True):
                    return oc.chat('h','m',copy.deepcopy(prefix),[{'type':'function','function':{'name':'inspect_image','parameters':{}}}],num_ctx=32768)
            if restart:
                with self.assertRaises(Crash):run(led)
                rec=json.loads((led.dir/'calls/0.json').read_text())
                self.assertEqual(rec['loop']['media']['rejected'][0]['offered'][0]['message']['images'],[bad])
                self.assertEqual(rec['loop']['media']['pending'],[])
                answer,_=run(run_ledger.RunLedger('media-test'))
            else:answer,_=run(led)
            self.assertEqual(answer,'recovered');self.assertEqual(tools,['bad.png','converted.png'])
            repair_request=next(m for m in calls if any('Tool image delivery error' in str(x.get('content')) for x in m))
            self.assertEqual(repair_request[:len(prefix)],prefix)
            self.assertFalse(any(bad in m.get('images',[]) for m in repair_request))
            self.assertTrue(any(e.get('name')=='image_delivery' and e.get('snag') for e in events))

    def test_normal_agent_loop_can_choose_a_new_reference_after_rejection(self):self._exercise(False)
    def test_rejected_batch_and_repair_request_survive_a_real_ledger_restart(self):self._exercise(True)

    def test_previously_accepted_or_unattributable_media_is_not_silently_removed(self):
        with patch.object(oc,'_chat_once',side_effect=mie.MediaInputRejected('rejected')) as request, \
             patch.object(oc.tool_selection,'select',side_effect=lambda ts,*a,**kw:(ts,None)):
            with self.assertRaises(oc.ChatError):
                oc.chat('h','m',[{'role':'user','content':'existing','images':['old']}],[],num_ctx=32768)
        self.assertEqual(request.call_count,1)

    def test_untyped_streaming_http400_keeps_the_existing_error_policy(self):
        request=httpx.Request('POST','http://synthetic/model')
        response=httpx.Response(400,request=request,stream=httpx.ByteStream(BODY.encode()))
        error=httpx.HTTPStatusError('streamed rejection',request=request,response=response)
        messages=[{'role':'user','content':'existing history','images':['accepted']}];before=copy.deepcopy(messages)
        with patch.object(oc,'_chat_once',side_effect=error),patch.object(oc.config,'CHAT_RETRIES',0), \
             patch.object(oc.tool_selection,'select',side_effect=lambda ts,*a,**kw:(ts,None)):
            with self.assertRaises(oc.ChatError):oc.chat('h','m',messages,[],num_ctx=32768)
        self.assertEqual(messages,before)
