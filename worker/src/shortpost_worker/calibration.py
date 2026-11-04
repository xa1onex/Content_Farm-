"""Owner-operated inspection and dry navigation. No method invokes submit()."""
import json,time,uuid,xml.etree.ElementTree as ET
from pathlib import Path
from .appium_session import Session
from .devices import Devices,probe_video
from .engine import ReadGuard
from .models import Assignment
from .platforms import PUBLISHERS
from .platforms.profile import Profile,Locator
from .errors import WorkerError

class LocalGuard:
    def __init__(self):self.until=time.monotonic()+1800
    def check(self):
        if time.monotonic()>self.until:raise WorkerError('diagnostic_timeout')

def select_locator(driver):
    source=driver.page_source
    if len(source)>2*1024**2:raise WorkerError('ui_hierarchy_too_large')
    tree=ET.fromstring(source);choices=[]
    for node in tree.iter():
        attrs=node.attrib
        candidate=None
        if attrs.get('resource-id'):candidate=Locator(by='id',value=attrs['resource-id'])
        elif attrs.get('content-desc'):candidate=Locator(by='accessibility id',value=attrs['content-desc'])
        if candidate and len(driver.find_elements(candidate.by,candidate.value))==1:
            if candidate not in choices:
                choices.append(candidate)
                print(len(choices),candidate.by,candidate.value,'text=',attrs.get('text','')[:80])
    if not choices:raise WorkerError('no_unique_ui_selectors')
    chosen=int(input('Select element number (nothing will be clicked): '))
    if not 1<=chosen<=len(choices):raise WorkerError('invalid_element_choice')
    return choices[chosen-1]

def inspect(config,journal,external_id,record=None):
    devices=Devices(config,journal);device=devices.configured(external_id)
    adb=devices.connect(device,LocalGuard());session=Session(device,adb.serial)
    try:
        driver=session.open()
        if record:
            selector=select_locator(driver)
            Path(record).write_text(selector.model_dump_json(indent=2),encoding='utf-8')
            print('Selector saved. No clicks performed.')
        else:
            folder=config.state_dir/'artifacts'/'inspection';folder.mkdir(parents=True,exist_ok=True)
            (folder/'hierarchy.xml').write_text(driver.page_source,encoding='utf-8')
            driver.save_screenshot(str(folder/'screen.png'))
            print('Private UI dump:',folder)
    finally:session.close()

def test_navigation(config,journal,external_id,platform,username,video,profile_path=None):
    devices=Devices(config,journal);device=devices.configured(external_id)
    profile_path=Path(profile_path).resolve() if profile_path else device.profiles.get(platform)
    if not profile_path:raise WorkerError('profile_not_configured')
    profile=Profile.load(profile_path).model_copy(update={'navigation_verified':True})
    adb=devices.connect(device,LocalGuard());session=Session(device,adb.serial)
    job_id=str(uuid.uuid4());guard=LocalGuard();remote=None
    # Diagnostic identities stay local: no claim/result/statistics API is called.
    from .devices import command
    metadata=json.loads(command([config.ffprobe_path,'-v','error','-show_entries','format=duration','-of','json',video]))
    account_id=str(uuid.uuid4());clip_id=str(uuid.uuid4())
    assignment=Assignment.model_validate({'job':{'id':job_id,'account_id':account_id,'clip_id':clip_id,'attempts':1,'status':'assigned','parameters':profile.default_parameters},
        'lease_token':'diagnostic-not-a-server-lease','lease_expires_at':'2099-01-01T00:00:00Z',
        'account':{'id':account_id,'platform':platform,'username':username,'timezone':'UTC'},
        'device':{'id':str(uuid.uuid4()),'external_id':external_id},
        'content':{'id':clip_id,'passport':'DIAGNOSTIC-LOCAL-000001','title':'ShortPost local diagnostic',
            'description':'ShortPost local diagnostic','duration_seconds':float(metadata['format']['duration'])}})
    try:
        driver=session.open();publisher=PUBLISHERS[platform](profile,driver,adb,guard,config.state_dir/'artifacts')
        publisher.verify_account(assignment);remote=publisher.prepare_media(Path(video))
        publisher.open_upload();publisher.fill_metadata();publisher.find(profile.submit)
        profile_path.write_text(profile.model_dump_json(indent=2),encoding='utf-8')
        print('Navigation verified. Stopped BEFORE Publish. Success detection and real posting are still unverified.')
    finally:
        session.close()
        if remote:
            try:adb.remove_media(remote)
            except WorkerError:pass
