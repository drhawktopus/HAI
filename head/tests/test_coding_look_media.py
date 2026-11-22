"""Real-media regression at the tool-to-model boundary; no live systems."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest import mock

from PIL import Image
import coding_client
import ollama_client
import tools

FIXTURES = Path(__file__).parent / 'fixtures/media'


def encoded_image(fmt, mode='RGB', **save_options):
    im = Image.new(mode, (8, 6), (120, 60, 10, 128) if mode == 'RGBA' else (120, 60, 10))
    out = io.BytesIO()
    im.save(out, format=fmt, **save_options)
    return out.getvalue()


class CodingLookMedia(unittest.TestCase):
    def look(self, raw, path='job/logo.png', sees=True, base64_override=None):
        got = {'path': path, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
               'data_b64': base64_override if base64_override is not None else base64.b64encode(raw).decode()}
        before = copy.deepcopy(got)
        ctx = ollama_client._CALL_CONTEXT
        ctx.serving, ctx.pictures = ('http://h', 'm'), []
        self.addCleanup(lambda: (setattr(ctx, 'serving', None), setattr(ctx, 'pictures', None)))
        with mock.patch.object(coding_client, 'read_bytes', return_value=got), \
                mock.patch.object(ollama_client, 'can_see', return_value=sees):
            result = json.loads(tools.run_tool('coding_look', {'workspace': 'w', 'path': path}, '', user='Alex'))
        self.assertEqual(got, before, 'runner result and source bytes must not be rewritten')
        return result, list(ctx.pictures)

    def test_exact_box2_m_ico_with_misleading_png_filename_becomes_real_png(self):
        raw = (FIXTURES / 'box2-m-favicon-mislabeled.png').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), 'cfda639f360d6bb47bb378d5e3f122ead908802cca2235272bfafdb66f286c0d')
        out, offered = self.look(raw, 'box2/box2-m/lttlabs-favicon.png')
        self.assertEqual(out['seen_by'], 'you')
        decoded = base64.b64decode(offered[0][1], validate=True)
        self.assertTrue(decoded.startswith(b'\x89PNG\r\n\x1a\n'), 'must not label raw ICO as PNG')
        with Image.open(io.BytesIO(decoded)) as actual, Image.open(FIXTURES / 'box2-m-existing-strata-normalized.png') as expected:
            self.assertEqual((actual.mode, actual.size, actual.tobytes()), (expected.mode, expected.size, expected.tobytes()))
        self.assertEqual(out['image']['source_format'], 'ICO')
        self.assertEqual(out['image']['format'], 'PNG')
        self.assertEqual((out['image']['width'], out['image']['height']), (16, 14))
        self.assertEqual(out['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(out['image']['sha256'], hashlib.sha256(decoded).hexdigest())

    def test_actual_bytes_determine_format_even_with_ico_or_unknown_suffix(self):
        raw = (FIXTURES / 'box2-m-favicon-mislabeled.png').read_bytes()
        for path in ('logo.ico', 'logo.bin', 'logo.jpg'):
            with self.subTest(path=path):
                out, offered = self.look(raw, path)
                self.assertEqual(out['seen_by'], 'you')
                self.assertTrue(base64.b64decode(offered[0][1]).startswith(b'\x89PNG\r\n\x1a\n'))

    def test_valid_png_is_verified_then_passed_byte_identically(self):
        raw = encoded_image('PNG', mode='RGBA')
        out, pictures = self.look(raw)
        self.assertEqual(base64.b64decode(pictures[0][1]), raw)
        self.assertFalse(out['image']['normalized'])

    def test_jpeg_gif_webp_bmp_normalize_to_png_without_resizing(self):
        for fmt in ('JPEG', 'GIF', 'WEBP', 'BMP'):
            with self.subTest(fmt=fmt):
                out, pictures = self.look(encoded_image(fmt))
                decoded = base64.b64decode(pictures[0][1])
                with Image.open(io.BytesIO(decoded)) as im:
                    self.assertEqual((im.format, im.size), ('PNG', (8, 6)))
                self.assertEqual(out['image']['source_format'], fmt)

    def test_exif_orientation_is_preserved_as_existing_strata_does(self):
        im = Image.new('RGB', (8, 6), (10, 20, 30))
        exif = Image.Exif(); exif[0x0112] = 6
        buf = io.BytesIO(); im.save(buf, format='JPEG', exif=exif)
        out, pictures = self.look(buf.getvalue())
        with Image.open(io.BytesIO(base64.b64decode(pictures[0][1]))) as decoded:
            self.assertEqual(decoded.size, (6, 8))

    def test_transparency_uses_existing_strata_white_background_policy(self):
        out, pictures = self.look(encoded_image('WEBP', mode='RGBA', lossless=True))
        with Image.open(io.BytesIO(base64.b64decode(pictures[0][1]))) as im:
            self.assertEqual(im.mode, 'RGB')
            self.assertEqual(im.getpixel((0, 0)), (187, 157, 132))

    def test_corrupt_bytes_and_bad_base64_are_normal_tool_errors_without_picture(self):
        for raw, override in ((b'not a picture', None), (b'\x89PNG\r\n\x1a\n', None), (b'', '!!!!'),
                              (b'', 'AA'), (b'', None)):
            with self.subTest(raw=raw, override=override):
                out, pictures = self.look(raw, base64_override=override)
                self.assertEqual(out['seen_by'], None)
                self.assertEqual(out['error_code'], 'invalid_image_data')
                self.assertTrue(out['error'])
                self.assertEqual(pictures, [])

    def test_no_vision_model_gets_no_picture_or_false_visual_claim(self):
        out, pictures = self.look(encoded_image('PNG'), sees=False)
        self.assertIsNone(out['seen_by'])
        self.assertEqual(pictures, [])
        self.assertIn('not set up to take pictures', out['error'])

    def test_animated_gif_uses_first_frame_and_reports_that_choice(self):
        first = Image.new('RGB', (8, 6), 'red')
        second = Image.new('RGB', (8, 6), 'blue')
        buf = io.BytesIO(); first.save(buf, format='GIF', save_all=True, append_images=[second], duration=100)
        out, pictures = self.look(buf.getvalue())
        self.assertEqual(out['image']['source_frames'], 2)
        with Image.open(io.BytesIO(base64.b64decode(pictures[0][1]))) as im:
            self.assertEqual(im.getpixel((0, 0)), (255, 0, 0))

    def test_native_gif_transparency_is_not_replaced_with_a_white_background(self):
        im = Image.new('P', (8, 6), 0)
        im.putpalette([0, 0, 0, 255, 0, 0] + [0] * 762)
        im.putpixel((1, 1), 1)
        buf = io.BytesIO(); im.save(buf, format='GIF', transparency=0)
        out, pictures = self.look(buf.getvalue())
        with Image.open(io.BytesIO(base64.b64decode(pictures[0][1]))) as decoded:
            self.assertEqual(decoded.mode, 'RGBA')
            self.assertEqual(decoded.getpixel((0, 0)), (0, 0, 0, 0))
            self.assertEqual(decoded.getpixel((1, 1)), (255, 0, 0, 255))

    def test_unsupported_container_is_a_tool_error_even_if_pillow_can_read_it(self):
        out, pictures = self.look(encoded_image('TIFF'))
        self.assertEqual(out['error_code'], 'invalid_image_data')
        self.assertIn('TIFF', out['error'])
        self.assertEqual(pictures, [])

    def test_bad_media_is_returned_to_agent_and_agent_can_choose_another_image(self):
        asks = []
        good = encoded_image('PNG')

        def once(host, model, messages, *args, **kwargs):
            asks.append(copy.deepcopy(messages))
            if len(asks) == 2:
                self.assertIn('invalid_image_data', messages[-1]['content'])
                self.assertFalse(any(m.get('images') for m in messages))
            if len(asks) == 3:
                self.assertTrue(base64.b64decode(messages[-1]['images'][0]).startswith(b'\x89PNG\r\n\x1a\n'))
                return {'message': {'role': 'assistant', 'content': 'Recovered using the other image.'}}
            return {'message': {'role': 'assistant', 'content': '', 'tool_calls': [{'function': {
                'name': 'coding_look', 'arguments': {'workspace': 'w', 'path': 'bad.png' if len(asks) == 1 else 'good.png'}}}]}}

        def read(workspace, path):
            raw = b'not an image' if path == 'bad.png' else good
            return {'path': path, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                    'data_b64': base64.b64encode(raw).decode()}

        with mock.patch.object(ollama_client, '_chat_once', once), \
                mock.patch.object(tools, 'tools_for', lambda tl, user: tl), \
                mock.patch.object(coding_client, 'read_bytes', read), \
                mock.patch.object(ollama_client, 'can_see', return_value=True):
            answer, _ = ollama_client.chat('http://h', 'm', [{'role': 'user', 'content': 'Look at my reference.'}], [])
        self.assertEqual(answer, 'Recovered using the other image.')
        self.assertEqual(len(asks), 3)

    def test_normalized_picture_reaches_both_wires_and_keeps_history_unchanged(self):
        raw = (FIXTURES / 'box2-m-favicon-mislabeled.png').read_bytes()
        out, pictures = self.look(raw)
        messages = [{'role': 'user', 'content': 'initial request'},
                    {'role': 'user', 'content': '[Picture from earlier]', 'images': ['OLDER-UNCHANGED']}]
        before = copy.deepcopy(messages)
        ollama_client._add_pictures(messages, pictures)
        self.assertEqual(messages[:2], before)
        self.assertTrue(base64.b64decode(messages[-1]['images'][0]).startswith(b'\x89PNG\r\n\x1a\n'))
        wire = ollama_client._openai_messages(messages[-1:], pictures=True)
        part = wire[0]['content'][0]['image_url']['url']
        self.assertTrue(part.startswith('data:image/png;base64,iVBOR'))


class ABlindJobIsSaidToTheOperator(unittest.TestCase):

    def blind_look(self, job, run='run:abc'):
        raw = encoded_image('PNG')
        got = {'path': 'job/shot.png', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
               'data_b64': base64.b64encode(raw).decode()}
        ctx = ollama_client._CALL_CONTEXT
        ctx.serving, ctx.pictures, ctx.job, ctx.window_run = ('http://box:11434', 'blind-35b'), [], job, run
        self.addCleanup(lambda: [setattr(ctx, k, None) for k in ('serving', 'pictures', 'job', 'window_run')])
        with mock.patch.object(coding_client, 'read_bytes', return_value=got), \
                mock.patch.object(ollama_client, 'can_see', return_value=False):
            return json.loads(tools.run_tool('coding_look', {'workspace': 'w', 'path': 'job/shot.png'}, '', user='Alex'))

    def setUp(self):
        tools._BLIND_JOBS_TOLD.clear()
        import claim_sweep
        self.desk = mock.patch.object(claim_sweep, 'notify_desk').start()
        self.addCleanup(mock.patch.stopall)

    def test_a_jobs_first_look_nothing_saw_reaches_the_desk_once_for_that_run(self):
        first = self.blind_look(job=True)
        second = self.blind_look(job=True)
        self.assertIsNone(first['seen_by'])
        self.assertIsNone(second['seen_by'])
        self.assertEqual(self.desk.call_count, 1)
        said = self.desk.call_args[0][0]
        self.assertIn('blind-35b', said)
        self.assertIn('cannot see', said)
        self.assertEqual(self.desk.call_args[1].get('tag'), 'job:blind')
        self.blind_look(job=True, run='run:another')
        self.assertEqual(self.desk.call_count, 2, 'another run of the same model is another notice')

    def test_a_chat_turn_is_told_in_its_result_and_the_desk_is_left_alone(self):
        out = self.blind_look(job=False)
        self.assertIn('not set up to take pictures', out['error'])
        self.assertEqual(self.desk.call_count, 0)

    def test_a_notice_that_fails_never_costs_the_look_its_answer(self):
        self.desk.side_effect = RuntimeError('relay down')
        out = self.blind_look(job=True)
        self.assertIn('not set up to take pictures', out['error'])


class ABoxThatIsFixedIsBelievedWithoutARestart(unittest.TestCase):
    """A model is given its eyes (or its parser) ON the box. A 'no' kept for the life of the process left it blind to
    Kirk until Kirk restarted."""

    def setUp(self):
        for d in (ollama_client._CAN_SEE, ollama_client._CAN_SEASON, ollama_client._NO_UNTIL, ollama_client._SEASON_QUIET):
            saved = dict(d)
            d.clear()
            self.addCleanup(lambda d=d, saved=saved: (d.clear(), d.update(saved)))

    def answers(self, *bodies):
        seq = list(bodies)
        asked = []

        def post(url, json=None, timeout=None):
            asked.append(url)
            r = mock.Mock()
            r.raise_for_status = lambda: None
            r.json = lambda body=seq[min(len(asked), len(seq)) - 1]: body
            return r
        return asked, mock.patch.object(ollama_client.httpx, 'post', post)

    def test_a_no_about_eyes_is_asked_again_after_its_minute_and_a_yes_is_kept(self):
        asked, patch = self.answers({'capabilities': ['tools']}, {'capabilities': ['tools', 'vision']})
        with patch, mock.patch.object(ollama_client.time, 'time', return_value=1000.0):
            self.assertIs(ollama_client.can_see('http://box:11434', 'm35'), False)
            self.assertIs(ollama_client.can_see('http://box:11434', 'm35'), False)
            self.assertEqual(len(asked), 1, 'inside the minute the box is not asked again')
        with patch, mock.patch.object(ollama_client.time, 'time',
                                      return_value=1000.0 + ollama_client.CAPABILITY_NO_LIFE_S + 1):
            self.assertIs(ollama_client.can_see('http://box:11434', 'm35'), True)
            self.assertIs(ollama_client.can_see('http://box:11434', 'm35'), True)
            self.assertEqual(len(asked), 2, 'a yes is kept: no third ask')

    def test_a_no_about_seasoning_is_asked_again_after_its_minute(self):
        import scheduler
        url = next((c.get('url') or '').rstrip('/') for c in scheduler.CARDS.values() if c.get('url'))
        asked, patch = self.answers({'modelfile': 'FROM x\n'}, {'modelfile': 'FROM x\nPARSER qwen3.5\n'})
        with patch, mock.patch.object(ollama_client.time, 'time', return_value=2000.0):
            self.assertIs(ollama_client.can_season(url, 'm35'), False)
            self.assertIs(ollama_client.can_season(url, 'm35'), False)
            self.assertEqual(len(asked), 1)
        with patch, mock.patch.object(ollama_client.time, 'time',
                                      return_value=2000.0 + ollama_client.CAPABILITY_NO_LIFE_S + 1):
            self.assertIs(ollama_client.can_season(url, 'm35'), True)


if __name__ == '__main__':
    unittest.main()
