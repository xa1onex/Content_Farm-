import re,time,uuid,logging
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
        self._click(Locator(by='accessibility id',value=f'Видео Reels {username} в строке 1, столбце 1'))
        if normalize_user(self.text(self._id('row_feed_photo_profile_name')))!=normalize_user(username):
            raise UIError('publication_author_unverified')

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

    def verify_account(self,assignment):
        if self.profile.instagram_reels_452:
            self.guard.check();self.driver.activate_app(self.profile.package);self._dismiss_share()
        super().verify_account(assignment)
        if not self.profile.instagram_reels_452:return
        self.before_post_count=self._post_count();self.before_post_id=None
        if self.before_post_count:
            self._open_latest_reel();self.before_post_id=self.read_open_reel()['external_id']
            self.guard.check();self.driver.back();self._profile_owner()

    def verify_publication(self):
        if self.profile.navigation_only:raise UIError('profile_navigation_only')
        if not self.profile.instagram_reels_452:return super().verify_publication()
        if not hasattr(self,'before_post_count'):raise UIError('publication_baseline_missing')
        deadline=time.monotonic()+self.profile.confirmation_timeout_seconds
        transient_ui={'ui_selector_missing','publication_content_unverified','publication_author_unverified','publication_link_not_copied'}
        while True:
            self.guard.check();profile_open=False
            try:
                # Instagram can redirect to the Reels feed after accepting the upload.
                # Reopening our profile is safe to repeat; submit() is never called here.
                self._dismiss_share()
                self._click(self._id('profile_tab'))
                self._profile_owner();count=self._post_count();profile_open=True
                if count<self.before_post_count or count>self.before_post_count+1:
                    raise UIError('publication_count_ambiguous')
                if count==self.before_post_count+1:
                    profile_open=False
                    self._open_latest_reel();proof=self.read_open_reel(self.context['caption'])
                    if proof['external_id']==self.before_post_id:raise UIError('publication_link_not_new')
                    return proof
            except UIError as exc:
                if exc.code not in transient_ui:raise
                logging.getLogger(__name__).warning('confirmation_wait code=%s',exc.code)
            except (WebDriverException,AppiumTransportError,TimeoutError,ConnectionError) as exc:
                logging.getLogger(__name__).warning('confirmation_transport_wait type=%s',type(exc).__name__)
            if time.monotonic()>=deadline:raise UIError('publication_not_confirmed')
            if profile_open:
                try:self._drag(.28,.78)
                except (WebDriverException,AppiumTransportError,TimeoutError,ConnectionError) as exc:
                    logging.getLogger(__name__).warning('confirmation_refresh_wait type=%s',type(exc).__name__)
            until=min(deadline,time.monotonic()+5)
            while time.monotonic()<until:self.guard.check();time.sleep(.25)
