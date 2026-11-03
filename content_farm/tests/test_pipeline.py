import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch,MagicMock,AsyncMock

import bot
import processor
import autopost
from clip_identity import checksum,clip_code,finish_output,normalize_job
from drive_helper import archive_job,save_private_json

class IdentityTests(unittest.TestCase):
    def job(self,p):
        save_private_json(p/'state.json',{'id':'0123456789ab','user_id':7,'source':{'kind':'youtube','value':'https://youtube.com/example'}})
    def test_same_bytes_have_same_code_even_in_different_jobs(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);self.job(p);video=p/'short_01.mp4';video.write_bytes(b'clip')
            clip=finish_output(p,0,video,{'start_time':0,'end_time':20,'description':'Test'})
            self.assertEqual(clip['id'],clip_code(hashlib.sha256(b'clip').hexdigest()))
            self.assertEqual(clip['file'],clip['id']+'.mp4')
            self.assertTrue((p/clip['file']).exists());self.assertFalse(video.exists())
            save_private_json(p/'manifest.json',{'clips':[clip]})
            self.assertEqual(normalize_job(p)['clips'][0]['id'],clip['id'])
    def test_changed_confirmed_bytes_do_not_keep_same_identity(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);self.job(p);video=p/'short_01.mp4';video.write_bytes(b'clip')
            finish_output(p,0,video,{'start_time':0,'end_time':20})
            video.write_bytes(b'changed')
            with self.assertRaises(ValueError):finish_output(p,0,video,{'start_time':0,'end_time':20})
    def test_legacy_migration_preserves_drive_and_telegram_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);self.job(p);(p/'short_01.mp4').write_bytes(b'clip')
            state=json.loads((p/'state.json').read_text())
            state.update(drive_files={'short_01.mp4':{'id':'drive-1','link':'https://drive.test/1'}},telegram_files={'short_01.mp4':'tg-1'},delivered_files=['short_01.mp4'])
            save_private_json(p/'state.json',state)
            save_private_json(p/'manifest.json',{'clips':[{'file':'short_01.mp4','start_time':0,'end_time':20}]})
            normalized=normalize_job(p);clip=normalized['clips'][0]
            new=json.loads((p/'state.json').read_text())
            self.assertEqual(new['drive_files'][clip['file']]['id'],'drive-1')
            self.assertEqual(new['telegram_files'][clip['file']],'tg-1')
            self.assertEqual(new['delivered_files'],[clip['file']])
            self.assertEqual(normalize_job(p),normalized)
    def test_legacy_cleaned_job_uses_drive_hash_without_redownload(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);self.job(p)
            state=json.loads((p/'state.json').read_text());state['drive_files']={'short_01.mp4':{'id':'remote','link':'url'}}
            save_private_json(p/'state.json',state)
            save_private_json(p/'manifest.json',{'clips':[{'file':'short_01.mp4','start_time':0,'end_time':20}]})
            result=normalize_job(p,lambda file:{'sha256Checksum':'a'*64})
            self.assertEqual(result['clips'][0]['id'],'CF-'+'A'*32)
    def test_resume_after_state_rename_before_manifest_upgrade(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);self.job(p);sha=hashlib.sha256(b'clip').hexdigest();name=clip_code(sha)+'.mp4'
            (p/name).write_bytes(b'clip')
            state=json.loads((p/'state.json').read_text());state['clip_outputs']={'0':{'file':name,'sha256':sha}}
            save_private_json(p/'state.json',state)
            save_private_json(p/'manifest.json',{'clips':[{'file':'short_01.mp4','start_time':0,'end_time':20}]})
            self.assertEqual(normalize_job(p)['clips'][0]['file'],name)

class ArchiveTests(unittest.TestCase):
    def setup_job(self,p):
        save_private_json(p/'state.json',{'id':'0123456789ab','user_id':7})
        (p/'short_01.mp4').write_bytes(b'clip')
        save_private_json(p/'manifest.json',{'clips':[{'file':'short_01.mp4','start_time':0,'end_time':20}]})
    def test_retry_reuses_id_and_syncs_drive_name_once(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);self.setup_job(p)
            drive=MagicMock();drive.job_folder.return_value='folder';drive.upload.side_effect=lambda path,parent,file_id=None:{'id':file_id or path.name,'link':'url'}
            with patch('drive_helper.DriveStorage',return_value=drive),patch.object(autopost,'AUTOPOST_ENABLED',False):
                archive_job(p);count=drive.upload.call_count;archive_job(p)
            self.assertEqual(drive.upload.call_count,count)
            self.assertEqual(drive.service.files().update.call_count,1)
            clip=json.loads((p/'manifest.json').read_text())['clips'][0]
            self.assertEqual(clip['drive_file_id'],clip['file'])
    def test_existing_content_is_reused_without_second_media_upload(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);self.setup_job(p);sha=hashlib.sha256(b'clip').hexdigest()
            drive=MagicMock();drive.job_folder.return_value='folder';drive.metadata.return_value={'sha256Checksum':sha}
            drive.upload.side_effect=lambda path,parent,file_id=None:{'id':path.name,'link':'url'}
            with patch('drive_helper.DriveStorage',return_value=drive),patch.object(autopost,'find_existing_clip',return_value={'drive_file_id':'already-uploaded'}):archive_job(p)
            self.assertFalse(any(args.args[0].suffix=='.mp4' for args in drive.upload.call_args_list))
            clip=json.loads((p/'manifest.json').read_text())['clips'][0]
            self.assertEqual(clip['drive_file_id'],'already-uploaded')

class GeminiTests(unittest.TestCase):
    def test_transient_api_error_is_not_bad_video_and_has_bounded_retries(self):
        from google.genai.errors import ServerError
        error=ServerError(503,{'error':{'code':503,'message':'overloaded','status':'UNAVAILABLE'}})
        client=MagicMock();client.__enter__.return_value=client
        client.models.generate_content.side_effect=error
        with patch.dict('os.environ',{'GEMINI_API_KEY':'test-only'}),patch('google.genai.Client',return_value=client),patch.object(processor.time,'sleep'):
            with self.assertRaises(processor.TransientProcessingError):
                processor.analyze_with_gemini({'segments':[{'start':0,'end':20,'text':'Test'}]},20)
        self.assertEqual(client.models.generate_content.call_count,3)
    def test_nonretryable_api_error_does_not_loop(self):
        from google.genai.errors import ClientError
        error=ClientError(403,{'error':{'code':403,'message':'denied'}})
        client=MagicMock();client.__enter__.return_value=client;client.models.generate_content.side_effect=error
        with patch.dict('os.environ',{'GEMINI_API_KEY':'test-only'}),patch('google.genai.Client',return_value=client):
            with self.assertRaises(processor.ProcessingError):processor.analyze_with_gemini({'segments':[]},20)
        self.assertEqual(client.models.generate_content.call_count,1)

class BridgeTests(unittest.TestCase):
    def test_pending_request_replays_same_key_and_body_after_lost_response(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);tokens=p/'keys.json';save_private_json(tokens,{'farm_token':'test-only-token'})
            with patch.object(autopost,'AUTOPOST_TOKEN_FILE',tokens):client=autopost.Client(p)
            client.session=MagicMock();client.session.post.side_effect=[OSError('lost reply'),SimpleNamespace(status_code=201,json=lambda:{'id':'clip-1'})]
            body={'passport':'CF-'+'A'*32}
            with self.assertRaises(OSError):client.post('/content',body,'content')
            self.assertEqual(client.post('/content',body,'content'),{'id':'clip-1'})
            self.assertEqual(client.session.post.call_args_list[0],client.session.post.call_args_list[1])
            self.assertNotIn('test-only-token',(p/'autopost_requests.json').read_text())
    def test_conflicting_payload_is_not_silently_replayed(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);tokens=p/'keys.json';save_private_json(tokens,{'farm_token':'test-only-token'})
            with patch.object(autopost,'AUTOPOST_TOKEN_FILE',tokens):client=autopost.Client(p)
            client.session=MagicMock();client.session.post.side_effect=OSError('lost')
            with self.assertRaises(OSError):client.post('/content',{'title':'one'},'content')
            with self.assertRaises(autopost.RegistrationError):client.post('/content',{'title':'two'},'content')

class RetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_persisted_retry_is_queued_once_and_future_retry_waits(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for job_id,retry_at in [('due',0),('future',10**12)]:
                save_private_json(root/job_id/'state.json',{'id':job_id,'user_id':7 if job_id=='due' else 8,'status':'retry_wait','retry_at':retry_at})
            q=asyncio.Queue()
            rounds=0
            async def tick(_):
                nonlocal rounds
                rounds+=1
                if rounds==2:raise asyncio.CancelledError()
            with patch.object(bot,'JOB_DIR',root),patch.object(bot,'queue',q),patch.object(bot,'busy_users',set()),patch.object(bot,'scheduled',set()),patch.object(bot.asyncio,'sleep',tick):
                with self.assertRaises(asyncio.CancelledError):await bot.delayed_retries()
                self.assertEqual(q.qsize(),1)
                self.assertEqual(q.get_nowait().name,'due')
            self.assertEqual(json.loads((root/'due/state.json').read_text())['status'],'queued')
            self.assertEqual(json.loads((root/'future/state.json').read_text())['status'],'retry_wait')

    async def test_transient_failure_persists_retry_without_losing_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);save_private_json(p/'state.json',{'id':'0123456789ab','user_id':7,'chat_id':7,'source':{'kind':'youtube','value':'url'},'status':'queued'})
            (p/'transcript.json').write_text('{}')
            q=asyncio.Queue();await q.put(p)
            fake=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(edit_text=AsyncMock())))
            with patch.object(bot,'queue',q),patch.object(bot,'process_job',side_effect=processor.TransientProcessingError('overloaded')):
                task=asyncio.create_task(bot.worker(fake));await asyncio.wait_for(q.join(),5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
            state=json.loads((p/'state.json').read_text())
            self.assertEqual(state['status'],'retry_wait');self.assertEqual(state['auto_retry_count'],1)
            self.assertTrue(state['retry_at']>0);self.assertTrue((p/'transcript.json').exists())
