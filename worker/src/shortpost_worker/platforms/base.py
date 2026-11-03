import abc,html,json,re,time
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from .profile import Profile
from ..errors import UIError,WorkerError

def normalize_user(value):return value.strip().lstrip('@').casefold()
def canonical_post(platform,url,username=None):
    value=url.strip()
    parsed=urlsplit(value)
    if parsed.scheme!='https' or parsed.username or parsed.password:raise UIError('publication_id_unavailable')
    host=(parsed.hostname or '').lower();path=parsed.path.rstrip('/')
    if platform=='tiktok' and host in ('www.tiktok.com','tiktok.com'):
        match=re.fullmatch(r'/@([^/]+)/video/([0-9]{10,30})',path)
        if match and (username is None or normalize_user(match[1])==normalize_user(username)):
            return match[2],f'https://www.tiktok.com/@{match[1]}/video/{match[2]}'
    elif platform=='instagram' and host in ('www.instagram.com','instagram.com'):
        match=re.fullmatch(r'/reel/([A-Za-z0-9_-]{3,30})',path)
        if match:return match[1],f'https://www.instagram.com/reel/{match[1]}/'
    elif platform=='youtube' and host in ('www.youtube.com','youtube.com','m.youtube.com','youtu.be'):
        identifier=path.lstrip('/') if host=='youtu.be' else (path.split('/')[-1] if path.startswith('/shorts/') else parse_qs(parsed.query).get('v',[''])[0])
        if re.fullmatch(r'[A-Za-z0-9_-]{11}',identifier):return identifier,f'https://www.youtube.com/shorts/{identifier}'
    raise UIError('publication_id_unavailable')

def exact_metric(value,metric):
    text=value.strip().replace('\u00a0',' ').replace('\u202f',' ')
    if metric=='retention':
        if re.fullmatch(r'[0-9]+([.,][0-9]+)?%',text):return float(text[:-1].replace(',','.'))/100
        return None
    if re.fullmatch(r'[0-9]+',text):return int(text)
    if re.fullmatch(r'[0-9]{1,3}( [0-9]{3})+',text):return int(text.replace(' ',''))
    if metric=='follower_delta' and re.fullmatch(r'-[0-9]+',text):return int(text)
    return None

class PlatformPublisher(abc.ABC):
    platform=''
    allowed_parameters:set[str]=set()
    def __init__(self,profile,driver,adb,guard,artifacts):
        self.profile=profile;self.driver=driver;self.adb=adb;self.guard=guard;self.artifacts=Path(artifacts)
        self.context={};self.baseline=set();self.assignment=None
    def inspect_error(self):
        for code,locator in self.profile.errors.items():
            if self.driver.find_elements(locator.by,locator.value):raise UIError(code)
    def find(self,locator,timeout=15):
        deadline=time.monotonic()+timeout
        while True:
            self.guard.check();self.inspect_error()
            value=locator.value
            for key in ('filename','media_album'):
                if '{'+key+'}' in value:value=value.replace('{'+key+'}',self.context[key])
            found=self.driver.find_elements(locator.by,value)
            if len(found)>1:raise UIError('ambiguous_ui_selector')
            if len(found)==1 and found[0].is_displayed():return found[0]
            if time.monotonic()>=deadline:raise UIError('ui_selector_missing')
            time.sleep(.25)
    def text(self,locator):
        element=self.find(locator)
        return element.text if locator.attribute=='text' else (element.get_attribute(locator.attribute) or '')
    def steps(self,steps):
        for step in steps:
            self.guard.check()
            if step.action=='back':self.driver.back();continue
            element=self.find(step.locator,step.timeout_seconds)
            if step.action=='click':self.guard.check();element.click()
            elif step.action=='input':
                self.guard.check();element.clear();self.guard.check();element.send_keys(self.context[step.source])
    def verify_account(self,assignment):
        if self.profile.platform!=self.platform:raise UIError('profile_platform_mismatch')
        if not self.profile.navigation_verified:raise UIError('profile_not_validated')
        if self.adb.version(self.profile.package)!=self.profile.app_version:raise UIError('app_version_changed')
        locale=self.adb.shell('getprop','persist.sys.locale') or self.adb.shell('getprop','ro.product.locale')
        if locale.replace('_','-').lower()!=self.profile.locale.replace('_','-').lower():raise UIError('app_locale_changed')
        if assignment.content.duration_seconds>self.profile.max_duration_seconds:raise UIError('unsupported_format')
        self.assignment=assignment
        self.parameters=dict(self.profile.default_parameters)|assignment.job.parameters
        if set(self.parameters)-self.allowed_parameters:raise UIError('unsupported_publish_parameters')
        caption=self.parameters.get('caption',assignment.content.description or assignment.content.title)
        title=self.parameters.get('title',assignment.content.title)
        if not isinstance(caption,str) or not isinstance(title,str) or not caption or len(caption)>self.profile.max_caption_chars or len(title)>self.profile.max_title_chars:
            raise UIError('invalid_publish_metadata')
        self.context={'caption':caption,'title':title,'filename':str(assignment.job.id)+'.mp4',
                      'media_album':str(assignment.job.id)}
        self.validate_parameters()
        self.guard.check();self.driver.activate_app(self.profile.package)
        self.steps(self.profile.account_steps)
        if normalize_user(self.text(self.profile.account))!=normalize_user(assignment.account.username):raise UIError('account_mismatch')
        source=self.driver.page_source
        self.baseline=set(re.findall(r'https://[^\s<>"\']+',html.unescape(source)))
        if self.profile.url_source=='clipboard':
            try:self.baseline.add(self.driver.get_clipboard_text())
            except Exception:raise UIError('clipboard_unavailable') from None
    @abc.abstractmethod
    def validate_parameters(self):...
    def prepare_media(self,local_path):
        return self.adb.stage_media(local_path,self.assignment.job.id,self.guard)
    def open_upload(self):self.steps(self.profile.upload_steps)
    def fill_metadata(self):
        self.steps(self.profile.metadata_steps)
        for name,value in self.parameters.items():
            if name in ('caption','title'):continue
            key=str(value).lower() if isinstance(value,bool) else str(value)
            choices=self.profile.parameter_steps.get(name,{})
            if key not in choices:raise UIError('unsupported_publish_parameters')
            self.steps(choices[key])
    def submit(self,before_submit):
        element=self.find(self.profile.submit)
        self.guard.check();before_submit();self.guard.check()
        element.click()  # Exactly one click. HTTP retries disabled in the Appium transport.
    def expected_publication_text(self):return self.context["caption"]
    def verify_publication(self):
        self.find(self.profile.confirmation,30)
        self.steps(self.profile.verification_steps)
        if normalize_user(self.text(self.profile.author_proof))!=normalize_user(self.assignment.account.username):raise UIError('publication_author_unverified')
        if self.text(self.profile.caption_proof).strip()!=self.expected_publication_text().strip():raise UIError('publication_content_unverified')
        raw=self.driver.get_clipboard_text() if self.profile.url_source=='clipboard' else self.text(self.profile.published_url)
        external_id,url=canonical_post(self.platform,raw,self.assignment.account.username)
        for previous in self.baseline:
            try:
                if canonical_post(self.platform,previous)[0]==external_id:raise UIError('publication_link_not_new')
            except UIError as exc:
                if exc.code=='publication_link_not_new':raise
        return {'external_id':external_id,'url':url}
    def collect_metrics(self,assignment,external_id,url):
        if not url or not self.profile.metrics:return {}
        self.verify_account(assignment)
        canonical_post(self.platform,url,assignment.account.username)
        self.guard.check();self.adb.shell('am','start','-a','android.intent.action.VIEW','-d',url,'-p',self.profile.package)
        self.steps(self.profile.metrics_steps)
        proof=self.driver.get_clipboard_text() if self.profile.url_source=='clipboard' else self.text(self.profile.metric_post_proof).strip()
        if proof!=external_id:
            try:proven_id=canonical_post(self.platform,proof,assignment.account.username)[0]
            except UIError:raise UIError('metrics_post_unverified') from None
            if proven_id!=external_id:raise UIError('metrics_post_unverified')
        result={name:exact_metric(self.text(selector),name) for name,selector in self.profile.metrics.items()}
        return {k:v for k,v in result.items() if v is not None and (k!='retention' or 0<=v<=1)}
    def evidence(self,job_id,attempt,code):
        directory=self.artifacts/(str(job_id)+'-'+str(attempt));directory.mkdir(parents=True,exist_ok=True)
        # Private local data: never uploaded automatically and excluded from Git.
        try:self.driver.save_screenshot(str(directory/'screen.png'))
        except Exception:pass
        try:(directory/'hierarchy.xml').write_text(self.driver.page_source,encoding='utf-8')
        except Exception:pass
        (directory/'error.json').write_text(json.dumps({'job_id':str(job_id),'attempt':attempt,'code':code}))
