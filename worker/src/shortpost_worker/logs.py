import json,logging
from logging.handlers import RotatingFileHandler

class JsonFormatter(logging.Formatter):
    def format(self,record):
        # Log codes and identifiers only. Do not log HTTP bodies, URLs, repr(errors) or headers.
        return json.dumps({'time':self.formatTime(record),'level':record.levelname,
            'logger':record.name,'event':record.getMessage()},ensure_ascii=False)
def configure(directory):
    directory.mkdir(parents=True,exist_ok=True)
    handler=RotatingFileHandler(directory/'worker.log',maxBytes=5*1024**2,backupCount=5,encoding='utf-8')
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO,handlers=[handler],force=True)
    for name in ['httpx','httpcore','urllib3','selenium','appium']:logging.getLogger(name).setLevel(logging.ERROR)
