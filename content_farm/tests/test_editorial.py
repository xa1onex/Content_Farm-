import asyncio,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,MagicMock,AsyncMock
import processor,bot
from drive_helper_json import save_private_json
class EditorialTests(unittest.TestCase):
 def test_selection_can_exceed_three_and_never_reuse_prior_intervals(self):
  rows=[{'start_time':i*30,'end_time':i*30+20,'description':str(i)} for i in range(8)]
  selected=processor.validate_segments(rows,300,limit=7,excluded=[rows[0]])
  self.assertEqual(len(selected),7);self.assertEqual(selected[0]['start_time'],30)
 def test_feedback_and_deep_search_are_in_the_actual_model_prompt(self):
  client=MagicMock();client.__enter__.return_value=client
  client.models.generate_content.return_value.text=json.dumps([{'start_time':30,'end_time':50,'description':'Complete story'}])
  with patch.dict('os.environ',{'GEMINI_API_KEY':'test-only'}),patch('google.genai.Client',return_value=client):
   result=processor.analyze_with_gemini({'segments':[{'start':0,'end':60,'text':'Speech'}]},60,count=5,excluded=[{'start_time':0,'end_time':20}],feedback=[{'rating':-1,'comment':'Начало без контекста'}])
  prompt=client.models.generate_content.call_args.kwargs['contents']
  self.assertIn('Начало без контекста',prompt);self.assertIn('Deep search',prompt);self.assertIn('previously used',prompt);self.assertEqual(len(result),1)
 def test_multiple_jobs_from_same_user_are_durably_queued(self):
  with tempfile.TemporaryDirectory() as d:
   q=asyncio.Queue(maxsize=10);message=SimpleNamespace(from_user=SimpleNamespace(id=7),chat=SimpleNamespace(id=7,type='private'),video=None,document=None,text='https://youtu.be/abcdefghijk',answer=AsyncMock())
   with patch.object(bot,'JOB_DIR',Path(d)),patch.object(bot,'queue',q),patch.object(bot,'busy_users',{7}),patch.object(bot,'last_submit',{7:10**12}):
    first=asyncio.run(bot.submit(message,MagicMock()));second=asyncio.run(bot.submit(message,MagicMock()))
   self.assertNotEqual(first['job_id'],second['job_id']);self.assertEqual(q.qsize(),2)
 def test_retries_reuse_existing_job_instead_of_making_duplicate(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'abc123abc123';p.mkdir();save_private_json(p/'state.json',{'id':p.name,'user_id':7,'status':'failed'})
   q=asyncio.Queue(maxsize=10);message=SimpleNamespace(from_user=SimpleNamespace(id=7),chat=SimpleNamespace(id=7,type='private'),text='',answer=AsyncMock())
   with patch.object(bot,'JOB_DIR',Path(d)),patch.object(bot,'queue',q),patch.object(bot,'scheduled',set()):
    first=asyncio.run(bot.submit(message,MagicMock(),retry_job_id=p.name));second=asyncio.run(bot.submit(message,MagicMock(),retry_job_id=p.name))
   self.assertEqual(first['job_id'],second['job_id']);self.assertEqual(q.qsize(),1)
 def test_feedback_buttons_fit_telegram_callback_limit(self):
  markup=bot.feedback_keyboard('CF-'+'A'*32)
  self.assertTrue(all(len(b.callback_data.encode())<=64 for b in markup.inline_keyboard[0]))
 def test_long_source_rejection_is_explicit_before_download(self):
  client=MagicMock();client.__enter__.return_value=client;client.extract_info.return_value={'duration':processor.MAX_SOURCE_SECONDS+1}
  with tempfile.TemporaryDirectory() as d,patch('yt_dlp.YoutubeDL',return_value=client):
   with self.assertRaisesRegex(processor.ProcessingError,'длинное'):processor.download_youtube_video('https://youtu.be/abcdefghijk',d)
  self.assertEqual(client.extract_info.call_count,1)

 def test_requested_count_triggers_additional_pass_when_first_search_is_short(self):
  first=[{'start_time':0,'end_time':20,'description':'one'}];second=[{'start_time':30,'end_time':50,'description':'two'}]
  with patch.object(processor,'analyze_with_gemini',side_effect=[first,second]) as model:
   clips,passes=processor.analyze_candidates({},100,count=5)
  self.assertEqual(passes,2);self.assertEqual(len(clips),2)
  self.assertEqual(model.call_args.kwargs['count'],4);self.assertEqual(model.call_args.kwargs['excluded'],first)
