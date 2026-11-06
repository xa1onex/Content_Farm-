from pathlib import Path
from unittest.mock import Mock,patch,AsyncMock
import pytest
from pydantic import ValidationError
from shortpost_worker.devices import Devices
from shortpost_worker.errors import WorkerError
from shortpost_worker.engine import Engine

def serial(config):
    config.sequential_emulators=True;config.max_parallel=1
    config.mumu_manager=Path('MuMuManager.exe');config.managed_instance_indices=[0,2,5,6,7,8,9]

def test_switch_shuts_other_managed_instances_only(config,journal):
    serial(config);manager=Mock();manager.list.return_value=[{'index':0,'is_process_started':True},{'index':9,'is_process_started':True}]
    with patch('shortpost_worker.devices.MuMu',return_value=manager):Devices(config,journal).exclusive(config.devices[0])
    manager.shutdown.assert_called_once_with(9)

def test_unknown_blocks_switch_and_shutdown(config,journal,assignment):
    serial(config);journal.save_assignment(assignment);journal.stage(assignment['job']['id'],1,'unknown')
    with patch('shortpost_worker.devices.MuMu') as factory:
        d=Devices(config,journal)
        with pytest.raises(WorkerError,match='unfinished_jobs_block_switch'):d.exclusive(config.devices[0])
        d.finish(config.devices[0]);factory.return_value.shutdown.assert_not_called()

def test_unmanaged_running_instance_is_never_stopped(config,journal):
    serial(config)
    with patch('shortpost_worker.devices.MuMu') as factory:
        factory.return_value.list.return_value=[{'index':10,'is_process_started':True}]
        with pytest.raises(WorkerError,match='unmanaged_emulator_running'):Devices(config,journal).exclusive(config.devices[0])
        factory.return_value.shutdown.assert_not_called()

def test_sequential_config_rejects_parallelism(config):
    serial(config);data=config.model_dump();data['max_parallel']=2
    with pytest.raises(ValidationError):type(config).model_validate(data)

@pytest.mark.asyncio
async def test_wake_before_claim_and_keep_claimed_device_running(config,journal,assignment):
    serial(config);api=Mock();api.get=AsyncMock(return_value={'job_id':assignment['job']['id'],'external_id':'mumu-01'})
    async def claim(job):
        journal.save_assignment(assignment);return assignment
    api.claim=AsyncMock(side_effect=claim);devices=Mock();devices.configured.return_value=config.devices[0]
    e=Engine(config,journal,api,devices=devices);e.send_heartbeat=AsyncMock()
    with patch('shortpost_worker.health.device_health',return_value={'online':True}),patch('shortpost_worker.engine.ReadGuard'):
        result=await e.claim_sequential()
    assert result==assignment;devices.exclusive.assert_called_once();devices.connect.assert_called_once();e.send_heartbeat.assert_awaited_once()
    devices.finish.assert_not_called();api.claim.assert_awaited_once_with(assignment['job']['id'])
