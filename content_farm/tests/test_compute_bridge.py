import importlib.util,sys,tempfile,json,unittest
from pathlib import Path
from unittest.mock import patch,Mock
import compute_bridge
class ComputeBridgeTests(unittest.TestCase):
 def test_validated_words_and_finite_bounds(self):
  good={'language':'ru','text':'Пример','segments':[{'start':0,'end':2,'text':'Пример','words':[{'start':0,'end':1,'word':'Пример'}]}]}
  self.assertIs(compute_bridge.validate_result(good,3),good)
  for value in (-1,99,float('nan')):
   bad=json.loads(json.dumps(good));bad['segments'][0]['words'][0]['start']=value
   with self.assertRaises(ValueError):compute_bridge.validate_result(bad,3)
 def test_prefetch_skips_chunk_possibly_running_on_server(self):
  with tempfile.TemporaryDirectory() as d:
   folder=Path(d);(folder/'state.json').write_text(json.dumps({'status':'processing'}));(folder/'source.mp4').write_bytes(b'video')
   (folder/'transcript_part_0000.json').write_text(json.dumps({'language':'ru'}))
   with patch.object(compute_bridge.subprocess,'run',return_value=Mock(stdout='{"format":{"duration":"1800"}}')):
    source,duration,index,language=compute_bridge.tasks(folder)
   self.assertEqual(index,2);self.assertEqual(language,'ru')
 def test_only_final_current_chunk_stays_on_cpu(self):
  with tempfile.TemporaryDirectory() as d:
   folder=Path(d);(folder/'state.json').write_text(json.dumps({'status':'processing'}));(folder/'source.mp4').write_bytes(b'video')
   (folder/'transcript_part_0000.json').write_text(json.dumps({'language':'ru'}))
   with patch.object(compute_bridge.subprocess,'run',return_value=Mock(stdout='{"format":{"duration":"1200"}}')):self.assertIsNone(compute_bridge.tasks(folder))
 def test_ready_or_nonprocessing_jobs_never_enter_compute(self):
  with tempfile.TemporaryDirectory() as d:
   folder=Path(d);(folder/'state.json').write_text(json.dumps({'status':'queued'}))
   self.assertIsNone(compute_bridge.tasks(folder))
   (folder/'state.json').write_text(json.dumps({'status':'processing'}));(folder/'transcript.json').write_text('{}')
   self.assertIsNone(compute_bridge.tasks(folder))
 def test_server_checkpoint_wins_if_it_finishes_while_pc_is_working(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);folder=root/'abcdef123456';folder.mkdir();work=root/'work';work.mkdir()
   state={'status':'processing'};(folder/'state.json').write_text(json.dumps(state));source=folder/'source.mp4';source.write_bytes(b'video')
   target=folder/'transcript_part_0002.json';pc=Mock()
   def remote(*args):
    target.write_text('server-output');return {'_compute':{'inference_seconds':1},'segments':[]}
   pc.transcribe.side_effect=remote
   def ffmpeg(argv,**kwargs):Path(argv[-1]).write_bytes(b'audio')
   with patch.object(compute_bridge,'tasks',return_value=(source,1800,2,'ru')),patch.object(compute_bridge.subprocess,'run',side_effect=ffmpeg):
    self.assertFalse(compute_bridge.prefetch(pc,folder,work))
   self.assertEqual(target.read_text(),'server-output');self.assertEqual(json.loads((folder/'state.json').read_text()),state);self.assertFalse(list(work.iterdir()))

 def test_final_missing_chunk_is_prefetched_when_cpu_cursor_is_proven(self):
  import hashlib
  with tempfile.TemporaryDirectory() as d:
   folder=Path(d);(folder/'state.json').write_text(json.dumps({'status':'processing'}));(folder/'source.mp4').write_bytes(b'video');(folder/'speech_chunk.wav').write_bytes(b'current-audio')
   raw={'language':'ru','_compute':{'input_sha256':hashlib.sha256(b'current-audio').hexdigest()}}
   (folder/'transcript_part_0000.json').write_text(json.dumps(raw));(folder/'compute-progress.json').write_text(json.dumps({'part':0}))
   with patch.object(compute_bridge.subprocess,'run',return_value=Mock(stdout='{"format":{"duration":"1200"}}')):self.assertEqual(compute_bridge.tasks(folder)[2],1)
