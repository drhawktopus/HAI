"""Reference selection must survive routing without dropping consent or adding identity LoRAs."""
import json
from unittest import mock

import app
import krea_reference
import p20_queue
import render_options
import tools
import test_image_controls as fixtures
from _no_live_probes import start_no_live_probes, stop_no_live_probes


def setUpModule(): start_no_live_probes()
def tearDownModule(): stop_no_live_probes()


import unittest
class ReferenceControls(unittest.TestCase):
    setUp = fixtures.ImageControls.setUp

    def test_a_head_without_extensions_has_no_second_adapter_renderer(self):
        import tempfile
        core = {k: v for k, v in render_options.RENDERERS.items() if not v.get('second_adapter')}
        with tempfile.TemporaryDirectory() as empty, mock.patch.dict(render_options.RENDERERS, core, clear=True):
            render_options._load_extensions(here=empty)
            self.assertEqual(render_options.RENDERERS, core, "an empty folder adds no renderer")
        self.assertIn(render_options.REFERENCE_RENDERER, core)
        self.assertTrue(all(not v.get('second_adapter') for v in core.values()))

    def generated_catalog(self):
        self.catalog()
        item = {'person':'control_character', 'filename':'generated.png', 'path':r'D:\controls\source.png',
                'sha256':'b'*64, 'kind':'generated_character', 'owner':'Alex',
                'name':'Fictional control', 'source_job':'123456abcdef', 'source_image':'source.png'}
        path = krea_reference.root() / 'catalog.json'
        items = json.loads(path.read_text())
        items['generated'] = item
        path.write_text(json.dumps(items))
        (krea_reference.root() / 'generated.png').write_bytes(b'\x89PNG\r\n\x1a\ntest')
        return {'user':'Alex','status':'done','use':'generic','image':'source.png',
                'person':'','lora_path':None}

    def test_generated_reference_uses_source_ownership_without_person_consent(self):
        source = self.generated_catalog()
        with mock.patch.object(p20_queue, 'get', return_value=source), \
             mock.patch.object(krea_reference.person_record, 'check_use', return_value=(False,'no human record')):
            self.assertEqual(krea_reference.resolve('Alex','generated')['use'],'generated')
            self.assertEqual(krea_reference.choices('Alex')[0]['name'],'Fictional control')
            self.assertEqual(krea_reference.choices('Alex')[0]['kind'],'generated_character')
            preview = self.client.get('/krea/reference/generated?user=Alex')
            self.assertEqual(preview.status_code,200)
            self.assertEqual(preview.headers['content-type'],'image/png')
            self.assertEqual(self.client.get('/krea/reference/generated?user=Someone').status_code,404)

    def test_generated_source_must_be_completed_owned_and_identity_free(self):
        source = self.generated_catalog()
        for change in ({'user':'Someone'},{'status':'pending'},{'use':'named'},
                       {'image':'other.png'},{'person':'alex'}, {'lora_path':'identity.safetensors'},
                       {'reference':{'id':'human'}},{'second_adapter':'second.safetensors'}):
            with self.subTest(change=change), mock.patch.object(p20_queue,'get',return_value=dict(source,**change)):
                with self.assertRaises(ValueError): krea_reference.resolve('Alex','generated')

    def test_generated_reference_queues_through_the_existing_reference_path(self):
        source = self.generated_catalog()
        prior = p20_queue.enqueue('Alex','alex','a previous portrait',None,None,
            use='personal',consent_reason='fixture',renderer='krea2_mothership')
        p20_queue.mark(prior['id'],'done',image='previous.png')
        with mock.patch.object(p20_queue,'get',return_value=source):
            result = app._queue_explicit_image(app.Q(text='A fictional character with her face visible',user='Alex',
                output_kind='image',renderer=render_options.REFERENCE_RENDERER,image_reference='generated'))
            mismatch = app._queue_explicit_image(app.Q(text='A portrait of Alex',user='Alex',
                output_kind='image',renderer=render_options.REFERENCE_RENDERER,image_reference='generated'))
        job = p20_queue.pending('Alex')[0]
        self.assertEqual(result['route'],'tool')
        self.assertEqual((job['person'],job['use']),('control_character','generated'))
        self.assertEqual(job['reference']['source_job'],'123456abcdef')
        self.assertIsNone(job['lora_path'])
        self.assertNotIn('second_adapter',job)
        self.assertIn('different person',mismatch['answer'])
        self.assertEqual(len(p20_queue.pending('Alex')),1)

    def catalog(self):
        root = krea_reference.root()
        root.mkdir()
        self.ref = {'person': 'alex', 'filename': 'approved.jpg',
                    'path': r'D:\approved\alex.jpg', 'sha256': 'a' * 64}
        (root / 'catalog.json').write_text(json.dumps({'ref1': self.ref}))
        (root / 'ref1.jpg').write_bytes(b'\xff\xd8\xfftest')

    def test_reference_options_and_previews_are_profile_scoped(self):
        self.catalog()
        self.assertIn(render_options.REFERENCE_RENDERER, render_options.available('Alex'))
        self.assertNotIn(render_options.REFERENCE_RENDERER, render_options.available('Someone'))
        self.assertEqual(self.client.get('/krea/references?user=Someone').json()['references'], [])
        self.assertEqual(self.client.get('/krea/reference/ref1?user=Someone').status_code, 404)
        self.assertEqual(self.client.get('/krea/reference/ref1?user=Alex').status_code, 200)

    def test_reference_image_order_uses_reference_without_character_adapter(self):
        self.catalog()
        result = app._queue_explicit_image(app.Q(text='A portrait in a bookshop', user='Alex',
            output_kind='image', renderer=render_options.REFERENCE_RENDERER, image_reference='ref1'))
        job = p20_queue.pending('Alex')[0]
        self.assertEqual(job['reference']['path'], self.ref['path'])
        self.assertEqual(job['reference']['sha256'], self.ref['sha256'])
        self.assertEqual(job['requires'], ['render.krea2'])
        self.assertIsNone(job['lora_path'])
        self.assertNotIn('second_adapter', job)
        self.assertEqual(job['edit_strength'], 1.0)
        self.assertIn('approved.jpg', result['answer'])

    def test_consent_revocation_removes_reference_and_prevents_queue(self):
        self.catalog()
        self.assertTrue(krea_reference.choices('Alex'))
        with mock.patch.object(krea_reference.person_record, 'check_use', return_value=(False, 'revoked')):
            self.assertEqual(krea_reference.choices('Alex'), [])
            tools._queue_reference_render('Alex', 'a portrait', 'ref1', render_options.REFERENCE_RENDERER)
        self.assertEqual(p20_queue.pending('Alex'), [])

    def test_missing_reference_and_mismatched_person_never_fall_back(self):
        self.catalog()
        tools._queue_reference_render('Alex', 'a portrait', '../private', render_options.REFERENCE_RENDERER)
        with self.assertRaises(ValueError):
            p20_queue.enqueue('Alex', 'other', 'portrait', None, None, use='personal',
                consent_reason='test', renderer=render_options.REFERENCE_RENDERER, reference_id='ref1')
        self.assertEqual(p20_queue.pending('Alex'), [])

    def test_named_person_automatically_uses_their_approved_reference(self):
        self.catalog()
        app._queue_explicit_image(app.Q(text='Make a portrait of me in a bookshop', user='Alex',
            output_kind='image', renderer=render_options.REFERENCE_RENDERER))
        job = p20_queue.pending('Alex')[0]
        self.assertEqual(job['person'], 'alex')
        self.assertEqual(job['reference']['id'], 'ref1')
        self.assertEqual(job['reference']['selection'], 'automatic')
        self.assertIsNone(job['lora_path'])

    def test_automatic_reference_does_not_cross_people_or_require_a_trained_adapter(self):
        self.catalog()
        with mock.patch.object(krea_reference.person_record, 'usable_models', return_value=[]):
            tools._render_person('Alex', {'person':'alex', 'scene':'a portrait',
                                        'renderer':render_options.REFERENCE_RENDERER})
        self.assertEqual(p20_queue.pending('Alex')[0]['reference']['selection'], 'automatic')
        with self.assertRaises(ValueError):
            krea_reference.automatic('Alex', 'quinn')

    def test_preferred_portrait_beats_filename_order_without_crossing_person(self):
        self.catalog()
        p = krea_reference.root() / 'catalog.json'
        items = json.loads(p.read_text())
        items['portrait'] = dict(self.ref, filename='z_portrait.jpg', preferred=True)
        items['other'] = dict(self.ref, person='quinn', filename='a_other.jpg', preferred=True)
        p.write_text(json.dumps(items))
        self.assertEqual(krea_reference.automatic('Alex', 'alex')['id'], 'portrait')

    def test_comparison_settings_reach_both_existing_identity_paths(self):
        self.catalog()
        people = fixtures._with_krea_adapter(first=True)
        with mock.patch.object(krea_reference.person_record, '_load_all', return_value=people):
            for renderer in ['krea2_mothership', render_options.REFERENCE_RENDERER]:
                app._queue_explicit_image(app.Q(text='A portrait of me in a bookshop', user='Alex',
                    output_kind='image', renderer=renderer, image_seed=42041,
                    image_width=1024, image_height=1024))
        jobs = p20_queue.pending('Alex')
        self.assertEqual(len(jobs), 2)
        self.assertTrue(jobs[0]['lora_path'])
        self.assertIsNone(jobs[1]['lora_path'])
        for job in jobs:
            self.assertEqual((job['seed'],job['width'],job['height']), (42041,1024,1024))

    def test_invalid_comparison_controls_never_queue_gpu_work(self):
        self.catalog()
        for settings in [{'image_seed':-1}, {'image_width':257,'image_height':1024}, {'image_width':1024}]:
            result = app._queue_explicit_image(app.Q(text='A portrait of me', user='Alex',
                output_kind='image', renderer=render_options.REFERENCE_RENDERER, **settings))
            self.assertEqual(result['route'], 'error')
        self.assertEqual(p20_queue.pending('Alex'), [])
