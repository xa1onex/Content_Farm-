from datetime import datetime
from uuid import UUID
from pydantic import BaseModel,Field,field_validator,model_validator
from typing import Literal

class Job(BaseModel):
    id:UUID
    account_id:UUID
    clip_id:UUID
    attempts:int=Field(ge=1)
    status:str
    parameters:dict=Field(default_factory=dict)
class Account(BaseModel):
    id:UUID
    platform:Literal['tiktok','instagram','youtube']
    username:str
    timezone:str
class Device(BaseModel):
    id:UUID
    external_id:str
class Content(BaseModel):
    id:UUID
    passport:str
    title:str
    description:str=''
    sha256:str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    duration_seconds:float=Field(gt=0)
class Assignment(BaseModel):
    job:Job
    lease_token:str=Field(min_length=20)
    lease_expires_at:datetime
    account:Account
    device:Device
    content:Content
    @field_validator('lease_expires_at')
    @classmethod
    def aware(cls,value):
        if not value.tzinfo:raise ValueError('lease deadline requires timezone')
        return value
    @model_validator(mode='after')
    def bindings(self):
        if self.job.account_id!=self.account.id or self.job.clip_id!=self.content.id:
            raise ValueError('Server assignment contains inconsistent identities')
        if self.job.status!='assigned':raise ValueError('Claim must return assigned')
        return self
