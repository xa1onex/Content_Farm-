import unittest
from transcripts import clip_transcript
class TranscriptTests(unittest.TestCase):
 def test_exact_cut_uses_same_overlapping_words_as_subtitles(self):
  source={'language':'ru','segments':[{'text':'Не использовать весь сегмент','words':[
   {'start':0,'end':9,'word':'Снаружи'}, {'start':9,'end':11,'word':' Первое'},
   {'start':11,'end':12,'word':','},{'start':12,'end':20,'word':' слово.'},
   {'start':20,'end':22,'word':'За пределами'}]}]}
  result=clip_transcript(source,10,20)
  self.assertEqual(result['text'],'Первое, слово.');self.assertEqual(result['language'],'ru')
  self.assertEqual(result['start_seconds'],10);self.assertEqual(result['end_seconds'],20)
 def test_fallback_does_not_include_neighboring_speech_without_word_times(self):
  result=clip_transcript({'segments':[{'start':0,'end':15,'text':'Лишний контекст'},
   {'start':15,'end':18,'text':'  Только   речь. '},{'start':18,'end':30,'text':'Соседний момент'}]},10,20)
  self.assertEqual(result['text'],'Только речь.')
 def test_missing_speech_is_empty_not_title_or_full_source(self):
  self.assertEqual(clip_transcript({'text':'Всё длинное видео','segments':[]},1,20)['text'],'')
 def test_invalid_times_are_rejected_and_malformed_words_are_skipped(self):
  for start,end in [(float('nan'),2),(1,float('inf')),(2,1),(-1,2)]:
   with self.assertRaises(ValueError):clip_transcript({},start,end)
  result=clip_transcript({'segments':[{'words':[{'start':None,'end':2,'word':'bad'},
   {'start':1,'end':float('nan'),'word':'bad'},{'start':1,'end':2,'word':'Хорошо.'}]}]},0,3)
  self.assertEqual(result['text'],'Хорошо.')
 def test_actual_registration_carries_spoken_text_as_metadata_not_description(self):
  import tempfile,json
  from pathlib import Path
  from unittest.mock import MagicMock,patch
  import autopost
  from drive_helper_json import save_private_json
  with tempfile.TemporaryDirectory() as folder:
   p=Path(folder);code='CF-'+'B'*32;name=code+'.mp4';sha='b'*64
   save_private_json(p/'state.json',{'id':'transcript-test','archived':True,'source':{'kind':'youtube','value':'https://youtu.be/example'},'drive_files':{name:{'id':'drive-file'}}})
   spoken=clip_transcript({'segments':[{'start':0,'end':15,'text':'Вот что говорят в видео.'}]},0,20)
   save_private_json(p/'manifest.json',{'title':'Source','clips':[{'id':code,'file':name,'sha256':sha,'duration_seconds':20,'description':'Описание поста','transcript':spoken}]})
   client=MagicMock();client.get.side_effect=[{'items':[{'id':'source'}]},{'items':[]}]
   client.post.return_value={'id':'clip','status':'ready','verified_at':'2026-11-08T00:00:00Z','passport':code,'sha256':sha,'drive_file_id':'drive-file'}
   with patch.object(autopost,'AUTOPOST_ENABLED',True),patch.object(autopost,'AUTOPOST_CATEGORY_ID','category'),patch.object(autopost,'AUTOPOST_FORMAT_ID','format'),patch.object(autopost,'Client',return_value=client):
    self.assertTrue(autopost.register_job(p)['registered'])
   payload=client.post.call_args.args[1]
   self.assertEqual(payload['description'],'Описание поста');self.assertEqual(payload['extra']['transcript'],spoken)
