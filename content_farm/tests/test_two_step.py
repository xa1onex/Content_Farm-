import asyncio,json,tempfile,unittest,uuid
from pathlib import Path
from types import SimpleNamespace
from collections import defaultdict
from unittest.mock import patch,MagicMock,AsyncMock
import processor,bot
from eta import remaining
from drive_helper_json import save_private_json

class TwoStepTests(unittest.TestCase):
 def test_analysis_stops_before_render_and_confirmed_count_reuses_cached_source_and_transcript(self):
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);p=root/'abc123abc123';p.mkdir()
   save_private_json(p/'state.json',{'id':p.name,'user_id':7,'status':'processing','analysis_only':True,'source':{'kind':'youtube','value':'https://youtu.be/abcdefghijk'},'details':{'title':'Test'}})
   (p/'source.mp4').write_bytes(b'cached');(p/'transcript.json').write_text('{"segments":[]}')
   first=[{'start_time':10,'end_time':30,'description':'One'}];second=[{'start_time':40,'end_time':60,'description':'Two'}]
   def build(video,start,end,i,where,transcript):
    final=where/f'short_{i+1:02d}.mp4';final.write_bytes(b'test clip');return final
   with patch('config.JOB_DIR',root),patch.object(processor,'validate_source',return_value=120),patch.object(processor,'transcribe') as transcription,patch.object(processor,'download_youtube_video') as download,patch.object(processor,'analyze_candidates',side_effect=[(first,1),(second,1)]) as model,patch.object(processor,'build_short',side_effect=build) as render,patch('clip_identity.finish_output',side_effect=lambda folder,i,final,segment:dict(segment,file=final.name)):
    with self.assertRaises(processor.AwaitingSelection):processor.process_job(p)
    render.assert_not_called();transcription.assert_not_called();download.assert_not_called()
    state=json.loads((p/'state.json').read_text());self.assertEqual(state['analysis']['selected'],1)
    state.update(clip_count=2,selection_confirmed=True);save_private_json(p/'state.json',state)
    result=processor.process_job(p)
    self.assertEqual(len(result['clips']),2);self.assertEqual(render.call_count,2)
    self.assertEqual(model.call_args.kwargs['count'],1);self.assertEqual(model.call_args.kwargs['excluded'],first)
    transcription.assert_not_called();download.assert_not_called()
 def test_render_confirmation_is_idempotent_and_checks_owner(self):
  async def run():
   with tempfile.TemporaryDirectory() as folder:
    root=Path(folder);p=root/'abc123abc123';p.mkdir();save_private_json(p/'state.json',{'id':p.name,'user_id':7,'status':'analysis_ready'})
    save_private_json(p/'selection.json',[{'start_time':10,'end_time':30}]);q=asyncio.Queue()
    message=SimpleNamespace(from_user=SimpleNamespace(id=7),chat=SimpleNamespace(id=7,type='private'),text='',answer=AsyncMock())
    with patch.object(bot,'JOB_DIR',root),patch.object(bot,'queue',q),patch.object(bot,'scheduled',set()),patch.object(bot,'busy_users',set()),patch.object(bot,'user_locks',defaultdict(asyncio.Lock)):
     request=str(uuid.uuid4());first=await bot.submit(message,None,request_id=request,render_job_id=p.name,clip_count=4)
     second=await bot.submit(message,None,request_id=request,render_job_id=p.name,clip_count=4)
     self.assertEqual(first['job_id'],second['job_id']);self.assertEqual(q.qsize(),1)
     message.from_user.id=8
     with self.assertRaisesRegex(ValueError,'другому'):await bot.submit(message,None,request_id=str(uuid.uuid4()),render_job_id=p.name,clip_count=4)
  asyncio.run(run())
 def test_analysis_forecast_excludes_render_upload_and_delivery(self):
  row={'duration':3600,'source_ready':True,'transcribed':True,'selected':None,'requested':8}
  full=remaining(row,.5);analysis=remaining(dict(row,analysis_only=True),.5)
  self.assertLess(analysis[1],full[1]);self.assertEqual(analysis[2],'analyze')
