import asyncio
import json
import base64
import hashlib
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs,urlparse

import bot
import processor
from drive_helper import DriveStorage, DriveConfigurationError, save_private_json, archive_job
from sources import parse_source
import setup_drive

class SelectionTests(unittest.TestCase):
    def test_bounds_overlap_nan_and_duration(self):
        data=[{'start_time':-1,'end_time':20},
            {'start_time':0,'end_time':14},
            {'start_time':0,'end_time':61},
            {'start_time':float('nan'),'end_time':30},
            {'start_time':False,'end_time':20},
            {'start_time':0,'end_time':20},
            {'start_time':15,'end_time':35},
            {'start_time':20,'end_time':40},
            {'start_time':50,'end_time':100}]
        selected=processor.validate_segments(data,90)
        self.assertEqual([(s['start_time'],s['end_time']) for s in selected],[(0,20),(20,40)])

    def test_no_arbitrary_fallback(self):
        with self.assertRaises(ValueError):
            processor.validate_segments([{'start_time':3,'end_time':7}],20)

    def test_models_are_preserved(self):
        self.assertEqual(processor.GEMINI_MODEL,'gemini-3.5-flash')
        self.assertEqual(processor.WHISPER_MODEL,'base')

class URLTests(unittest.TestCase):
    def test_youtube_canonical(self):
        self.assertEqual(parse_source('Видео https://youtu.be/jNQXAC9IVRw?t=3'),
            ('youtube','https://www.youtube.com/watch?v=jNQXAC9IVRw'))
        self.assertEqual(parse_source('https://youtube.com/shorts/jNQXAC9IVRw')[0],'youtube')

    def test_drive(self):
        self.assertEqual(parse_source('https://drive.google.com/file/d/1234567890abcd/view'),
            ('drive','1234567890abcd'))

    def test_reject_arbitrary_urls_and_playlists(self):
        for url in ['http://127.0.0.1/private','https://youtube.com.attacker.test/?v=jNQXAC9IVRw',
            'https://youtube.com/playlist?list=123','https://user:pass@youtube.com/watch?v=jNQXAC9IVRw',
            'https://drive.google.com/drive/folders/1234567890abcd',
            'https://youtu.be/jNQXAC9IVRw https://youtu.be/jNQXAC9IVRw']:
            with self.subTest(url=url),self.assertRaises(ValueError):parse_source(url)

class SubtitlesTests(unittest.TestCase):
    def test_overlapping_transcription_chunks_keep_global_timestamps(self):
        data={'segments':[{'words':[
            {'word':'контекст','start':0,'end':1},
            {'word':'новое','start':2,'end':3},
            {'word':'слово','start':10,'end':11},
            {'word':'позже','start':14,'end':15}]}]}
        result=processor.merge_chunk_segments(data,598,600,612,620)
        self.assertEqual([w['word'] for w in result[0]['words']],['новое','слово'])
        self.assertEqual(result[0]['start'],600)
        self.assertEqual(result[0]['end'],609)

    def test_empty_words_commands_and_clip_bounds(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'s.ass'
            processor.generate_tiktok_ass([
                {'word':'','start':0,'end':1},
                {'word':'Привет{\\pos(0,0)}','start':-1,'end':1},
                {'word':'мир!','start':1,'end':2},
                {'word':'позже','start':4,'end':5}],path,duration=3)
            text=path.read_text()
            self.assertNotIn('позже'.upper(),text)
            self.assertNotIn('{\\pos(0,0)}',text)
            self.assertIn('ПРИВЕТ',text)
            self.assertIn('0:00:00.00',text)

    def test_centisecond_rounding(self):
        self.assertEqual(processor.format_ass_time(59.999),'0:01:00.00')

class StorageTests(unittest.TestCase):
    def test_oauth_link_requests_offline_access_with_pkce_and_state(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);client=p/'client.json';pending=p/'pending.json'
            save_private_json(client,{'installed':{'client_id':'test.apps.googleusercontent.com',
                'client_secret':'test-only','auth_uri':'https://accounts.google.com/o/oauth2/auth',
                'token_uri':'https://oauth2.googleapis.com/token','redirect_uris':['http://localhost']}})
            out=io.StringIO()
            with patch.object(setup_drive,'GDRIVE_CLIENT_FILE',client),patch.object(setup_drive,'PENDING',pending),patch('sys.stdout',out):
                setup_drive.auth_url()
            url=next(line for line in out.getvalue().splitlines() if line.startswith('https://'))
            query=parse_qs(urlparse(url).query);saved=json.loads(pending.read_text())
            challenge=base64.urlsafe_b64encode(hashlib.sha256(saved['code_verifier'].encode()).digest()).rstrip(b'=').decode()
            self.assertEqual(query['state'][0],saved['state'])
            self.assertEqual(query['code_challenge'][0],challenge)
            self.assertEqual(query['code_challenge_method'][0],'S256')
            self.assertEqual(query['access_type'][0],'offline')
            self.assertEqual(query['redirect_uri'][0],'http://localhost:8765/')
            self.assertEqual(pending.stat().st_mode & 0o777,0o600)

    def test_my_drive_service_account_needs_oauth(self):
        drive=DriveStorage.__new__(DriveStorage)
        drive.oauth=False
        drive.root={'capabilities':{'canAddChildren':True}}
        with self.assertRaises(DriveConfigurationError):drive.check_writable()
        drive.root['driveId']='shared-drive'
        drive.check_writable()

    def test_keep_local_files_until_archive_and_delivery(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); (p/'short_01.mp4').write_bytes(b'video')
            save_private_json(p/'manifest.json',{})
            save_private_json(p/'state.json',{'archived':False,'delivered':True})
            bot.clean_archived_job(p)
            self.assertTrue((p/'short_01.mp4').exists())
            bot.update_state(p,archived=True,delivered=False)
            bot.clean_archived_job(p)
            self.assertTrue((p/'short_01.mp4').exists())
            bot.update_state(p,delivered=True)
            bot.clean_archived_job(p)
            self.assertFalse((p/'short_01.mp4').exists())
            self.assertTrue((p/'manifest.json').exists())

    def test_archive_retry_does_not_upload_twice(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'source.mp4').write_bytes(b'video');(p/'short_01.mp4').write_bytes(b'clip')
            save_private_json(p/'state.json',{'id':'job','user_id':7})
            fake=MagicMock();fake.job_folder.return_value='folder'
            fake.upload.side_effect=lambda path,folder:{'id':path.name,'link':'https://drive.google.com/f/'+path.name}
            with patch('drive_helper.DriveStorage',return_value=fake):
                archive_job(p);archive_job(p)
            self.assertEqual(fake.upload.call_count,2)

    def test_user_isolation(self):
        with tempfile.TemporaryDirectory() as d,patch.object(bot,'JOB_DIR',Path(d)):
            for i in [1,2]:
                p=Path(d)/str(i);p.mkdir()
                save_private_json(p/'state.json',{'user_id':i,'id':str(i),'created':str(i)})
            self.assertEqual([s['id'] for _,s in bot.user_jobs(1)],['1'])

class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_interrupted_telegram_download_keeps_no_partial_source(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            async def interrupted(*args,**kwargs):
                Path(kwargs['destination']).write_bytes(b'partial')
                raise OSError('interrupted')
            client=SimpleNamespace(download=interrupted)
            with self.assertRaises(OSError):
                await bot.download_telegram(client,p,{'value':'tg-file','size':100})
            self.assertFalse((p/'source.mp4').exists())
            self.assertFalse((p/'source.incoming').exists())

    async def test_completed_progress_cannot_be_overwritten(self):
        msg=SimpleNamespace(edit_text=AsyncMock())
        reporter=bot.ProgressReporter(asyncio.get_running_loop(),msg)
        reporter.done=True
        await reporter.edit('obsolete progress')
        msg.edit_text.assert_not_called()
        await reporter.edit('completed',final=True)
        msg.edit_text.assert_awaited_once_with('completed')

    async def run_job(self,archive_fails=False,delivery_fails=False):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'short_01.mp4').write_bytes(b'clip')
            save_private_json(p/'state.json',{'id':'0123456789ab','user_id':7,'chat_id':7,
                'source':{'kind':'youtube','value':'test'},
                'status':'queued','archived':False,'delivered':False})
            save_private_json(p/'manifest.json',{'clips':[{'file':'short_01.mp4','description':'A < B',
                'start_time':0,'end_time':20}]})
            q=asyncio.Queue();await q.put(p)
            status=SimpleNamespace(edit_text=AsyncMock())
            fake_bot=SimpleNamespace(send_message=AsyncMock(return_value=status),
                send_video=AsyncMock(return_value=SimpleNamespace(video=SimpleNamespace(file_id='tg-file'))))
            if delivery_fails:
                fake_bot.send_video.side_effect=OSError('upload failed')
            def archive(path):
                if archive_fails:raise DriveConfigurationError('needs OAuth')
                return bot.update_state(path,archived=True)
            with patch.object(bot,'queue',q),patch.object(bot,'archive_job',side_effect=archive):
                task=asyncio.create_task(bot.worker(fake_bot))
                await asyncio.wait_for(q.join(),timeout=5)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
            return bot.load_state(p),(p/'short_01.mp4').exists(),fake_bot.send_video.call_args

    async def test_success_cleans_confirmed_files(self):
        state,exists,args=await self.run_job()
        self.assertEqual(state['status'],'completed')
        self.assertFalse(exists)
        self.assertEqual(args.kwargs['caption'],'A &lt; B')

    async def test_drive_failure_delivers_but_keeps_files(self):
        state,exists,_=await self.run_job(archive_fails=True)
        self.assertEqual(state['status'],'needs_retry')
        self.assertTrue(state['delivered']);self.assertTrue(exists)

    async def test_telegram_failure_keeps_files(self):
        state,exists,_=await self.run_job(delivery_fails=True)
        self.assertEqual(state['status'],'needs_retry')
        self.assertFalse(state['delivered']);self.assertTrue(exists)

if __name__=='__main__':unittest.main()
