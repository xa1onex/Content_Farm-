from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from pydantic import ValidationError
from shortpost_worker.platforms.base import canonical_post,exact_metric
from shortpost_worker.platforms.profile import Profile
from shortpost_worker.platforms.tiktok import TikTokPublisher
from shortpost_worker.platforms.instagram import InstagramPublisher
from shortpost_worker.platforms.youtube import YouTubePublisher
from shortpost_worker.errors import UIError

def profile(platform='tiktok'):
    loc=lambda value:{'by':'id','value':value}
    return Profile.model_validate({'platform':platform,'package':'com.example.social','app_version':'1','locale':'en-US',
        'navigation_verified':True,'account':loc('account'),'upload_steps':[{'action':'click','locator':loc('create')}],
        'metadata_steps':[{'action':'input','locator':loc('caption'),'source':'caption'}],
        'submit':loc('publish'),'confirmation':loc('confirmed'),'caption_proof':loc('post-caption'),
        'author_proof':loc('author'),'published_url':loc('link'),'max_duration_seconds':180,'max_caption_chars':2200})

@pytest.mark.parametrize('platform,url,identifier',[
 ('tiktok','https://www.tiktok.com/@example/video/1234567890123456789','1234567890123456789'),
 ('instagram','https://www.instagram.com/reel/ABC_123x/','ABC_123x'),
 ('youtube','https://youtube.com/shorts/AbCdEfGhI_1','AbCdEfGhI_1'),
 ('youtube','https://youtu.be/AbCdEfGhI_1','AbCdEfGhI_1'),
 ('youtube','https://www.youtube.com/watch?v=AbCdEfGhI_1','AbCdEfGhI_1')])
def test_real_id_is_parsed_from_platform_url(platform,url,identifier):assert canonical_post(platform,url,'example')[0]==identifier

@pytest.mark.parametrize('platform,url',[
 ('tiktok','https://tiktok.com/@wrong/video/1234567890123456789'),
 ('tiktok','https://evil.example/@example/video/1234567890123456789'),
 ('instagram','https://instagram.com/reel/00000000-0000-4000-8000-000000000000'),
 ('youtube','https://youtube.com/shorts/local-random-uuid'),
 ('youtube','http://youtube.com/shorts/AbCdEfGhI_1')])
def test_fake_id_or_wrong_author_is_rejected(platform,url):
    with pytest.raises(UIError):canonical_post(platform,url,'example')

@pytest.mark.parametrize('value,metric,expected',[
 ('1 234','views',1234),('1234','likes',1234),('1\u00a0234','views',1234),
 ('1.2K','views',None),('1,2 тыс.','views',None),('68%','retention',.68),
 ('-2','follower_delta',-2),('Unavailable','likes',None),('1,234','views',None)])
def test_metrics_only_report_unambiguous_values(value,metric,expected):assert exact_metric(value,metric)==expected

def test_submit_must_not_appear_in_replayable_flow():
    values=profile().model_dump();values['upload_steps']=[{'action':'click','locator':values['submit']}]
    with pytest.raises(ValidationError):Profile.model_validate(values)

def test_submit_journal_is_written_before_click(tmp_path):
    order=[];element=Mock();element.click.side_effect=lambda:order.append('click')
    guard=Mock();driver=Mock();publisher=TikTokPublisher(profile(),driver,Mock(),guard,tmp_path)
    publisher.find=lambda _:element
    publisher.submit(lambda:order.append('durable'))
    assert order==['durable','click'] and element.click.call_count==1

def test_journal_failure_prevents_submit(tmp_path):
    element=Mock();publisher=TikTokPublisher(profile(),Mock(),Mock(),Mock(),tmp_path);publisher.find=lambda _:element
    def fail():raise OSError('disk full')
    with pytest.raises(OSError):publisher.submit(fail)
    element.click.assert_not_called()

def test_album_selector_resolves_assignment_and_rejects_multiple_thumbnails(tmp_path):
    from shortpost_worker.platforms.profile import Locator,Step
    driver=Mock();element=Mock();driver.find_elements.return_value=[element,element]
    publisher=InstagramPublisher(profile('instagram'),driver,Mock(),Mock(),tmp_path)
    album='00000000-0000-4000-8000-000000000001'
    publisher.context={'media_album':album}
    selector=Locator(by='xpath',value='//*[@text="{media_album}"]')
    with pytest.raises(UIError,match='ambiguous_ui_selector'):
        publisher.steps([Step(action='click',locator=selector)])
    driver.find_elements.assert_called_with('xpath','//*[@text="'+album+'"]')
    element.click.assert_not_called()

def test_youtube_requires_explicit_audience_and_visibility(tmp_path):
    p=YouTubePublisher(profile('youtube'),Mock(),Mock(),Mock(),tmp_path);p.context={'title':'Test'}
    for parameters in ({},{'visibility':'public'},{'visibility':'public','made_for_kids':'false'}):
        p.parameters=parameters
        with pytest.raises(UIError):p.validate_parameters()
    p.parameters={'visibility':'private','made_for_kids':False};p.validate_parameters()

def test_wrong_account_stops_before_upload(tmp_path,assignment):
    from shortpost_worker.models import Assignment
    adb=Mock();adb.version.return_value='1';adb.shell.return_value='en-US'
    driver=Mock();element=Mock();element.text='@wrong';element.is_displayed.return_value=True
    driver.find_elements.return_value=[element]
    publisher=TikTokPublisher(profile(),driver,adb,Mock(),tmp_path)
    with pytest.raises(UIError,match='account_mismatch'):publisher.verify_account(Assignment.model_validate(assignment))

def test_app_version_change_stops_before_navigation(tmp_path,assignment):
    from shortpost_worker.models import Assignment
    adb=Mock();adb.version.return_value='2';driver=Mock()
    publisher=TikTokPublisher(profile(),driver,adb,Mock(),tmp_path)
    with pytest.raises(UIError,match='app_version_changed'):publisher.verify_account(Assignment.model_validate(assignment))
    driver.activate_app.assert_not_called()

def test_appium_transport_disables_http_retries():
    from appium.webdriver.client_config import AppiumClientConfig
    from appium.webdriver.appium_connection import AppiumConnection
    config=AppiumClientConfig(remote_server_addr='http://127.0.0.1:4723',timeout=20,
        init_args_for_pool_manager={'init_args_for_pool_manager':{'retries':0}})
    connection=AppiumConnection(client_config=config)
    try:
        retries=connection._conn.connection_pool_kw['retries']
        assert retries==0 or retries.total==0
    finally:connection.close()

def test_youtube_proves_title_rather_than_description(tmp_path):
    publisher=YouTubePublisher(profile('youtube'),Mock(),Mock(),Mock(),tmp_path)
    publisher.context={'title':'Actual video title','caption':'Different description'}
    assert publisher.expected_publication_text()=='Actual video title'

def test_metrics_verify_exact_post_url(tmp_path,assignment):
    from shortpost_worker.models import Assignment
    values=profile().model_dump();values['metric_post_proof']={'by':'id','value':'proof'}
    values['metrics']={'views':{'by':'id','value':'views'}}
    publisher=TikTokPublisher(Profile.model_validate(values),Mock(),Mock(),Mock(),tmp_path)
    publisher.verify_account=lambda _:None;publisher.steps=lambda _:None
    url='https://www.tiktok.com/@example/video/1234567890123456789'
    publisher.text=lambda locator:url if locator.value=='proof' else '123'
    assert publisher.collect_metrics(Assignment.model_validate(assignment),'1234567890123456789',url)=={'views':123}
    publisher.text=lambda locator:'https://www.tiktok.com/@example/video/9999999999999999999'
    with pytest.raises(UIError,match='metrics_post_unverified'):
        publisher.collect_metrics(Assignment.model_validate(assignment),'1234567890123456789',url)
