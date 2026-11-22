"""A reachable coding entry loads the existing permitted toolkit, not another worker."""
import copy
import json
import unittest
from unittest.mock import patch
import config
import tool_selection as ts
import tools
import ollama_client as oc


def names(items): return [t['function']['name'] for t in items]
def schema(name): return {'type':'function','function':{'name':name,'description':name,'parameters':{}}}


class CodingEntry(unittest.TestCase):
    def test_interactive_engineering_open_loads_existing_operations_deployment_and_handoff_tools(self):
        with patch.object(config,'ADMIN_USERS',{'test-admin'}),patch.object(tools,'_ground_dataset_sets',side_effect=lambda x:x):
            allowed=tools.tools_for(tools.HEAVY_TOOLS,'test-admin')
        got,_=self.expand(available=allowed)
        needed={'host_admin_list','host_admin_run','host_admin_exec','self_deploy','deploy_status','deploy_promote','deploy_cancel','journal_append','handoff_note'}
        self.assertTrue(needed <= set(names(got)));self.assertTrue(set(names(got)) <= set(names(allowed)))

    def test_host_discovery_loads_only_the_existing_permitted_host_family(self):
        available=[schema('host_admin_list'),schema('host_admin_run')]
        got,note=oc._coding_tools_after_open('host_admin_list',{},'Registered admin hosts:\nsundae (user@host)',available[:1],available,100000)
        self.assertEqual(names(got),names(available));self.assertIsNone(note)
        bad,_=oc._coding_tools_after_open('host_admin_list',{},'Could not read host registry',available[:1],available,100000)
        self.assertEqual(names(bad),['host_admin_list'])

    def test_engineering_brief_distinguishes_workspace_limits_from_gated_host_authority(self):
        import ast
        from pathlib import Path
        tree=ast.parse((Path(oc.__file__).parent/'app.py').read_text(encoding='utf-8'))
        value=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='CODING_MODE_BRIEF' for t in n.targets))
        self.assertIn("not a limit on Doc's overall role",value)
        self.assertIn('host_admin_exec',value);self.assertIn('human approvals',value)
        self.assertIn('review before the final checkpoint',value)
        self.assertNotIn('checkpoint before review',value)

    def setUp(self):
        self.core=schema('search_docs')
        self.entry=next(t for t in tools.CODING_TOOLS if t['function']['name']=='coding_workspace')
        self.pool=[self.core]+list(tools.CODING_TOOLS)
        self.open_args={'action':'open','workspace':'test-workspace'}
        self.open_result=json.dumps({'id':'test-workspace','read':True,'write':True})
        self.msgs=[{'role':'system','content':'SYS'},{'role':'user','content':'Please continue'}]
        p=patch.object(oc,'_STICKY_TOOLS',{});p.start();self.addCleanup(p.stop)
        import tempfile, shutil   # Isolate the block store to this test's temp dir; do not touch the gate's store.
        d=tempfile.mkdtemp();self.addCleanup(shutil.rmtree,d,True)
        p=patch.object(oc,'TOOL_BLOCK_DIR',d);p.start();self.addCleanup(p.stop)

    def expand(self, result=None, args=None, available=None, picked=None, budget=100000):
        return oc._coding_tools_after_open('coding_workspace',self.open_args if args is None else args,
            self.open_result if result is None else result,[self.core,self.entry] if picked is None else picked,
            self.pool if available is None else available,budget)

    def test_workspace_entry_survives_a_semantic_miss_without_loading_the_full_pool(self):
        pool=self.pool+[schema('other_%d'%i) for i in range(35)]
        with patch.object(ts,'_GENERAL',[frozenset(names(pool))]),patch.object(ts,'_PURPOSE_BUILT',[]), \
             patch.object(ts,'_matrix',return_value=(names(pool),[[0.,1.] for _ in pool])), \
             patch.object(ts.embeddings,'embed_queries',return_value=[[1.,0.]]):
            got,_=ts.select(pool,'test-admin','fix the upload bug',125952,corpus=('',0.))
        self.assertEqual(names(got),['search_docs','coding_workspace'])

    def test_successful_open_adds_all_existing_coding_tools_once_after_the_existing_prefix(self):
        got,note=self.expand()
        self.assertEqual(names(got),['search_docs']+names(tools.CODING_TOOLS));self.assertIsNone(note)
        self.assertEqual(self.expand(picked=got)[0],got)

    def test_list_failure_malformed_and_mismatched_results_do_not_load_tools(self):
        for args,result in [({'action':'list'},'{}'),(self.open_args,'not JSON'),
                            (self.open_args,'{"error":"denied"}'),(self.open_args,'{"id":"other","read":true}'),
                            (self.open_args,'{"id":"test-workspace","read":false}')]:
            with self.subTest(result=result):
                got,_=self.expand(args=args,result=result);self.assertEqual(names(got),['search_docs','coding_workspace'])

    def test_open_never_reintroduces_permissions_or_tools_absent_from_the_caller_pool(self):
        survey=[self.core,self.entry,schema('coding_read')]
        got,_=self.expand(available=survey)
        self.assertEqual(names(got),names(survey))
        with patch.object(config,'ADMIN_USERS',{'test-admin'}),patch.object(tools,'_ground_dataset_sets',side_effect=lambda x:x):
            allowed=tools.tools_for(tools.HEAVY_TOOLS,'non-admin')
        got,_=self.expand(available=allowed,picked=[self.core])
        expected=set(names(tools.CODING_TOOLS)) & set(names(allowed))
        self.assertEqual(set(names(got))-{'search_docs'},expected)
        self.assertNotIn('coding_decision',names(got));self.assertNotIn('coding_escalation',names(got))
        self.assertNotIn('self_deploy',names(got))

    def test_insufficient_context_keeps_the_prior_set_and_explains_the_limit(self):
        picked=[self.core,self.entry]
        got,note=self.expand(budget=ts._approx_tokens(picked)+1)
        self.assertEqual(got,picked);self.assertIn('could not fit',note)

    def test_small_brain_pool_does_not_gain_the_workspace_entry(self):
        self.assertNotIn('coding_workspace',names(tools.BRAIN_TOOLS))
        self.assertIn('coding_workspace',names(tools.HEAVY_TOOLS))

    def test_actual_loop_opens_reads_and_retains_the_complete_toolset_for_the_next_request(self):
        captured=[];executed=[];visible=[]
        turns=[{'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':'coding_workspace','arguments':self.open_args}}]}},
               {'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':'coding_read','arguments':{'workspace':'test-workspace','path':'app.py'}}}]}},
               {'message':{'role':'assistant','content':'verified'}}]
        def once(host,model,messages,offered,*args,**kw):
            captured.append(names(offered));visible.append(copy.deepcopy(messages));return turns.pop(0)
        def run(name,args,*a,**kw):
            executed.append(name);return self.open_result if name=='coding_workspace' else '{"content":"source","sha256":"sha"}'
        with patch.object(ts,'select',return_value=([self.core,self.entry],None)), \
             patch.object(oc,'_chat_once',side_effect=once),patch.object(tools,'run_tool',side_effect=run), \
             patch.object(tools,'tools_for',side_effect=lambda p,u:p):
            answer,esc=oc.chat('test-host','test-model',copy.deepcopy(self.msgs),self.pool,num_ctx=125952)
        self.assertEqual((answer,esc),('verified',None));self.assertEqual(executed,['coding_workspace','coding_read'])
        self.assertEqual(captured[0],['search_docs','coding_workspace'])
        self.assertEqual(captured[1:],[[ 'search_docs']+names(tools.CODING_TOOLS)]*2)
        self.assertIn('Available tools now:',visible[1][-1]['content'])
        self.assertIn('coding_list',visible[1][-1]['content'])
        later=oc.sticky_tools('U',self.msgs,[self.core],self.pool,100000)
        # Same user used by chat() above is None; a different user must not inherit it.
        self.assertEqual(names(later),['search_docs'])
        held=oc.sticky_tools(None,self.msgs+[{'role':'assistant','content':'verified'},{'role':'user','content':'next'}],
                             [self.core],self.pool,100000)
        self.assertEqual(names(held),captured[1])

    def test_merely_offering_the_entry_does_not_activate_a_coding_loop(self):
        with patch.object(ts,'select',return_value=([self.core,self.entry],None)), \
             patch.object(oc,'_chat_once',return_value={'message':{'role':'assistant','content':'hello'}}), \
             patch.object(tools,'tools_for',side_effect=lambda p,u:p),patch.object(oc,'_LoopProgress') as progress:
            self.assertEqual(oc.chat('test-host','test-model',copy.deepcopy(self.msgs),self.pool,num_ctx=125952)[0],'hello')
        progress.assert_not_called()

    def test_expanded_tool_names_are_saved_in_the_existing_run_checkpoint(self):
        import tempfile
        from pathlib import Path
        import run_ledger
        with tempfile.TemporaryDirectory() as temp:
            ledger=run_ledger.RunLedger.create('entry-test','stream',{},root=temp)
            slot=ledger.open_call();responses=[{'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':'coding_workspace','arguments':self.open_args}}]}},
                                             {'message':{'role':'assistant','content':'verified'}}]
            with patch.object(ts,'select',return_value=([self.core,self.entry],None)),patch.object(oc,'_chat_once',side_effect=responses), \
                 patch.object(tools,'run_tool',return_value=self.open_result),patch.object(tools,'tools_for',side_effect=lambda p,u:p), \
                 patch.object(oc._CALL_CONTEXT,'ckpt_slot',slot,create=True):
                oc.chat('test-host','test-model',copy.deepcopy(self.msgs),self.pool,num_ctx=125952)
            record=json.loads(slot.path.read_text())
            self.assertEqual(record['tool_names'],['search_docs']+names(tools.CODING_TOOLS))

    # a JOB's open keeps the block as a SET: core + toolkit + tools called in this job; drop the rest

    def job_pick(self):
        extra=[schema('fetch_url'),schema('docker_stop'),schema('git_commit'),schema('render_person')]
        return [self.core,self.entry]+extra,self.pool+extra

    def test_a_jobs_open_keeps_core_the_toolkit_and_what_it_called_and_lets_the_rest_go(self):
        picked,available=self.job_pick()
        got,note=oc._coding_tools_after_open('coding_workspace',self.open_args,self.open_result,picked,available,100000,
                                             job=True,called={'render_person'})
        self.assertIsNone(note)
        self.assertEqual(names(got)[:4],['search_docs','coding_workspace','fetch_url','render_person'],
                         'always-offered tools and a tool the job already used stay, in their old order')
        self.assertEqual(set(names(got)),{'search_docs','fetch_url','render_person'}|set(names(tools.CODING_TOOLS)))
        self.assertNotIn('docker_stop',names(got));self.assertNotIn('git_commit',names(got))

    def test_a_chats_open_still_only_adds(self):
        picked,available=self.job_pick()
        got,_=oc._coding_tools_after_open('coding_workspace',self.open_args,self.open_result,picked,available,100000)
        self.assertEqual(names(got)[:len(picked)],names(picked),'a person may ask about anything next: nothing is let go')

    def test_a_toolkit_that_does_not_fit_leaves_the_jobs_block_as_it_was(self):
        picked,available=self.job_pick()
        got,note=oc._coding_tools_after_open('coding_workspace',self.open_args,self.open_result,picked,available,1,job=True)
        self.assertEqual(names(got),names(picked));self.assertIn('could not fit',note)

    def test_the_conversation_holds_the_jobs_set_afterwards_not_the_old_block(self):
        picked,available=self.job_pick()
        responses=[{'message':{'role':'assistant','content':'','tool_calls':[{'function':{'name':'coding_workspace','arguments':self.open_args}}]}},
                   {'message':{'role':'assistant','content':'done'}}]
        with patch.object(ts,'select',return_value=(picked,None)),patch.object(oc,'_chat_once',side_effect=responses), \
             patch.object(tools,'run_tool',return_value=self.open_result),patch.object(tools,'tools_for',side_effect=lambda p,u:p), \
             patch.object(oc._CALL_CONTEXT,'job',True,create=True):
            oc.chat('test-host','test-model',copy.deepcopy(self.msgs),available,num_ctx=125952)
        held=oc.sticky_tools(None,self.msgs+[{'role':'assistant','content':'done'},{'role':'user','content':'next'}],
                             [self.core],available,100000)      # the same conversation, a turn later
        self.assertNotIn('docker_stop',names(held),'the held list starts over from the set: the old picks do not come back')
        self.assertNotIn('git_commit',names(held))
        self.assertTrue({'search_docs','fetch_url','coding_workspace','coding_test'} <= set(names(held)))
