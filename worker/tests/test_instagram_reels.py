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
    p._settle=Mock();p._drag=Mock()
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
    p._open_latest_reel=Mock(side_effect=UIError('ui_selector_missing'))
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
    p._click=Mock(side_effect=[StaleElementReferenceException(),None,None]);p._profile_owner=Mock()
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


def test_confirmation_exits_retained_nested_post_before_reading_profile(tmp_path):
    p=publisher(tmp_path);state={'screen':'post'}
    p._click=Mock()  # Instagram restores the old nested view across tab switches.
    def find(by,value):
        name=value.rsplit('/',1)[-1]
        if state['screen']=='profile' and name=='profile_header_familiar_post_count_value':return [Mock()]
        if state['screen']=='post' and name=='action_bar_button_back':return [Mock()]
        if state['screen']=='post' and name=='action_bar_title':return [SimpleNamespace(text='Публикации')]
        return []  # The author row can already be outside the scrolled viewport.
    p.driver.find_elements.side_effect=find
    p.driver.back.side_effect=lambda:state.update(screen='profile')
    def owner():
        if state['screen']!='profile':raise UIError('account_mismatch')
    p._profile_owner=owner;p._post_count=Mock(return_value=6);p._open_latest_reel=Mock()
    p.read_open_reel=Mock(return_value={'external_id':'NEW_123'});p.submit=Mock()
    assert p.verify_publication()['external_id']=='NEW_123'
    p.driver.back.assert_called_once();p.submit.assert_not_called()


def test_profile_recovery_never_backs_out_of_a_publication_composer(tmp_path):
    p=publisher(tmp_path);p._click=Mock();p._profile_owner=Mock(side_effect=UIError('account_mismatch'))
    def find(by,value):
        if value.endswith('/action_bar_button_back'):return [Mock()]
        if value.endswith('/action_bar_title'):return [SimpleNamespace(text='Новое видео Reels')]
        return []
    p.driver.find_elements.side_effect=find
    with pytest.raises(UIError,match='account_mismatch'):p._open_own_profile()
    p.driver.back.assert_not_called()


def test_first_reels_disclosure_continues_same_submission_once(tmp_path):
    p=publisher(tmp_path);share=Mock();notice=Mock();authorize=Mock()
    p.find=Mock(side_effect=[share,notice]);p.text=Mock(side_effect=['О Reels','Описание ролика'])
    p.submit(authorize)
    authorize.assert_called_once();share.click.assert_called_once();notice.click.assert_called_once()


def test_normal_reels_without_disclosure_has_exactly_one_submit(tmp_path):
    p=publisher(tmp_path);share=Mock();authorize=Mock()
    p.find=Mock(side_effect=[share,UIError('ui_selector_missing')])
    p.submit(authorize)
    authorize.assert_called_once();share.click.assert_called_once()


@pytest.mark.parametrize('title,caption',[('Другой экран','Описание ролика'),('О Reels','Другая подпись')])
def test_unknown_disclosure_or_changed_caption_never_clicks_final_share(tmp_path,title,caption):
    p=publisher(tmp_path);share=Mock();notice=Mock();authorize=Mock()
    p.find=Mock(side_effect=[share,notice]);p.text=Mock(side_effect=[title,caption])
    with pytest.raises(UIError):p.submit(authorize)
    authorize.assert_called_once();share.click.assert_called_once();notice.click.assert_not_called()


def test_lease_loss_before_disclosure_confirmation_stops_final_click(tmp_path):
    p=publisher(tmp_path);share=Mock();notice=Mock();authorize=Mock()
    p.find=Mock(side_effect=[share,notice]);p.text=Mock(side_effect=['О Reels','Описание ролика'])
    p.guard.check.side_effect=[None,None,LeaseLost()]
    with pytest.raises(LeaseLost):p.submit(authorize)
    share.click.assert_called_once();notice.click.assert_not_called()


def test_first_reel_below_recommendations_is_found_by_exact_identity(tmp_path):
    p=publisher(tmp_path);tile=Mock();p.find=Mock(side_effect=[UIError('ui_selector_missing'),tile]);p.text=Mock(return_value='example')
    p._open_latest_reel()
    p._drag.assert_called_once_with(.78,.35);tile.click.assert_called_once()
    assert all('image_button' in call.args[0].value and ' в строке 1, столбце 1' in call.args[0].value for call in p.find.call_args_list)


def test_missing_exact_first_reel_never_selects_an_unrelated_tile(tmp_path):
    p=publisher(tmp_path);p.find=Mock(side_effect=UIError('ui_selector_missing'));p._click=Mock()
    with pytest.raises(UIError,match='ui_selector_missing'):p._open_latest_reel()
    assert p.find.call_count==8 and p._drag.call_count==6


def test_return_to_scrolled_own_grid_restores_counts_before_verification(tmp_path):
    p=publisher(tmp_path);p._click=Mock();p._profile_owner=Mock();state={'top':False}
    p.driver.find_elements.side_effect=lambda by,value: [Mock()] if state['top'] and value.endswith('/profile_header_familiar_post_count_value') else []
    p._drag.side_effect=lambda *args:state.update(top=True)
    p._open_own_profile()
    p._profile_owner.assert_called_once();p._drag.assert_called_once_with(.25,.85)


def test_count_increase_with_stale_empty_grid_refreshes_and_retries_without_submit(tmp_path,monkeypatch):
    fake_clock(monkeypatch);p=publisher(tmp_path);p._open_own_profile=Mock();p._post_count=Mock(return_value=6)
    p._open_latest_reel=Mock(side_effect=[UIError('ui_selector_missing'),None]);p.read_open_reel=Mock(return_value={'external_id':'NEW_123'})
    p.submit=Mock()
    assert p.verify_publication()['external_id']=='NEW_123'
    assert p._open_own_profile.call_count==3
    p._drag.assert_called_once_with(.28,.78);p.submit.assert_not_called()


def test_reels_only_profile_uses_own_first_thumbnail_and_checks_author(tmp_path):
    p=publisher(tmp_path);tab=Mock();tab.get_attribute.return_value='true'
    p.driver.find_elements.return_value=[tab];tile=Mock();p.find=Mock(return_value=tile);p.text=Mock(return_value='example')
    p._open_latest_reel()
    assert 'preview_clip_thumbnail' in p.find.call_args.args[0].value
    assert 'clips_grid_recyclerview' in p.find.call_args.args[0].value
    tile.click.assert_called_once();p._drag.assert_not_called()


def test_reels_viewer_proves_expanded_original_caption_and_fresh_link(tmp_path):
    p=publisher(tmp_path);p._click=Mock()
    p.driver.find_elements.side_effect=lambda by,value:[Mock()] if value.endswith('/clips_author_username') else []
    expected='Пользователь example оставил комментарий: "Описание ролика"'
    p.text=Mock(side_effect=['example',expected,'example']);p.driver.get_clipboard_text.return_value='https://www.instagram.com/reel/NEW_123/?igsh=x'
    assert p.read_open_reel('Описание ролика')=={'external_id':'NEW_123','url':'https://www.instagram.com/reel/NEW_123/'}
    p.driver.back.assert_called_once();p.driver.set_clipboard_text.assert_called_once()
    assert any(call.args[0].value.endswith('/direct_share_button') for call in p._click.call_args_list)


def test_reels_viewer_wrong_full_caption_closes_sheet_without_copying_link(tmp_path):
    p=publisher(tmp_path);p._click=Mock();p._clipboard=Mock()
    p.text=Mock(side_effect=['example','wrong caption','wrong caption'])
    with pytest.raises(UIError,match='publication_content_unverified'):p._read_reels_viewer('Описание ролика')
    p.driver.back.assert_called_once();p._clipboard.assert_not_called()


def test_reels_viewer_wrong_author_stops_before_reading_caption(tmp_path):
    p=publisher(tmp_path);p._click=Mock();p.text=Mock(return_value='other')
    with pytest.raises(UIError,match='publication_author_unverified'):p._read_reels_viewer('Описание ролика')
    p._click.assert_not_called()


def test_reels_viewer_stale_clipboard_is_not_a_publication_proof(tmp_path):
    p=publisher(tmp_path);p._click=Mock();p.text=Mock(return_value='example')
    p.driver.get_clipboard_text.side_effect=lambda:p.driver.set_clipboard_text.call_args.args[0]
    with pytest.raises(UIError,match='publication_link_not_copied'):p._read_reels_viewer()


def test_account_verification_saves_real_count_and_baseline_link_before_upload(tmp_path,monkeypatch):
    import json
    from shortpost_worker.platforms.base import PlatformPublisher
    p=publisher(tmp_path);a=SimpleNamespace(account=p.assignment.account,job=SimpleNamespace(id='local-job',attempts=1))
    monkeypatch.setattr(PlatformPublisher,'verify_account',lambda self,assignment:setattr(self,'assignment',assignment))
    p._dismiss_share=Mock();p._open_own_profile=Mock();p._post_count=Mock(return_value=5)
    p._open_latest_reel=Mock();p.read_open_reel=Mock(return_value={'external_id':'OLD_123'});p._profile_owner=Mock()
    p.verify_account(a)
    saved=json.loads((tmp_path/'local-job-1/verification-baseline.json').read_text())
    assert saved['username']=='example' and saved['before_count']==5 and saved['before_post_id']=='OLD_123'


def test_identified_meta_account_switcher_is_closed_without_switching_accounts(tmp_path):
    p=publisher(tmp_path);p._click=Mock()
    p.driver.find_elements.side_effect=lambda by,value:[Mock()] if 'Открыть настройки Аккаунта Meta' in value else []
    p._dismiss_account_switcher()
    p._click.assert_not_called();p.driver.back.assert_called_once()


def test_other_close_buttons_are_not_touched_without_meta_switcher_identity(tmp_path):
    p=publisher(tmp_path);p._click=Mock();p.driver.find_elements.return_value=[]
    p._dismiss_account_switcher();p._click.assert_not_called();p.driver.back.assert_not_called()


def test_display_name_is_not_used_as_author_identity_for_profile_thumbnail(tmp_path):
    p=publisher(tmp_path);p.find=Mock(return_value=Mock());p.text=Mock(return_value='someone_else')
    with pytest.raises(UIError,match='publication_author_unverified'):p._open_latest_reel()
    locator=p.find.call_args.args[0]
    assert 'image_button' in locator.value and 'example' not in locator.value


def test_failed_content_read_returns_to_profile_and_refreshes_before_retry(tmp_path,monkeypatch):
    fake_clock(monkeypatch);p=publisher(tmp_path);p._open_own_profile=Mock();p._post_count=Mock(return_value=6)
    p._open_latest_reel=Mock();p.read_open_reel=Mock(side_effect=[UIError('publication_content_unverified'),{'external_id':'NEW_123'}])
    assert p.verify_publication()['external_id']=='NEW_123'
    assert p._open_own_profile.call_count==3;p._drag.assert_called_once_with(.28,.78)


def test_own_post_without_bottom_navigation_is_closed_before_tab_clicks(tmp_path):
    p=publisher(tmp_path);state={'screen':'post'}
    def elements(by,value):
        if state['screen']=='post' and value.endswith('/action_bar_button_back'):return [Mock()]
        if state['screen']=='post' and value.endswith('/action_bar_title'):return [SimpleNamespace(text='Публикации')]
        if state['screen']=='profile' and value.endswith('/profile_header_familiar_post_count_value'):return [Mock()]
        return []
    p.driver.find_elements.side_effect=elements;p.driver.back.side_effect=lambda:state.update(screen='profile')
    def click(locator):
        if state['screen']!='profile':raise UIError('ui_selector_missing')
    p._click=Mock(side_effect=click);p._profile_owner=Mock()
    p._open_own_profile();p.driver.back.assert_called_once();assert p._click.call_count==2


def test_profile_prefers_dedicated_reels_view_when_grid_tab_is_selected(tmp_path):
    p=publisher(tmp_path);tab=Mock();tab.get_attribute.return_value='false'
    p.driver.find_elements.return_value=[tab];p._click=Mock();p.find=Mock(return_value=Mock());p.text=Mock(return_value='example')
    p._open_latest_reel()
    assert 'preview_clip_thumbnail' in p.find.call_args.args[0].value
    p._click.assert_called_once();assert '@content-desc="Reels"' in p._click.call_args.args[0].value


def test_caption_keyboard_is_hidden_before_publication_can_be_fenced(tmp_path):
    p=publisher(tmp_path);p.steps=Mock();p.parameters={};p.driver.is_keyboard_shown.side_effect=[True,False]
    p.fill_metadata();p.driver.hide_keyboard.assert_called_once();p.driver.back.assert_not_called()


def test_caption_without_keyboard_does_not_back_out_of_composer(tmp_path):
    p=publisher(tmp_path);p.steps=Mock();p.parameters={};p.driver.is_keyboard_shown.return_value=False
    p.fill_metadata();p.driver.hide_keyboard.assert_not_called();p.driver.back.assert_not_called()


def test_visible_keyboard_after_hide_stops_before_submit(tmp_path):
    p=publisher(tmp_path);p.steps=Mock();p.parameters={};p.driver.is_keyboard_shown.return_value=True
    with pytest.raises(UIError,match='keyboard_not_dismissed'):p.fill_metadata()
    p.driver.hide_keyboard.assert_called_once()


def test_first_next_can_continue_to_disclosure_but_final_share_is_clicked_once(tmp_path):
    p=publisher(tmp_path);first=Mock();next_button=Mock();notice=Mock();before=Mock()
    first.get_attribute.return_value='Далее';next_button.get_attribute.return_value='Далее';next_button.is_enabled.return_value=True
    p.find=Mock(side_effect=[first,UIError('ui_selector_missing'),next_button,notice])
    p.text=Mock(side_effect=['Новое видео Reels','Описание ролика','О Reels','Описание ролика']);p.authorize_continue=Mock()
    p.submit(before)
    before.assert_called_once();first.click.assert_called_once();next_button.click.assert_called_once();notice.click.assert_called_once()
    assert p.authorize_continue.call_count==2


def test_final_share_is_not_repeated_when_disclosure_does_not_appear(tmp_path):
    p=publisher(tmp_path);share=Mock();share.get_attribute.return_value='Поделиться'
    p.find=Mock(side_effect=[share,UIError('ui_selector_missing')]);p.authorize_continue=Mock()
    p.submit(Mock());share.click.assert_called_once();p.authorize_continue.assert_not_called();assert p.find.call_count==2


def test_editorial_veto_before_first_reels_final_confirmation_blocks_share(tmp_path):
    from shortpost_worker.errors import WorkerError
    p=publisher(tmp_path);first=Mock();notice=Mock()
    p.find=Mock(side_effect=[first,notice]);p.text=Mock(side_effect=['О Reels','Описание ролика'])
    p.authorize_continue=Mock(side_effect=WorkerError('editorial_rejected'))
    with pytest.raises(WorkerError,match='editorial_rejected'):p.submit(Mock())
    first.click.assert_called_once();notice.click.assert_not_called()


def test_next_can_reveal_final_share_only_on_verified_composer(tmp_path):
    p=publisher(tmp_path);first=Mock();current=Mock();first.get_attribute.return_value='Далее';current.get_attribute.return_value='Поделиться'
    current.is_enabled.return_value=True;p.driver.find_elements.return_value=[]
    p.find=Mock(side_effect=[first,UIError('ui_selector_missing'),current]);p.authorize_continue=Mock()
    p.text=Mock(side_effect=['Новое видео Reels','Описание ролика'])
    p.submit(Mock());first.click.assert_called_once();current.click.assert_called_once();p.authorize_continue.assert_called_once()


def test_revealed_final_share_with_another_caption_is_never_clicked(tmp_path):
    p=publisher(tmp_path);first=Mock();current=Mock();first.get_attribute.return_value='Далее';current.get_attribute.return_value='Поделиться'
    current.is_enabled.return_value=True;p.driver.find_elements.return_value=[]
    p.find=Mock(side_effect=[first,UIError('ui_selector_missing'),current]);p.authorize_continue=Mock()
    p.text=Mock(side_effect=['Новое видео Reels','Другая подпись'])
    with pytest.raises(UIError,match='publication_content_unverified'):p.submit(Mock())
    current.click.assert_not_called();p.authorize_continue.assert_not_called()


def test_fresh_server_veto_blocks_revealed_final_share(tmp_path):
    from shortpost_worker.errors import WorkerError
    p=publisher(tmp_path);first=Mock();current=Mock();first.get_attribute.return_value='Далее';current.get_attribute.return_value='Поделиться'
    current.is_enabled.return_value=True;p.driver.find_elements.return_value=[]
    p.find=Mock(side_effect=[first,UIError('ui_selector_missing'),current]);p.authorize_continue=Mock(side_effect=WorkerError('publication_permission_denied'))
    p.text=Mock(side_effect=['Новое видео Reels','Описание ролика'])
    with pytest.raises(WorkerError,match='publication_permission_denied'):p.submit(Mock())
    current.click.assert_not_called()


def test_second_next_can_reveal_final_share_without_disclosure(tmp_path):
    p=publisher(tmp_path);first=Mock();next_button=Mock();final=Mock()
    first.get_attribute.return_value='Далее';next_button.get_attribute.return_value='Далее';final.get_attribute.return_value='Поделиться'
    next_button.is_enabled.return_value=True;final.is_enabled.return_value=True;p.driver.find_elements.return_value=[]
    p.find=Mock(side_effect=[first,UIError('ui_selector_missing'),next_button,UIError('ui_selector_missing'),final]);p.authorize_continue=Mock()
    p.text=Mock(side_effect=['Новое видео Reels','Описание ролика','Новое видео Reels','Описание ролика'])
    p.submit(Mock());first.click.assert_called_once();next_button.click.assert_called_once();final.click.assert_called_once()
    assert p.authorize_continue.call_count==2


def test_pending_processing_blocks_extra_next_navigation(tmp_path):
    p=publisher(tmp_path);first=Mock();current=Mock();first.get_attribute.return_value='Далее';current.get_attribute.return_value='Далее';current.is_enabled.return_value=True
    p.find=Mock(side_effect=[first,UIError('ui_selector_missing'),current]);p.driver.find_elements.return_value=[Mock()];p.authorize_continue=Mock()
    p.submit(Mock());first.click.assert_called_once();current.click.assert_not_called();p.authorize_continue.assert_not_called()


def test_recommended_reel_does_not_block_return_to_the_own_profile(tmp_path):
    p=publisher(tmp_path);state={'screen':'recommendation'}
    def elements(by,value):
        if value.endswith('/clips_viewer_action_bar_title') and state['screen']=='recommendation':return [Mock()]
        if value.endswith('/profile_tab'):return [Mock()]
        if value.endswith('/profile_header_familiar_post_count_value') and state['screen']=='profile':return [Mock()]
        return []
    p.driver.find_elements.side_effect=elements
    def click(locator):
        if locator.value.endswith('/feed_tab'):state['screen']='home'
        if locator.value.endswith('/profile_tab'):state['screen']='profile'
    p._click=Mock(side_effect=click);p._profile_owner=Mock();p.text=Mock(return_value='unrelated_reel_author')
    p._open_own_profile()
    assert state['screen']=='profile';p._profile_owner.assert_called_once();p.driver.back.assert_not_called();p.text.assert_not_called()


def test_reels_caption_expands_inline_without_leaving_own_viewer(tmp_path):
    p=publisher(tmp_path);expanded={'value':False};exact=Mock();exact.is_displayed.return_value=True
    def find(by,value):
        if 'clips_caption_component' in value and 'Описание ролика' in value:
            return [exact] if expanded['value'] else []
        return []
    p.driver.find_elements.side_effect=find
    p._click=Mock(side_effect=lambda locator:expanded.update(value=True) if 'clips_caption_component' in locator.value else None)
    p.text=Mock(return_value='example');p._clipboard=Mock(side_effect=[None,'https://www.instagram.com/reel/INLINE123/'])
    assert p._read_reels_viewer('Описание ролика')['external_id']=='INLINE123'
    p.driver.back.assert_not_called()
    assert '//*[@text=' in p.driver.find_elements.call_args_list[0].args[1]


def test_inline_caption_proof_rejects_ambiguous_duplicates(tmp_path):
    p=publisher(tmp_path);p.text=Mock(return_value='example')
    p.driver.find_elements.return_value=[Mock(),Mock()]
    with pytest.raises(UIError,match='ambiguous_ui_selector'):p._read_reels_viewer('Описание ролика')
    p.driver.set_clipboard_text.assert_not_called()


def test_hide_counts_checks_exact_post_before_any_setting(tmp_path):
    p=publisher(tmp_path);p.read_open_reel=Mock(return_value={'url':'https://www.instagram.com/reel/OTHER/'})
    p._visibility_controls=Mock()
    with pytest.raises(UIError,match='visibility_post_mismatch'):p.hide_public_counts({'url':'https://www.instagram.com/reel/EXPECTED/'})
    p._visibility_controls.assert_not_called()


def test_hide_counts_is_idempotent_and_never_disables_comments(tmp_path):
    p=publisher(tmp_path);url='https://www.instagram.com/reel/OWN/'
    p.read_open_reel=Mock(return_value={'url':url});p._visibility_controls=Mock();p._close_visibility_controls=Mock()
    hidden=set();clicked=[]
    def find(by,value):
        key='likes' if 'Нравится' in value else 'shares'
        if 'starts-with' in value:
            return [SimpleNamespace(is_displayed=lambda:True)] if key in hidden else []
        def click():hidden.add(key);clicked.append(value)
        return [] if key in hidden else [SimpleNamespace(click=click)]
    p.driver.find_elements.side_effect=find
    assert p.hide_public_counts({'url':url})=={'url':url,'likes':'hidden','shares':'hidden'}
    assert len(clicked)==2 and all('Выключить' not in text for text in clicked)
    assert p.hide_public_counts({'url':url})['shares']=='hidden'
    assert len(clicked)==2


def test_visibility_requires_proof_of_changed_setting(tmp_path):
    p=publisher(tmp_path);url='https://www.instagram.com/reel/OWN/'
    p.read_open_reel=Mock(return_value={'url':url});p._visibility_controls=Mock();p._close_visibility_controls=Mock()
    p.driver.find_elements.return_value=[]
    with pytest.raises(UIError,match='visibility_not_confirmed'):p.hide_public_counts({'url':url})


@pytest.mark.parametrize('initial',['own-reels','home'])
def test_own_reel_with_bottom_tabs_is_closed_even_when_profile_restores_it(tmp_path,initial):
    p=publisher(tmp_path);state={'screen':initial,'retained':True}
    def find(by,value):
        name=value.rsplit('/',1)[-1]
        if name=='clips_viewer_action_bar_title' and state['screen']=='own-reels':return [SimpleNamespace(text='Ваши видео Reels')]
        if name=='profile_tab':return [Mock()]
        if name=='profile_header_familiar_post_count_value' and state['screen']=='profile':return [Mock()]
        return []
    def click(locator):
        if locator.value.endswith('/feed_tab'):state['screen']='home'
        elif locator.value.endswith('/profile_tab'):state['screen']='own-reels' if state['retained'] else 'profile'
    def back():state.update(screen='profile',retained=False)
    p.driver.find_elements.side_effect=find;p.driver.back.side_effect=back;p._click=Mock(side_effect=click)
    p._profile_owner=Mock(side_effect=lambda:None if state['screen']=='profile' else (_ for _ in ()).throw(UIError('account_mismatch')))
    p._open_own_profile()
    assert state['screen']=='profile';p.driver.back.assert_called_once()



def test_optional_instagram_rating_prompt_is_declined_without_rating_or_submission(tmp_path):
    p=publisher(tmp_path);cancel=Mock();title=SimpleNamespace(text='Оценить Instagram')
    p.driver.find_elements.side_effect=lambda by,value: [title] if value.endswith('/appirater_title_area') else ([cancel] if value.endswith('/appirater_cancel_button') else [])
    p._dismiss_account_switcher();cancel.click.assert_called_once();p.driver.back.assert_not_called()
    title.text='Другой диалог';cancel.reset_mock();p._dismiss_account_switcher();cancel.click.assert_not_called()

def test_navigation_refinds_stale_element_before_retry(tmp_path):
    from selenium.common.exceptions import StaleElementReferenceException
    p=publisher(tmp_path);old=Mock();fresh=Mock();old.click.side_effect=StaleElementReferenceException()
    p.find=Mock(side_effect=[old,fresh]);locator=p._id('direct_share_button')
    p._click(locator)
    assert p.find.call_count==2 and p.guard.check.call_count==4
    fresh.click.assert_called_once();p._settle.assert_called_once_with(.25)

def test_navigation_stale_retry_is_bounded_and_has_clear_error(tmp_path):
    from selenium.common.exceptions import StaleElementReferenceException
    p=publisher(tmp_path);element=Mock();element.click.side_effect=StaleElementReferenceException();p.find=Mock(return_value=element)
    with pytest.raises(UIError,match='ui_element_unstable'):p._click(p._id('direct_share_button'))
    assert p.find.call_count==3

def test_navigation_does_not_retry_ambiguous_transport_failure(tmp_path):
    from selenium.common.exceptions import WebDriverException
    p=publisher(tmp_path);element=Mock();element.click.side_effect=WebDriverException('transport');p.find=Mock(return_value=element)
    with pytest.raises(WebDriverException):p._click(p._id('direct_share_button'))
    assert p.find.call_count==1

def test_navigation_retry_stops_on_lost_lease(tmp_path):
    from selenium.common.exceptions import StaleElementReferenceException
    p=publisher(tmp_path);element=Mock();element.click.side_effect=StaleElementReferenceException();p.find=Mock(return_value=element)
    p.guard.check.side_effect=[None,None,LeaseLost()]
    with pytest.raises(LeaseLost):p._click(p._id('direct_share_button'))
    assert p.find.call_count==1

def test_thumbnail_progress_does_not_skip_verified_final_share(tmp_path):
    p=publisher(tmp_path);first=Mock();share=Mock();first.get_attribute.return_value='Далее';share.get_attribute.return_value='Поделиться';share.is_enabled.return_value=True
    p.find=Mock(side_effect=[first,UIError('ui_selector_missing'),share]);p.driver.find_elements.return_value=[Mock()]
    p.text=Mock(side_effect=['Новое видео Reels','Описание ролика']);p.authorize_continue=Mock()
    p.submit(Mock());share.click.assert_called_once();p.authorize_continue.assert_called_once()


def test_thumbnail_progress_does_not_bypass_caption_verification(tmp_path):
    p=publisher(tmp_path);first=Mock();share=Mock();first.get_attribute.return_value='Далее';share.get_attribute.return_value='Поделиться';share.is_enabled.return_value=True
    p.find=Mock(side_effect=[first,UIError('ui_selector_missing'),share]);p.driver.find_elements.return_value=[Mock()]
    p.text=Mock(side_effect=['Новое видео Reels','Другая подпись']);p.authorize_continue=Mock()
    with pytest.raises(UIError,match='publication_content_unverified'):p.submit(Mock())
    share.click.assert_not_called();p.authorize_continue.assert_not_called()


def test_real_share_with_progress_is_still_never_clicked_twice(tmp_path):
    p=publisher(tmp_path);share=Mock();share.get_attribute.return_value='Поделиться';p.driver.find_elements.return_value=[Mock()]
    p.find=Mock(side_effect=[share,UIError('ui_selector_missing')]);p.authorize_continue=Mock()
    p.submit(Mock());share.click.assert_called_once();p.authorize_continue.assert_not_called()

def test_open_profile_closes_identified_comments_before_bottom_navigation(tmp_path):
    p=publisher(tmp_path);comment=Mock();comment.is_displayed.return_value=True
    p.driver.find_elements.side_effect=lambda by,value:[comment] if value.endswith('/layout_comment_thread_edittext_multiline') else []
    p._dismiss_comment_sheet();p.driver.back.assert_called_once();p.guard.check.assert_called_once()


def test_comment_dismissal_never_closes_video_publication_composer(tmp_path):
    p=publisher(tmp_path);p.driver.find_elements.side_effect=lambda by,value:[Mock()] if value.endswith('/caption_input_text_view') else []
    p._dismiss_comment_sheet();p.driver.back.assert_not_called()
