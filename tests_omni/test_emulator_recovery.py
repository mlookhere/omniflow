"""MuMu host launch targeting and outage detection regressions."""
from unittest.mock import Mock, patch
import pytest
from omniflow.emulator_recovery import instance_index, instance_offline, launch_instance
from swordx.adb import ADBError

@pytest.mark.parametrize(('serial','expected'), [
    ('127.0.0.1:16384',0),('localhost:16385',0),
    ('127.0.0.1:16416',1),('localhost:16865',15),
    ('127.0.0.1:7555',None),('127.0.0.1:16386',None),
    ('127.0.0.1:16896',None),('emulator-5554',None),
    ('192.168.0.4:16384',None)])
def test_instance_index(serial, expected):
    assert instance_index(serial) == expected

def test_scoped_launch(tmp_path):
    cli = tmp_path / 'mumu-cli.exe'
    cli.touch()
    with patch('omniflow.emulator_recovery.subprocess.run', return_value=Mock(returncode=0)) as run:
        assert launch_instance('localhost:16417', cli=cli)
    assert run.call_args.args[0] == [str(cli), 'control', '-v', '1', 'launch']
    assert run.call_args.kwargs['timeout'] == 20

def test_legacy_manager(tmp_path):
    cli = tmp_path / 'MuMuManager.exe'
    cli.touch()
    with patch('omniflow.emulator_recovery.subprocess.run', return_value=Mock(returncode=0)) as run:
        assert launch_instance('127.0.0.1:16384', cli=cli)
    assert run.call_args.args[0] == [str(cli), 'api', '-v', '0', 'launch_player']

def test_unknown_serial_never_launches(tmp_path):
    cli = tmp_path / 'mumu-cli.exe'
    cli.touch()
    with patch('omniflow.emulator_recovery.subprocess.run') as run:
        assert not launch_instance('127.0.0.1:7555', cli=cli)
    run.assert_not_called()

def test_cli_failure(tmp_path):
    cli = tmp_path / 'mumu-cli.exe'
    cli.touch()
    with patch('omniflow.emulator_recovery.subprocess.run', return_value=Mock(returncode=1, stderr=b'failure')):
        with pytest.raises(ADBError, match='failure'):
            launch_instance('127.0.0.1:16416', cli=cli)

def test_offline_requires_missing_transport_and_listener():
    device = Mock(serial='127.0.0.1:16416')
    device.devices.return_value = [device.serial]
    assert not instance_offline(device)
    device._endpoint_open.assert_not_called()
    device.devices.return_value = []
    device._endpoint_open.return_value = True
    assert not instance_offline(device)
    device._endpoint_open.return_value = False
    assert instance_offline(device)
    device.devices.side_effect = ADBError('server unavailable')
    assert not instance_offline(device)
