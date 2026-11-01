import json,sqlite3,threading,time,uuid
from contextlib import contextmanager
from pathlib import Path

SCHEMA="""
CREATE TABLE IF NOT EXISTS operations (
 id TEXT PRIMARY KEY, slot TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, path TEXT NOT NULL,
 body TEXT NOT NULL, response TEXT, state TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
 job_id TEXT NOT NULL, attempt INTEGER NOT NULL, device TEXT NOT NULL, assignment TEXT NOT NULL,
 stage TEXT NOT NULL DEFAULT 'assigned', irreversible_at REAL, result TEXT, publication_id TEXT,
 updated REAL NOT NULL, PRIMARY KEY(job_id,attempt));
CREATE TABLE IF NOT EXISTS history (
 id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, attempt INTEGER, event TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS mappings (
 external_id TEXT PRIMARY KEY, instance_index INTEGER NOT NULL UNIQUE, android_id TEXT NOT NULL UNIQUE,
 serial TEXT, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS controls (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS metrics (
 publication_id TEXT NOT NULL, hours INTEGER NOT NULL, due REAL NOT NULL, assignment TEXT NOT NULL,
 external_id TEXT NOT NULL, url TEXT, state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(publication_id,hours));
"""
class Journal:
    def __init__(self,path,cipher):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.cipher=cipher;self.lock=threading.RLock()
        self.db=sqlite3.connect(path,timeout=10,check_same_thread=False,isolation_level=None)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA foreign_keys=ON');self.db.execute('PRAGMA busy_timeout=10000')
        version=self.db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0,1):raise RuntimeError('Unsupported journal schema; do not overwrite local state')
        self.db.executescript(SCHEMA);self.db.execute('PRAGMA user_version=1')
    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:yield self.db;self.db.execute('COMMIT')
            except BaseException:self.db.execute('ROLLBACK');raise
    def seal(self,value):return self.cipher.encrypt(json.dumps(value,separators=(',',':'),sort_keys=True))
    def open(self,value):return json.loads(self.cipher.decrypt(value)) if value else None
    def operation(self,kind,path,body,slot=None):
        slot=slot or str(uuid.uuid4())
        with self.transaction() as db:
            row=db.execute('SELECT * FROM operations WHERE slot=?',(slot,)).fetchone()
            if row:
                if row['path']!=path or self.open(row['body'])!=body:raise ValueError('Operation slot reused with different payload')
                return dict(row)
            identifier=uuid.uuid4().hex
            db.execute('INSERT INTO operations(id,slot,kind,path,body,created) VALUES(?,?,?,?,?,?)',
                (identifier,slot,kind,path,self.seal(body),time.time()))
            return dict(db.execute('SELECT * FROM operations WHERE id=?',(identifier,)).fetchone())
    def find_operation(self,slot):
        with self.lock:
            row=self.db.execute('SELECT * FROM operations WHERE slot=?',(slot,)).fetchone()
            return dict(row) if row else None
    def pending(self,kind=None):
        with self.lock:
            query='SELECT * FROM operations WHERE state="pending"';args=()
            if kind:query+=' AND kind=?';args=(kind,)
            return [dict(r) for r in self.db.execute(query+' ORDER BY created',args)]
    def finish(self,operation,response):
        with self.transaction() as db:
            if operation['kind']=='claim' and response.get('job'):
                from .models import Assignment
                assignment=Assignment.model_validate(response).model_dump(mode='json')
                self._save_assignment(db,assignment)
            db.execute('UPDATE operations SET state="done",response=? WHERE id=?',(self.seal(response),operation['id']))
    def reject(self,operation):
        with self.transaction() as db:db.execute('UPDATE operations SET state="rejected" WHERE id=?',(operation['id'],))
    def _save_assignment(self,db,data):
        db.execute('INSERT OR IGNORE INTO jobs(job_id,attempt,device,assignment,updated) VALUES(?,?,?,?,?)',
            (data['job']['id'],data['job']['attempts'],data['device']['external_id'],self.seal(data),time.time()))
    def save_assignment(self,assignment):
        with self.transaction() as db:self._save_assignment(db,assignment)
    def jobs(self,unfinished=False):
        with self.lock:
            query='SELECT * FROM jobs'
            if unfinished:query+=' WHERE stage NOT IN ("published","failed","reconciled","abandoned")'
            return [dict(r) for r in self.db.execute(query+' ORDER BY updated')]
    def job(self,id,attempt):
        with self.lock:
            row=self.db.execute('SELECT * FROM jobs WHERE job_id=? AND attempt=?',(id,attempt)).fetchone()
            return dict(row) if row else None
    def stage(self,id,attempt,stage):
        with self.transaction() as db:
            db.execute('UPDATE jobs SET stage=?,updated=? WHERE job_id=? AND attempt=?',(stage,time.time(),id,attempt))
            db.execute('INSERT INTO history(job_id,attempt,event,created) VALUES(?,?,?,?)',(id,attempt,stage,time.time()))
    def mark_irreversible(self,id,attempt):
        with self.transaction() as db:
            row=db.execute('SELECT irreversible_at,result FROM jobs WHERE job_id=? AND attempt=?',(id,attempt)).fetchone()
            if not row or row['irreversible_at'] is not None or row['result'] is not None:
                raise RuntimeError('Submit already attempted or result recorded; never repeat')
            db.execute('UPDATE jobs SET irreversible_at=?,stage="submitting",updated=? WHERE job_id=? AND attempt=?',
                (time.time(),time.time(),id,attempt))
            db.execute('INSERT INTO history(job_id,attempt,event,created) VALUES(?,?,?,?)',(id,attempt,'irreversible_started',time.time()))
    def record_result(self,id,attempt,payload):
        with self.transaction() as db:
            row=db.execute('SELECT result FROM jobs WHERE job_id=? AND attempt=?',(id,attempt)).fetchone()
            if not row:raise RuntimeError('Unknown local job')
            if row['result'] and self.open(row['result'])!=payload:raise RuntimeError('Durable result cannot be replaced')
            db.execute('UPDATE jobs SET result=?,stage="result_pending",updated=? WHERE job_id=? AND attempt=?',
                (self.seal(payload),time.time(),id,attempt))
    def result_acknowledged(self,id,attempt,response):
        row=self.job(id,attempt)
        assignment=self.open(row['assignment']);payload=self.open(row['result'])
        with self.transaction() as db:
            stage=response['status']
            db.execute('UPDATE jobs SET stage=?,publication_id=?,updated=? WHERE job_id=? AND attempt=?',
                (stage,response.get('publication_id'),time.time(),id,attempt))
            if stage=='published' and response.get('publication_id'):
                from datetime import datetime
                published=datetime.fromisoformat(payload['published_at'].replace('Z','+00:00')).timestamp()
                for hours in (24,48):
                    db.execute('INSERT OR IGNORE INTO metrics(publication_id,hours,due,assignment,external_id,url) VALUES(?,?,?,?,?,?)',
                        (response['publication_id'],hours,published+hours*3600,self.seal(assignment),payload['external_id'],payload.get('url')))
    def blocked_devices(self):return {row['device'] for row in self.jobs(unfinished=True)}
    def mapping(self,external_id,index,android_id,serial):
        with self.transaction() as db:
            row=db.execute('SELECT * FROM mappings WHERE external_id=?',(external_id,)).fetchone()
            if row and (row['android_id']!=android_id or row['instance_index']!=index):
                raise RuntimeError('Device identity changed. Rebind explicitly after checking the emulator.')
            db.execute('INSERT INTO mappings VALUES(?,?,?,?,?) ON CONFLICT(external_id) DO UPDATE SET serial=excluded.serial,updated=excluded.updated',
                (external_id,index,android_id,serial,time.time()))
    def control(self,key,value=None):
        with self.transaction() as db:
            if value is not None:db.execute('INSERT INTO controls VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,value))
            row=db.execute('SELECT value FROM controls WHERE key=?',(key,)).fetchone()
            return row['value'] if row else None
    def due_metrics(self,at):
        with self.lock:return [dict(r) for r in self.db.execute('SELECT * FROM metrics WHERE state="pending" AND attempts<10 AND due<=? ORDER BY due',(at,))]
    def metric_state(self,pub,hours,done):
        with self.transaction() as db:
            db.execute('UPDATE metrics SET state=?,attempts=attempts+1,due=? WHERE publication_id=? AND hours=?',
                ('done' if done else 'pending',time.time()+3600,pub,hours))
    def close(self):self.db.close()
