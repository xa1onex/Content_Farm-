import re,time,uuid,logging,json
from selenium.common.exceptions import WebDriverException
from urllib3.exceptions import HTTPError as AppiumTransportError
from .base import PlatformPublisher,canonical_post,normalize_user
from .profile import Locator
from ..errors import UIError

def xpath_literal(value):
    if "'" not in value:return "'"+value+"'"
    if '"' not in value:return '"'+value+'"'
    return 'concat('+',"\'",'.join("'"+part+"'" for part in value.split("'"))+')'

class InstagramPublisher(PlatformPublisher):
    platform='instagram'
    allowed_parameters={'caption','allow_comments'}
    def validate_parameters(self):
        if 'allow_comments' in self.parameters and not isinstance(self.parameters['allow_comments'],bool):
            raise UIError('invalid_publish_parameters')

    def _id(self,name):return Locator(by='id',value='com.instagram.android:id/'+name)

    def _click(self,locator):
        element=self.find(locator);self.guard.check();element.click()

    def _dismiss_account_switcher(self):
        # Decline only Instagram's identified optional rating prompt.
        title=self.driver.find_elements('id',self._id('appirater_title_area').value)
        cancel=self.driver.find_elements('id',self._id('appirater_cancel_button').value)
        if len(title)==1 and title[0].text=='Оценить Instagram' and len(cancel)==1:
            self.guard.check();cancel[0].click();self._settle(.5)
        # Recent Instagram versions can overlay the profile with Meta's switcher.
        # Close only this identified sheet; never select or add another account.
        label=Locator(by='xpath',value='//*[@text="Открыть настройки Аккаунта Meta"]')
        found=self.driver.find_elements(label.by,label.value)
        if len(found)>1:raise UIError('ambiguous_ui_selector')
        if found:
            self.guard.check();self.driver.back();self._settle(.5)

    def _dismiss_own_reels(self):
        title=self.driver.find_elements('id',self._id('clips_viewer_action_bar_title').value)
        if len(title)==1 and title[0].text=='Ваши видео Reels':
            self.guard.check();self.driver.back();self._settle(.5)

    def _open_own_profile(self):
        self._dismiss_account_switcher()
        self._dismiss_own_reels()
        # The dedicated own-Reels viewer has no bottom navigation bar.
        if (self.driver.find_elements('id',self._id('clips_viewer_action_bar_title').value)
                and not self.driver.find_elements('id',self._id('profile_tab').value)):
            self.guard.check();self.driver.back();self._settle(.5)
        # Some versions hide bottom tabs in the own-post viewer. Leave that
        # identified read-only screen before trying any bottom navigation.
        for _ in range(3):
            back=self.driver.find_elements('id',self._id('action_bar_button_back').value)
            title=self.driver.find_elements('id',self._id('action_bar_title').value)
            if len(back)!=1 or len(title)!=1 or title[0].text not in ('Публикации','Видео Reels'):break
            self.guard.check();self.driver.back();self._settle(.5)
        # Return from other tabs, then close a retained nested post viewer.
        self._click(self._id('feed_tab'))
        self._settle(.5)
        self._click(self._id('profile_tab'))
        self._settle(.5);self._dismiss_account_switcher();self._dismiss_own_reels()
        # Instagram preserves each tab's navigation stack. Its own-post viewer
        # must be closed explicitly; a tab switch alone does not reset it.
        for _ in range(3):
            if self.driver.find_elements('id',self._id('profile_header_familiar_post_count_value').value):break
            back=self.driver.find_elements('id',self._id('action_bar_button_back').value)
            title=self.driver.find_elements('id',self._id('action_bar_title').value)
            if len(back)!=1 or len(title)!=1 or title[0].text not in ('Публикации','Видео Reels'):break
            self.guard.check();self.driver.back();self._settle(.5)
        self._profile_owner()
        # Returning from a grid tile retains its scroll offset. Counts must be
        # read at the top of our own profile before checking a new publication.
        for _ in range(3):
            if self.driver.find_elements('id',self._id('profile_header_familiar_post_count_value').value):break
            self._drag(.25,.85);self._settle(.5)

    def _profile_owner(self):
        if normalize_user(self.text(self.profile.account))!=normalize_user(self.assignment.account.username):
            raise UIError('account_mismatch')

    def _post_count(self):
        value=self.text(self._id('profile_header_familiar_post_count_value')).strip()
        if not re.fullmatch(r'[0-9]+',value):raise UIError('publication_count_unavailable')
        return int(value)

    def _drag(self,start_y,end_y):
        self.guard.check();size=self.driver.get_window_size();x=int(size['width']*.5)
        self.driver.execute('actions',{'actions':[{'type':'pointer','id':'shortpost-finger',
            'parameters':{'pointerType':'touch'},'actions':[
                {'type':'pointerMove','duration':0,'x':x,'y':int(size['height']*start_y)},
                {'type':'pointerDown','button':0},{'type':'pause','duration':200},
                {'type':'pointerMove','duration':1600,'x':x,'y':int(size['height']*end_y)},
                {'type':'pointerUp','button':0}]}]})
        self.guard.check()

    def _open_latest_reel(self):
        username=self.assignment.account.username
        reels_tab=Locator(by='xpath',value='//*[@resource-id="com.instagram.android:id/profile_tab_icon_view" and @content-desc="Reels"]')
        tabs=self.driver.find_elements(reels_tab.by,reels_tab.value)
        in_reels=len(tabs)==1 and tabs[0].get_attribute('selected')=='true'
        suffix=' в строке 1, столбце 1'
        # Instagram labels thumbnails with the display name, not the unique username.
        # Select the first own-profile cell by role/position, then prove its author.
        grid=Locator(by='xpath',value='//*[@resource-id="com.instagram.android:id/image_button" and starts-with(@content-desc,"Видео Reels ") and substring(@content-desc,string-length(@content-desc)-'+str(len(suffix)-1)+')='+xpath_literal(suffix)+']')
        thumb=Locator(by='xpath',value='(//*[@resource-id="com.instagram.android:id/clips_grid_recyclerview"]//*[@resource-id="com.instagram.android:id/preview_clip_thumbnail" and (starts-with(@content-desc,'+
            xpath_literal('Видео\u00a0Reels от ')+') or starts-with(@content-desc,'+xpath_literal('Видео Reels от ')+'))])[1]')
        for use_reels in ([True] if len(tabs)==1 else [False,True]):
            if use_reels and not in_reels:
                self._click(reels_tab);self._settle(.5)
            locator=thumb if use_reels else grid
            for attempt in range(4):
                try:element=self.find(locator,timeout=2);break
                except UIError as exc:
                    if exc.code!='ui_selector_missing':raise
                    if attempt==3:
                        if use_reels:raise
                        element=None;break
                    # New profiles put recommendations above their first row.
                    self._drag(.78,.35);self._settle(.5)
            if element is not None:
                self.guard.check();element.click()
                author='clips_author_username' if use_reels else 'row_feed_photo_profile_name'
                if normalize_user(self.text(self._id(author)))!=normalize_user(username):
                    raise UIError('publication_author_unverified')
                return

    def _settle(self,seconds=1.5):
        until=time.monotonic()+seconds
        while time.monotonic()<until:self.guard.check();time.sleep(.1)

    def _clipboard(self,value=None):
        # Android restricts clipboard access to foreground applications.
        self.guard.check();self.driver.activate_app('io.appium.settings')
        try:
            self.guard.check()
            if value is None:return self.driver.get_clipboard_text()
            self.driver.set_clipboard_text(value)
        finally:self.driver.activate_app(self.profile.package)

    def _dismiss_share(self):
        locator=Locator(by='xpath',value='//*[@resource-id="com.instagram.android:id/button" and @content-desc="Копировать ссылку"]')
        elements=self.driver.find_elements(locator.by,locator.value)
        if len(elements)==1 and elements[0].is_displayed():self.guard.check();self.driver.back()

    def read_open_reel(self,caption=None):
        """Read the opened own Reel. Also used to calibrate against an existing post."""
        username=self.assignment.account.username
        if self.driver.find_elements('id',self._id('clips_author_username').value):return self._read_reels_viewer(caption)
        if normalize_user(self.text(self._id('row_feed_photo_profile_name')))!=normalize_user(username):
            raise UIError('publication_author_unverified')
        self._drag(.77,.42)
        if caption is not None:
            more=self.driver.find_elements('accessibility id','ещё')
            if len(more)>1:raise UIError('ambiguous_ui_selector')
            if more and more[0].is_displayed():self.guard.check();more[0].click()
            expected=username+' '+caption
            proof=Locator(by='xpath',value='//*[@content-desc='+xpath_literal(expected)+' or @text='+xpath_literal(expected)+']')
            actual=self.text(proof)
            if actual!=expected:
                proof=proof.model_copy(update={'attribute':'content-desc'})
                if self.text(proof)!=expected:raise UIError('publication_content_unverified')
        # The clipboard must change because Copy link succeeded in this very operation.
        nonce='shortpost-link-check-'+uuid.uuid4().hex
        self._clipboard(nonce)
        self._click(self._id('row_feed_button_share'))
        self._settle()  # Share targets move while the sheet animates into position.
        try:
            self._click(Locator(by='xpath',value='//*[@resource-id="com.instagram.android:id/button" and @content-desc="Копировать ссылку"]'))
            self._settle(.5)
            raw=self._clipboard()
            if raw==nonce:raise UIError('publication_link_not_copied')
            external_id,url=canonical_post('instagram',raw,username)
            return {'external_id':external_id,'url':url}
        finally:
            # A failed read must not leave a sheet blocking the next profile check.
            try:self._dismiss_share()
            except (WebDriverException,AppiumTransportError,TimeoutError,ConnectionError):pass

    def _read_reels_viewer(self,caption=None):
        username=self.assignment.account.username
        if normalize_user(self.text(self._id('clips_author_username')))!=normalize_user(username):
            raise UIError('publication_author_unverified')
        if caption is not None:
            # 452 expands the caption inline; later versions open its original
            # comment. Accept only the complete exact caption in either location.
            inline=Locator(by='xpath',value='//*[@resource-id="com.instagram.android:id/clips_caption_component"]//*[@text='+xpath_literal(caption)+' or @content-desc='+xpath_literal(caption)+']')
            matches=self.driver.find_elements(inline.by,inline.value)
            if len(matches)>1:raise UIError('ambiguous_ui_selector')
            if not matches:
                self._click(Locator(by='xpath',value='//*[@resource-id="com.instagram.android:id/clips_caption_component"]//*[@clickable="true"]'))
                self._settle(.5)
                matches=self.driver.find_elements(inline.by,inline.value)
                if len(matches)>1:raise UIError('ambiguous_ui_selector')
                if not matches:
                    try:
                        expected='Пользователь '+username+' оставил комментарий: "'+caption+'"'
                        locator=Locator(by='xpath',value='//*[@text='+xpath_literal(expected)+' or @content-desc='+xpath_literal(expected)+']')
                        if self.text(locator)!=expected:
                            if self.text(locator.model_copy(update={'attribute':'content-desc'}))!=expected:raise UIError('publication_content_unverified')
                    finally:self.guard.check();self.driver.back();self._settle(.5)
            if matches and not matches[0].is_displayed():raise UIError('publication_content_unverified')
        nonce='shortpost-link-check-'+uuid.uuid4().hex
        self._clipboard(nonce)
        if normalize_user(self.text(self._id('clips_author_username')))!=normalize_user(username):raise UIError('publication_author_unverified')
        self._click(self._id('direct_share_button'));self._settle()
        try:
            self._click(Locator(by='xpath',value='//*[@resource-id="com.instagram.android:id/button" and @content-desc="Копировать ссылку"]'))
            self._settle(.5);raw=self._clipboard()
            if raw==nonce:raise UIError('publication_link_not_copied')
            external_id,url=canonical_post('instagram',raw,username)
            return {'external_id':external_id,'url':url}
        finally:self._dismiss_share()

    def _visibility_controls(self):
        title=Locator(by='xpath',value='//*[@resource-id="com.instagram.android:id/title_text_view" and @text="Управлять видео Reels"]')
        if self.driver.find_elements(title.by,title.value):return
        manage=Locator(by='xpath',value='//*[@resource-id="com.instagram.android:id/row_simple_text_title" and @text="Управлять"]')
        if not self.driver.find_elements(manage.by,manage.value):
            self._click(self._id('clips_ufi_more_button_component'));self._settle(.5)
        self._click(manage);self._settle(.5)
        self.find(title)

    def _close_visibility_controls(self):
        for _ in range(2):
            known=Locator(by='xpath',value='//*[@resource-id="com.instagram.android:id/title_text_view" and @text="Управлять видео Reels"] | //*[@resource-id="com.instagram.android:id/row_simple_text_title" and @text="Управлять"]')
            if not self.driver.find_elements(known.by,known.value):break
            self.guard.check();self.driver.back();self._settle(.5)

    def hide_public_counts(self,proof):
        """Hide only public counts on a freshly proven own Reel, idempotently."""
        if not self.profile.instagram_reels_452:raise UIError('visibility_profile_unsupported')
        actual=self.read_open_reel()
        if actual['url']!=proof['url']:raise UIError('visibility_post_mismatch')
        result={}
        for key,label,token in [('likes','Скрыть число отметок "Нравится" от других','Нравится'),
                                ('shares','Скрыть число поделившихся','поделившихся')]:
            try:
                self._visibility_controls()
                hide=Locator(by='xpath',value='//*[@resource-id="com.instagram.android:id/control_option_text" and @text='+xpath_literal(label)+']')
                show=Locator(by='xpath',value='//*[@resource-id="com.instagram.android:id/control_option_text" and starts-with(@text,"Показ") and contains(@text,'+xpath_literal(token)+')]')
                actions=self.driver.find_elements(hide.by,hide.value)
                inverse=self.driver.find_elements(show.by,show.value)
                if len(actions)>1 or len(inverse)>1 or (actions and inverse):raise UIError('ambiguous_ui_selector')
                if actions:
                    self.guard.check();actions[0].click();self._settle(.5)
                    self._visibility_controls()
                    inverse=self.driver.find_elements(show.by,show.value)
                if len(inverse)!=1 or not inverse[0].is_displayed():raise UIError('visibility_not_confirmed')
                result[key]='hidden'
            finally:self._close_visibility_controls()
        return {'url':proof['url'],**result}

    def verify_account(self,assignment):
        if self.profile.instagram_reels_452:
            self.guard.check();self.driver.activate_app(self.profile.package);self._dismiss_share()
            self.assignment=assignment;self._open_own_profile()
        super().verify_account(assignment)
        if not self.profile.instagram_reels_452:return
        self.before_post_count=self._post_count();self.before_post_id=None
        if self.before_post_count:
            self._open_latest_reel();self.before_post_id=self.read_open_reel()['external_id']
            self.guard.check();self.driver.back();self._profile_owner()

        directory=self.artifacts/(str(assignment.job.id)+'-'+str(assignment.job.attempts))
        directory.mkdir(parents=True,exist_ok=True)
        (directory/'verification-baseline.json').write_text(json.dumps({
            'username':assignment.account.username,'before_count':self.before_post_count,
            'before_post_id':self.before_post_id,'checked_at':time.time()}),encoding='utf-8')

    def fill_metadata(self):
        super().fill_metadata()
        if not self.profile.instagram_reels_452:return
        # Caption input can move the final button and leave the IME over it.
        # Finish that layout change before fencing the only submission click.
        self._settle(1)
        if self.driver.is_keyboard_shown():
            self.guard.check();self.driver.hide_keyboard();self._settle(.5)
        if self.driver.is_keyboard_shown():raise UIError('keyboard_not_dismissed')

    def _authorize_continuation(self):
        self.guard.check()
        if self.authorize_continue:self.authorize_continue()
        self.guard.check()

    def submit(self,before_submit):
        if not self.profile.instagram_reels_452:return super().submit(before_submit)
        if self.profile.navigation_only:raise UIError('profile_navigation_only')
        button=self.find(self.profile.submit)
        label=button.get_attribute('content-desc') or button.text
        self.guard.check();before_submit();self.guard.check();button.click()
        notice=self._id('clips_nux_sheet_share_button')
        try:confirmation=self.find(notice,timeout=5)
        except UIError as exc:
            if exc.code!='ui_selector_missing':raise
            if label!='Далее':return  # A real Share action is never repeated.
            try:next_button=self.find(self.profile.submit,timeout=2)
            except UIError as missing:
                if missing.code=='ui_selector_missing':return
                raise
            if next_button.get_attribute('content-desc')!='Далее' or not next_button.is_enabled():return
            if self.driver.find_elements('class name','android.widget.ProgressBar'):return
            if self.text(self._id('action_bar_title'))!='Новое видео Reels':raise UIError('publication_notice_unverified')
            if self.text(self._id('caption_input_text_view'))!=self.context['caption']:raise UIError('publication_content_unverified')
            # A first tap can only blur/reflow the caption field. Continue the
            # still-visible Next navigation once, with a fresh server veto check.
            self._authorize_continuation();next_button.click()
            confirmation=self.find(notice,timeout=15)
        if self.text(self._id('clips_nux_sheet_title'))!='О Reels':raise UIError('publication_notice_unverified')
        if self.text(self._id('caption_input_text_view'))!=self.context['caption']:raise UIError('publication_content_unverified')
        self._authorize_continuation();confirmation.click()

    def verify_publication(self):
        if self.profile.navigation_only:raise UIError('profile_navigation_only')
        if not self.profile.instagram_reels_452:return super().verify_publication()
        if not hasattr(self,'before_post_count'):raise UIError('publication_baseline_missing')
        deadline=time.monotonic()+self.profile.confirmation_timeout_seconds
        transient_ui={'ui_selector_missing','publication_content_unverified','publication_author_unverified','publication_link_not_copied'}
        while True:
            self.guard.check();profile_open=False;retry_profile=False
            try:
                # Instagram can redirect to the Reels feed after accepting the upload.
                # Reopening our profile is safe to repeat; submit() is never called here.
                self._dismiss_share()
                self._open_own_profile()
                count=self._post_count();profile_open=True
                if count<self.before_post_count or count>self.before_post_count+1:
                    raise UIError('publication_count_ambiguous')
                if count==self.before_post_count+1:
                    retry_profile=True
                    self._open_latest_reel();profile_open=False
                    proof=self.read_open_reel(self.context['caption'])
                    if proof['external_id']==self.before_post_id:raise UIError('publication_link_not_new')
                    return proof
            except UIError as exc:
                if exc.code not in transient_ui:raise
                logging.getLogger(__name__).warning('confirmation_wait code=%s',exc.code)
            except (WebDriverException,AppiumTransportError,TimeoutError,ConnectionError) as exc:
                logging.getLogger(__name__).warning('confirmation_transport_wait type=%s',type(exc).__name__)
            if time.monotonic()>=deadline:raise UIError('publication_not_confirmed')
            if profile_open or retry_profile:
                try:self._open_own_profile();self._drag(.28,.78)
                except UIError as exc:
                    if exc.code not in transient_ui:raise
                    logging.getLogger(__name__).warning('confirmation_refresh_wait code=%s',exc.code)
                except (WebDriverException,AppiumTransportError,TimeoutError,ConnectionError) as exc:
                    logging.getLogger(__name__).warning('confirmation_refresh_wait type=%s',type(exc).__name__)
            until=min(deadline,time.monotonic()+5)
            while time.monotonic()<until:self.guard.check();time.sleep(.25)
