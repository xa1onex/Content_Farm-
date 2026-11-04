import unittest
from operations import recovery_changes

class RecoveryTests(unittest.TestCase):
    def test_completed_media_retries_are_bounded_and_keep_state(self):
        state={'id':'clip-job','archived':True,'registered':False,'pipeline_retry_count':0}
        for expected in (60,180,600,1800,3600):
            state.update(recovery_changes(state,1000))
            self.assertEqual(state['retry_at'],1000+expected)
            self.assertEqual(state['status'],'retry_wait')
            self.assertTrue(state['archived'])
        state.update(recovery_changes(state,1000))
        self.assertEqual(state['status'],'needs_retry')

    def test_report_closes_http_session_and_uses_farm_endpoint(self):
        from unittest.mock import MagicMock,patch
        from operations import send_report
        client=MagicMock();client.base='https://example.test/api/v1'
        with patch('autopost.Client',return_value=client):send_report({'counts':{'completed':1}})
        self.assertEqual(client.session.post.call_args.args[0],'https://example.test/api/v1/farm/heartbeat')
        self.assertEqual(client.session.post.call_args.kwargs['json'],{'counts':{'completed':1}})
        client.close.assert_called_once()
