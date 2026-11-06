import json
from pathlib import Path
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator

class Strict(BaseModel):model_config=ConfigDict(extra='forbid')
class Locator(Strict):
    by:Literal['id','accessibility id','xpath','-android uiautomator']
    value:str=Field(min_length=1,max_length=3000)
    attribute:str='text'
class Step(Strict):
    action:Literal['click','input','wait','back']
    locator:Locator|None=None
    source:Literal['caption','title','filename','media_album']|None=None
    timeout_seconds:int=Field(default=15,ge=1,le=30)
    optional:bool=False
    @model_validator(mode='after')
    def fields(self):
        if self.action!='back' and not self.locator:raise ValueError('Step requires a locator')
        if self.action=='input' and not self.source:raise ValueError('Input step requires a source')
        if self.optional and self.action!='click':raise ValueError('Only dialog clicks may be optional')
        return self
class Profile(Strict):
    schema_version:Literal[1]=1
    platform:Literal['tiktok','instagram','youtube']
    package:str=Field(pattern=r'^[A-Za-z][A-Za-z0-9_.]+$')
    app_version:str=Field(min_length=1)
    locale:str=Field(min_length=2)
    click_settle_seconds:float=Field(default=0,ge=0,le=2)
    navigation_verified:bool=False
    navigation_only:bool=False
    instagram_reels_452:bool=False
    account_steps:list[Step]=Field(default_factory=list)
    account:Locator
    upload_steps:list[Step]=Field(min_length=1)
    metadata_steps:list[Step]=Field(min_length=1)
    submit:Locator
    confirmation:Locator|None=None
    confirmation_timeout_seconds:int=Field(default=30,ge=1,le=1800)
    verification_steps:list[Step]=Field(default_factory=list)
    caption_proof:Locator|None=None
    author_proof:Locator|None=None
    published_url:Locator|None=None
    url_source:Literal['element','clipboard']='element'
    errors:dict[Literal['captcha','platform_block','login_required'],Locator]=Field(default_factory=dict)
    parameter_steps:dict[str,dict[str,list[Step]]]=Field(default_factory=dict)
    default_parameters:dict=Field(default_factory=dict)
    metrics_steps:list[Step]=Field(default_factory=list)
    metric_post_proof:Locator|None=None
    metrics:dict[Literal['views','likes','comments','shares','saves','follower_delta','retention'],Locator]=Field(default_factory=dict)
    max_duration_seconds:float=Field(gt=0)
    max_caption_chars:int=Field(gt=0)
    max_title_chars:int=Field(default=100,gt=0)
    @model_validator(mode='after')
    def prevent_submit_in_flow(self):
        steps=self.account_steps+self.upload_steps+self.metadata_steps+self.verification_steps+self.metrics_steps
        steps+=[s for choices in self.parameter_steps.values() for flow in choices.values() for s in flow]
        for step in steps:
            if step.action=='click' and step.locator and step.locator.by==self.submit.by and step.locator.value==self.submit.value:
                raise ValueError('Submit must not occur in a replayable flow')
        if self.instagram_reels_452:
            if (self.platform,self.package,self.locale.replace('_','-').lower())!=('instagram','com.instagram.android','ru-ru') or self.app_version not in ('451.0.0.53.80','452.0.0.0.43','454.0.0.54.77','455.0.0.0.61'):
                raise ValueError('Instagram Reels adapter requires the calibrated version and locale')
        if not self.navigation_only and not self.instagram_reels_452:
            if not all((self.confirmation,self.caption_proof,self.author_proof)):raise ValueError('Publication proof locators required')
            if self.url_source=='element' and not self.published_url:raise ValueError('URL locator required')
        if self.metrics and not self.metric_post_proof and self.url_source!='clipboard':raise ValueError('Metrics require proof of the opened post ID')
        return self
    @classmethod
    def load(cls,path):return cls.model_validate_json(Path(path).read_text(encoding='utf-8'))
