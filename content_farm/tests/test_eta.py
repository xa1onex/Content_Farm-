import unittest,tempfile,json
from pathlib import Path
from unittest.mock import patch
from eta import estimate,measured_ratio,remaining,observe
class TimingTests(unittest.TestCase):
 def row(self,**kw):return dict({'id':'a'*12,'status':'processing','created':'2026-11-06T10:00:00Z','duration':10800,'source_ready':True,'parts':6,'part_times':[100,400,700],'requested':3},**kw)
 def test_actual_chunk_speed_drives_remaining_not_entire_original(self):
  row=self.row();self.assertEqual(measured_ratio([row]),.5)
  lo,hi,_,confidence=remaining(row,.5)
  self.assertEqual(confidence,'measured');self.assertLess(lo,10800*.5);self.assertGreater(hi,7200*.5)
 def test_fifo_waits_include_processing_even_if_it_was_submitted_later(self):
  active=self.row();second=self.row(id='b'*12,status='queued',created='2026-11-06T09:00:00Z')
  result=estimate([second,active],at=1000)['items']
  self.assertEqual([r['job_id'] for r in result],['a'*12,'b'*12]);self.assertGreater(result[1]['wait_min_seconds'],0)
  self.assertEqual(result[1]['remaining_min_seconds'],result[1]['wait_min_seconds']+result[0]['remaining_min_seconds'])
 def test_finished_jobs_do_not_delay_queue(self):
  result=estimate([self.row(status='completed'),self.row(id='b'*12,status='queued')],at=1000)['items']
  self.assertEqual(len(result),1);self.assertEqual(result[0]['wait_max_seconds'],0)
 def test_unknown_length_is_explicitly_rough_and_wider(self):
  known=remaining(self.row(duration=3600),.5);unknown=remaining(self.row(duration=0),.5)
  self.assertEqual(unknown[-1],'rough');self.assertGreater(unknown[1],known[1])
 def test_transcript_cache_and_finished_renders_skip_expensive_stages(self):
  row=self.row(transcribed=True,selected=3,rendered=3,manifest=True)
  self.assertEqual(remaining(row,.5)[2],'upload');self.assertLess(remaining(row,.5)[1],1000)
 def test_future_retry_is_included(self):
  item=estimate([self.row(status='retry_wait',retry_at=1600)],at=1000)['items'][0]
  self.assertEqual(item['wait_min_seconds'],600)
 def test_observer_is_read_only_and_counts_only_completed_render_receipts(self):
  with tempfile.TemporaryDirectory() as root:
   p=Path(root);state={'id':'a'*12,'status':'processing','clip_outputs':{'0':{'file':'CF-X.mp4'},'1':{'file':'missing.mp4'}}}
   original=json.dumps(state);(p/'state.json').write_text(original);(p/'CF-X.mp4').write_bytes(b'clip');(p/'short_02.mp4').write_bytes(b'unfinished')
   observed=observe(p,{'duration':100});self.assertEqual(observed['rendered'],1);self.assertEqual((p/'state.json').read_text(),original)
