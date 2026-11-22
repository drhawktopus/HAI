"""Image selection must affect real orders; self renders must never silently become generic."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from fastapi.testclient import TestClient
import app
import comfy_client
import config
import p20_queue
import person_record
import render_options
import tools
from _no_live_probes import start_no_live_probes, stop_no_live_probes


def setUpModule(): start_no_live_probes()
def tearDownModule(): stop_no_live_probes()

PERSON = {"alex": {"name": "Alex C", "consent": {"granted": True,
    "scope": {"uses": ["personal"], "allowlist": ["Alex"]}},
    "models": [{"kind": "image_lora", "status": "ready", "trigger": "a1ex",
                "path": r"B:\models\alex_flux2_v1.safetensors"}]}}


class ImageControls(unittest.TestCase):
    def test_named_scene_keeps_composition_before_the_identity(self):
        for framing in ('chest-up side-profile portrait', 'full-length photograph',
                        'eye-level full-body photograph'):
            text = 'Create a ' + framing + ' of Avery wearing a navy coat.'
            scene = app._p20_scene_from(text, 'Avery')
            self.assertEqual(scene, framing + ' of the subject wearing a navy coat.')

    def test_generic_scene_keeps_camera_instructions_and_other_people(self):
        self.assertEqual(app._p20_scene_from(
            'Create an eye-level photograph of a woman beside a statue.', None),
            'eye-level photograph of a woman beside a statue.')
        self.assertEqual(app._p20_scene_from(
            'Please make a picture of Avery beside Alex.', 'Avery'), 'subject beside Alex.')

    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.object(config, 'STATE_DIR', tmp))
        self.enterContext(mock.patch.object(p20_queue, 'QUEUE_DIR', str(Path(tmp)/'queue')))
        self.enterContext(mock.patch.object(person_record, '_load_all', return_value=copy.deepcopy(PERSON)))
        self.enterContext(mock.patch.object(comfy_client, 'quota_remaining', return_value=None))
        self.enterContext(mock.patch.object(comfy_client, 'record_use'))
        self.enterContext(mock.patch.object(tools.notes, 'log'))
        self.enterContext(mock.patch.object(app, '_persist'))
        self.client = TestClient(app.app)

    def test_renderer_preference_follows_the_account(self):
        r = self.client.post('/prefs/renderer', json={'user':'Alex','renderer':'flux2_mothership'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.client.get('/prefs/renderer?user=Alex').json()['renderer'],'flux2_mothership')
        self.assertEqual(self.client.get('/prefs/renderer?user=Someone').json()['renderer'],'auto')

    def test_renderer_can_return_to_auto_without_touching_text_pin(self):
        self.client.post('/prefs/pin',json={'user':'Alex','model':'force_27b','think':'max'})
        render_options.save('Alex','flux2_mothership')
        render_options.save('Alex','auto')
        self.assertEqual(render_options.preference('Alex'),'auto')
        self.assertEqual(render_options.automatic_setup('Alex'),'flux2_mothership')
        self.assertEqual(self.client.get('/prefs/pin?user=Alex').json()['pin']['think'],'max')

    def test_unknown_renderer_is_refused_without_saving(self):
        r=self.client.post('/prefs/renderer',json={'user':'Alex','renderer':'invented_gpu'})
        self.assertEqual(r.status_code,400)
        self.assertEqual(render_options.preference('Alex'),'auto')

    def test_saved_renderer_pins_the_actual_order(self):
        render_options.save('Alex','flux2_mothership')
        tools._queue_generic_render('Alex','a turtle')
        j=p20_queue.pending('Alex')[0]
        self.assertEqual((j['host'],j['renderer'],j['requires']),
                         ('mothership','flux2_mothership',['render.flux2']))

    def test_explicit_renderer_override_does_not_rewrite_account_preference(self):
        render_options.save('Alex','flux2_mothership')
        tools._queue_generic_render('Alex','a turtle',renderer='auto')
        self.assertEqual(p20_queue.pending('Alex')[0]['host'],'')
        self.assertEqual(render_options.preference('Alex'),'flux2_mothership')

    def test_me_and_myself_resolve_to_authenticated_account(self):
        for text in ('Make an image of me eating fried chicken','Generate a portrait of myself',
                     'me flying above New York','draw me with sunglasses'):
            self.assertEqual(app._p20_resolve_subject(text,'Alex')[0],'alex',text)

    def test_for_me_does_not_turn_an_object_into_a_self_portrait(self):
        for text in ('Make a picture for me of a turtle','Draw me a turtle','Paint me another tree'):
            self.assertIsNone(render_options.self_reference(text),text)

    def test_generic_entry_cannot_silently_drop_a_self_identity(self):
        answer=tools._queue_generic_render('Alex','me eating fried chicken')
        j=p20_queue.pending('Alex')[0]
        self.assertEqual((j['person'],j['use'],j['trigger']),('alex','personal','a1ex'))
        self.assertEqual(j['lora_path'],PERSON['alex']['models'][0]['path'])
        self.assertIn('alex_flux2_v1',answer)
        self.assertNotIn('no identity model',answer.lower())

    def test_account_alias_uses_personal_consent_despite_different_display_name(self):
        tools._render_person('Alex',{'person':'alex','scene':'eating fried chicken'})
        self.assertEqual(p20_queue.pending('Alex')[0]['use'],'personal')

    def test_own_checkpoint_listing_uses_the_same_canonical_personal_identity(self):
        self.assertIn('trigger=a1ex',tools._list_checkpoints('Alex',{'person':'alex'}))

    def test_missing_self_record_does_not_queue_a_generic_person(self):
        with mock.patch.object(person_record,'_load_all',return_value={}):
            answer=tools._queue_generic_render('Alex','me flying above New York')
        self.assertIn('identity model',answer)
        self.assertEqual(p20_queue.pending('Alex'),[])

    def test_missing_trained_identity_is_explained_without_a_generic_fallback(self):
        people=copy.deepcopy(PERSON);people['alex']['models']=[]
        with mock.patch.object(person_record,'_load_all',return_value=people):
            answer=tools._queue_generic_render('Alex','me flying')
        self.assertIn('no trained image model',answer)
        self.assertEqual(p20_queue.pending('Alex'),[])

    def test_explicit_image_composer_queues_a_plain_prompt_without_an_llm(self):
        with mock.patch.object(app.mccoy,'observe') as triage:
            answer=app._run_query_core(app.Q(text='a turtle',user='Alex',output_kind='image',
                                          renderer='flux2_mothership'))
        triage.assert_not_called()
        self.assertEqual(answer['route'],'tool')
        self.assertEqual(p20_queue.pending('Alex')[0]['host'],'mothership')

    def test_explicit_self_option_uses_the_saved_model_for_a_scene_only(self):
        app._queue_explicit_image(app.Q(text='flying above New York',user='Alex',
                                       output_kind='image',image_identity='self'))
        self.assertEqual(p20_queue.pending('Alex')[0]['person'],'alex')

    def test_conflicting_no_identity_and_me_does_not_queue_a_stranger(self):
        answer=app._queue_explicit_image(app.Q(text='me flying',user='Alex',output_kind='image',image_identity='none'))
        self.assertEqual(answer['route'],'error')
        self.assertEqual(p20_queue.pending('Alex'),[])

    def test_loading_metadata_reads_the_real_adapter_and_claimed_host(self):
        tools._queue_generic_render('Alex','me eating fried chicken')
        j=p20_queue.pending('Alex')[0];p20_queue.claim(j['id'],'mothership')
        out=self.client.get('/p20/my-jobs?user=Alex').json()['jobs'][0]
        self.assertEqual(out['host'],'mothership')
        self.assertIn('alex_flux2_v1',out['description'])
        self.assertNotIn('No identity model',out['description'])

    def test_generic_image_honestly_reports_no_identity_model(self):
        tools._queue_generic_render('Alex','a turtle')
        j=p20_queue.pending('Alex')[0]
        self.assertIn('No identity model',render_options.describe(j)['description'])

    def test_identity_picker_reads_only_the_users_usable_personal_model(self):
        got=self.client.get('/prefs/renderer?user=Alex').json()['identity']
        self.assertEqual((got['available'],got['name'],got['model']),(True,'Alex C','alex_flux2_v1'))
        self.assertFalse(self.client.get('/prefs/renderer?user=Someone').json()['identity']['available'])

    def test_unknown_renderer_cannot_create_an_order_through_image_composer(self):
        answer=app._queue_explicit_image(app.Q(text='a turtle',user='Alex',output_kind='image',renderer='wrong'))
        self.assertEqual(answer['route'],'error')
        self.assertEqual(p20_queue.pending('Alex'),[])

    def test_agent_image_tool_keeps_raw_self_request_when_prompt_was_rewritten(self):
        tools._generate_image('Alex',{'prompt':'a man flying above a city'},
                              user_text='Make an image of me flying above a city')
        job=p20_queue.pending('Alex')[0]
        self.assertEqual((job['person'],job['use']),('alex','personal'))
        self.assertEqual(job['lora_path'],PERSON['alex']['models'][0]['path'])

    def test_agent_image_tool_honors_saved_renderer_for_plain_image(self):
        render_options.save('Alex','flux2_mothership')
        tools._generate_image('Alex',{'prompt':'a turtle in a garden'})
        job=p20_queue.pending('Alex')[0]
        self.assertEqual((job['renderer'],job['host']),('flux2_mothership','mothership'))
        self.assertEqual(job['requires'],['render.flux2'])

    def test_natural_image_request_uses_top_renderer_without_a_mode_choice(self):
        with mock.patch.object(app.mccoy,'observe'), mock.patch.object(app.ollama_client,'chat') as chat:
            answer=app._run_query_core(app.Q(text='Generate an image of a turtle',user='Alex',
                                          renderer='flux2_mothership'))
        chat.assert_not_called()
        self.assertEqual(answer['route'],'p20-direct')
        self.assertEqual(p20_queue.pending('Alex')[0]['renderer'],'flux2_mothership')

    def test_unplugged_archive_is_explained_and_never_queues_a_substitute(self):
        people=copy.deepcopy(PERSON);people['alex']['models'][0]['status']='unavailable'
        people['alex']['consent']['scope']['uses'].append('named')
        with mock.patch.object(person_record,'_load_all',return_value=people):
            answer=tools._render_person('Alex',{'person':'alex','scene':'flying above a city'})
            self.assertIn('unavailable',tools._library_people('Alex'))
            self.assertIn('archive drive is unplugged',tools._list_checkpoints('Alex',{'person':'alex'}))
        self.assertIn('archive drive is unplugged',answer)
        self.assertEqual(p20_queue.pending('Alex'),[])


def _with_krea_adapter(first):
    """PERSON plus a Krea 2 adapter for alex, listed first or second (the record keeps the ACTIVE model first)."""
    people=copy.deepcopy(PERSON)
    krea={"kind":"image_lora","status":"ready","base":"krea2","trigger":"a1ex",
          "path":r"F:\comfyui-rocm\models\loras\alex_krea2_v1.safetensors"}
    people['alex']['models'].insert(0 if first else 1, krea)
    return people


class KreaRenderer(unittest.TestCase):
    """Krea 2 is a separate base: its renders need render.krea2, and an adapter only loads into the base it was trained on."""
    setUp = ImageControls.setUp

    def test_krea_is_offered_and_saved_for_the_account(self):
        ids=[r['id'] for r in self.client.get('/prefs/renderer?user=Alex').json()['renderers']]
        self.assertIn('krea2_mothership',ids)
        r=self.client.post('/prefs/renderer',json={'user':'Alex','renderer':'krea2_mothership'})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(render_options.preference('Alex'),'krea2_mothership')
        self.assertIn('krea2_mothership',app.CHAT_HTML)

    def test_generic_krea_order_needs_the_krea_capability(self):
        render_options.save('Alex','krea2_mothership')
        answer=tools._queue_generic_render('Alex','a turtle')
        j=p20_queue.pending('Alex')[0]
        self.assertEqual((j['host'],j['renderer'],j['render_model'],j['requires']),
                         ('mothership','krea2_mothership','krea2',['render.krea2']))
        self.assertIn('Krea 2',answer)
        self.assertNotIn('Flux.2',render_options.describe(j)['description'])

    def test_krea_never_renders_a_person_with_a_flux2_adapter(self):
        render_options.save('Alex','krea2_mothership')
        answer=tools._render_person('Alex',{'person':'alex','scene':'flying above a city'})
        self.assertIn('Krea 2',answer)
        self.assertIn('Flux.2',answer)
        self.assertEqual(p20_queue.pending('Alex'),[])
        self.assertFalse(render_options.own_identity('Alex')['available'])

    def test_krea_uses_the_krea_adapter_even_when_it_is_not_first(self):
        with mock.patch.object(person_record,'_load_all',return_value=_with_krea_adapter(first=False)):
            tools._render_person('Alex',{'person':'alex','scene':'flying','renderer':'krea2_mothership'})
            ident=render_options.own_identity('Alex','krea2_mothership')
        j=p20_queue.pending('Alex')[0]
        self.assertEqual((j['lora_path'],j['requires']),
                         (r'F:\comfyui-rocm\models\loras\alex_krea2_v1.safetensors',['render.krea2']))
        self.assertEqual(ident['model'],'alex_krea2_v1')

    def test_flux2_never_renders_with_a_krea_adapter(self):
        with mock.patch.object(person_record,'_load_all',return_value=_with_krea_adapter(first=True)):
            tools._render_person('Alex',{'person':'alex','scene':'flying','renderer':'flux2_mothership'})
        j=p20_queue.pending('Alex')[0]
        self.assertEqual((j['lora_path'],j['requires']),(PERSON['alex']['models'][0]['path'],['render.flux2']))

    def test_krea_training_order_needs_the_krea_trainer_and_registers_krea(self):
        with mock.patch.object(app,'p20_order',return_value={'ok':True,'routed_to':['mothership']}) as order:
            tools._queue_training(sorted(config.ADMIN_USERS)[0],
                                  {'name':'alex_krea2_v1','launcher':r'D:\x.bat','log':r'D:\x.log',
                                   'output_dir':r'D:\out','base':'krea2','trigger':'a1ex'})
        req=order.call_args[0][0]
        self.assertEqual(req.requires,['train','train.krea2'])
        self.assertEqual(req.spec['register']['base'],'krea2')
