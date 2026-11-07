import unittest
from unittest.mock import patch, MagicMock
from discovery import clean_candidate, queries_from, ranked_candidates, profile_prompt, search, DiscoveryError

class DiscoveryTests(unittest.TestCase):
    def test_only_real_long_videos(self):
        base={'id':'abcdefghijk','duration':200,'title':'Выпуск'}
        self.assertIsNotNone(clean_candidate(base,set()))
        for changed in [{'duration':50},{'duration':None},{'id':'bad'},{'is_live':True},{'live_status':'is_upcoming'},{'duration':99999}]:self.assertIsNone(clean_candidate({**base,**changed},set()))
        self.assertIsNone(clean_candidate(base,{'abcdefghijk'}))
    def test_ranker_cannot_invent_video_or_duplicate_it(self):
        candidates=[{'id':'abcdefghijk','title':'Выпуск'}]
        response={'items':[{'id':'invented123','score':90,'reason':'Fake'},{'id':'abcdefghijk','score':90,'reason':'Подходит'},{'id':'abcdefghijk','score':90,'reason':'Повтор'}]}
        self.assertEqual(len(ranked_candidates(response,candidates,8)),1)
    def test_queries_are_bounded_unique_and_feedback_is_context(self):
        self.assertEqual(queries_from({'queries':['тема','тема','другое','третье','четвертое']}),['тема','другое','третье'])
        prompt=profile_prompt({'source_feedback':[{'comment':'Без политики'}],'clip_feedback':[{'comment':'Обрезана развязка'}]})
        self.assertIn('Без политики',prompt);self.assertIn('Обрезана развязка',prompt)
    def test_search_uses_metadata_only_and_deduplicates(self):
        ydl=MagicMock();ydl.extract_info.return_value={'entries':[{'id':'abcdefghijk','duration':200,'title':'Выпуск'}]}
        with patch('yt_dlp.YoutubeDL') as factory:
            factory.return_value.__enter__.return_value=ydl
            result=search(['один','два'],set())
            self.assertEqual(len(result),1)
            self.assertTrue(factory.call_args.args[0]['extract_flat']);self.assertTrue(factory.call_args.args[0]['skip_download'])
            self.assertTrue(all(call.kwargs['download'] is False for call in ydl.extract_info.call_args_list))
    def test_search_failure_does_not_fake_completion(self):
        with patch('yt_dlp.YoutubeDL') as factory:
            factory.return_value.__enter__.return_value.extract_info.side_effect=RuntimeError('private provider error')
            with self.assertRaisesRegex(DiscoveryError,'search_unavailable'):search(['тема'],set())

if __name__=='__main__':unittest.main()
