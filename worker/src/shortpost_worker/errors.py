class WorkerError(Exception):
    def __init__(self,code,*,status=None,retry_after=None):
        self.code=code;self.status=status;self.retry_after=retry_after
        super().__init__(code)
class LeaseLost(WorkerError):
    def __init__(self):super().__init__('lease_expired')
class StopRequested(WorkerError):
    def __init__(self):super().__init__('operator_stop')
class UIError(WorkerError):
    pass
