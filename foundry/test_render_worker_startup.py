"""One worker must claim the shared render before touching the card."""
import unittest
from unittest import mock
import p20_render_worker as W


class RenderOwnership(unittest.TestCase):
    def test_current_image_is_named_during_its_entire_job_and_cleared_on_failure(self):
        def fail(token, job):
            self.assertEqual(W.ACTIVE_RENDER_JOB, job['id'])
            raise RuntimeError('test failure')
        with mock.patch.object(W, 'claim_job', return_value=True), mock.patch.object(W, '_run_job', side_effect=fail):
            with self.assertRaises(RuntimeError): W.run_job('test-token', {'id': '82924ff33f5f'})
        self.assertIsNone(W.ACTIVE_RENDER_JOB)

    def test_status_is_kept_for_the_existing_heartbeat_thread(self):
        with mock.patch.object(W, '_api'):
            W.post_gpu_status('test-token', {'state': 'rendering', 'detail': 'rendering job 82924ff33f5f'})
        self.assertEqual(W.LAST_GPU_STATUS['detail'], 'rendering job 82924ff33f5f')

    def test_losing_the_claim_touches_no_gpu(self):
        with mock.patch.object(W, 'claim_job', return_value=False), mock.patch.object(W, '_run_job') as render:
            W.run_job('test-token', {'id': '82924ff33f5f'})
        render.assert_not_called()

    def test_claim_names_the_host_and_requires_explicit_success(self):
        with mock.patch.object(W, '_api', return_value=b'{"ok":true}') as call:
            self.assertTrue(W.claim_job('test-token','82924ff33f5f'))
        self.assertIn('/claim?host='+W.FLEET_HOST, call.call_args.args[0])
        self.assertEqual(call.call_args.kwargs['data'], b'')
        with mock.patch.object(W, '_api', return_value=b'{"ok":false}'):
            self.assertFalse(W.claim_job('test-token','82924ff33f5f'))

    def test_claim_outage_is_not_permission_to_render(self):
        with mock.patch.object(W, '_api', side_effect=OSError('test offline')):
            self.assertFalse(W.claim_job('test-token','82924ff33f5f'))
