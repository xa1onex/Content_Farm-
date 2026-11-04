from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from pydantic import ValidationError
from shortpost_worker.errors import UIError,LeaseLost
from shortpost_worker.platforms.profile import Profile
from shortpost_worker.platforms.instagram import InstagramPublisher,xpath_literal

def native_profile():
    return Profile.load(Path(__file__).resolve().parents[1]/'profiles'/'instagram-452.0.0.0.43-ru-RU.reels.json')

def publisher(tmp_path):
    p=InstagramPublisher(native_profile(),Mock(),Mock(),Mock(),tmp_path)
    p.assignment=SimpleNamespace(account=SimpleNamespace(username='example'))
    p._settle=Mock()
    p.driver.find_elements.return_value=[]
    p.context={'caption':'Описание ролика'};p.before_post_count=5;p.before_post_id='OLD_123'
    return p

@pytest.mark.parametrize('field,value',[('platform','youtube'),('app_version','453'),('locale','en-US'),('package','com.other.app')])
def test_native_profile_is_pinned_to_actual_calibration(field,value):
    data=native_profile().model_dump();data[field]=value
    with pytest.raises(ValidationError):Profile.model_validate(data)

def fake_clock(monkeypatch):
    from shortpost_worker.platforms import instagram
    clock=SimpleNamespace(now=0.)
    monkeypatch.setattr(instagram.time,'monotonic',lambda:clock.now)
    monkeypatch.setattr(instagram.time,'sleep',lambda seconds:setattr(clock,'now',clock.now+seconds))
    return clock

def test_waits_for_exactly_one_new_post_then_proves_author_caption_and_link(tmp_path,monkeypatch):
    clock=fake_clock(monkeypatch);p=publisher(tmp_path)
    p._click=Mock();p._profile_owner=Mock();p._drag=Mock();p._post_count=Mock(side_effect=[5,5,6])
    p._open_latest_reel=Mock();p.read_open_reel=Mock(return_value={'external_id':'NEW_123','url':'https://www.instagram.com/reel/NEW_123/'})
    assert p.verify_publication()['external_id']=='NEW_123'
    assert clock.now==10 and p.guard.check.call_count>=40
    p.read_open_reel.assert_called_once_with('Описание ролика');p._open_latest_reel.assert_called_once()

@pytest.mark.parametrize('count',[4,7])
def test_concurrent_changes_do_not_confirm_the_wrong_post(tmp_path,count):
    p=publisher(tmp_path);p._click=Mock();p._profile_owner=Mock();p._post_count=Mock(return_value=count)
    p._open_latest_reel=Mock()
    with pytest.raises(UIError,match='publication_count_ambiguous'):p.verify_publication()
    p._open_latest_reel.assert_not_called()

def test_same_old_link_does_not_confirm_a_new_post(tmp_path):
    p=publisher(tmp_path);p._click=Mock();p._profile_owner=Mock();p._post_count=Mock(return_value=6)
    p._open_latest_reel=Mock();p.read_open_reel=Mock(return_value={'external_id':'OLD_123'})
    with pytest.raises(UIError,match='publication_link_not_new'):p.verify_publication()

def test_upload_timeout_and_lease_loss_stop_polling(tmp_path,monkeypatch):
    clock=fake_clock(monkeypatch);p=publisher(tmp_path);p.profile.confirmation_timeout_seconds=2
    p._click=Mock();p._profile_owner=Mock();p._post_count=Mock(return_value=5);p._drag=Mock()
    with pytest.raises(UIError,match='publication_not_confirmed'):p.verify_publication()
    assert clock.now==2
    p.guard.check.side_effect=LeaseLost()
    with pytest.raises(LeaseLost):p.verify_publication()

def test_wrong_author_stops_before_copying_any_link(tmp_path):
    p=publisher(tmp_path);p.text=Mock(return_value='someone_else');p._drag=Mock()
    with pytest.raises(UIError,match='publication_author_unverified'):p.read_open_reel(p.context['caption'])
    p._drag.assert_not_called();p.driver.set_clipboard_text.assert_not_called()

def test_stale_clipboard_does_not_confirm_copy_link(tmp_path):
    p=publisher(tmp_path);p.text=Mock(side_effect=['example','example Описание ролика'])
    p._drag=Mock();p._click=Mock();p.driver.find_elements.return_value=[]
    p.driver.get_clipboard_text.side_effect=lambda:p.driver.set_clipboard_text.call_args.args[0]
    with pytest.raises(UIError,match='publication_link_not_copied'):p.read_open_reel(p.context['caption'])
    p.driver.back.assert_not_called()

def test_full_caption_is_required_and_new_clipboard_link_is_canonicalized(tmp_path):
    p=publisher(tmp_path);p.text=Mock(side_effect=['example','example Описание ролика'])
    p._drag=Mock();p._click=Mock();p.driver.find_elements.return_value=[]
    p.driver.get_clipboard_text.return_value='https://www.instagram.com/reel/NEW_123/?igsh=test'
    assert p.read_open_reel(p.context['caption'])=={'external_id':'NEW_123','url':'https://www.instagram.com/reel/NEW_123/'}
    p.driver.set_clipboard_text.assert_called_once()
    assert p.driver.activate_app.call_args_list[0].args==('io.appium.settings',)
    assert p.driver.activate_app.call_args_list[-1].args==('com.instagram.android',)
    p.text=Mock(side_effect=['example','example Другая подпись','example Другая подпись'])
    with pytest.raises(UIError,match='publication_content_unverified'):p.read_open_reel(p.context['caption'])

def test_xpath_literal_handles_quotes_without_changing_expression():
    assert xpath_literal('test')=="'test'"
    assert xpath_literal("it's")=='''"it's"'''
    assert xpath_literal('a\'b"c')=='''concat('a',"'",'b"c')'''


def test_caption_can_use_accessibility_description_instead_of_text(tmp_path):
    p=publisher(tmp_path);p.text=Mock(side_effect=['example','','example Описание ролика'])
    p._drag=Mock();p._click=Mock();p.driver.find_elements.return_value=[]
    p.driver.get_clipboard_text.return_value='https://www.instagram.com/reel/NEW_123/'
    assert p.read_open_reel(p.context['caption'])['external_id']=='NEW_123'

def test_transient_read_error_after_submit_retries_confirmation_only(tmp_path,monkeypatch):
    from selenium.common.exceptions import StaleElementReferenceException
    fake_clock(monkeypatch);p=publisher(tmp_path)
    p._click=Mock(side_effect=[StaleElementReferenceException(),None]);p._profile_owner=Mock()
    p._post_count=Mock(return_value=6);p._open_latest_reel=Mock();p._drag=Mock()
    p.read_open_reel=Mock(return_value={'external_id':'NEW_123','url':'https://www.instagram.com/reel/NEW_123/'})
    p.submit=Mock()
    assert p.verify_publication()['external_id']=='NEW_123'
    p.submit.assert_not_called()


def test_share_sheet_is_closed_after_failed_clipboard_read(tmp_path):
    p=publisher(tmp_path);p.text=Mock(side_effect=['example','example Описание ролика'])
    p._drag=Mock();p._click=Mock()
    button=Mock();button.is_displayed.return_value=True
    p.driver.find_elements.side_effect=lambda by,value: [button] if 'Копировать ссылку' in value else []
    p.driver.get_clipboard_text.side_effect=lambda:p.driver.set_clipboard_text.call_args.args[0]
    with pytest.raises(UIError,match='publication_link_not_copied'):p.read_open_reel(p.context['caption'])
    p.driver.back.assert_called_once()
