import pytest

from dispatch.meshcore_adapter import RadioError, send


def test_manual_permission_blocks_radio_before_adapter(monkeypatch):
    monkeypatch.setattr("dispatch.meshcore_adapter._send_serial",lambda *args: (_ for _ in ()).throw(AssertionError("radio opened")))
    with pytest.raises(RadioError,match="disabled"):
        send({"transmit_enabled":False},{"device":"/dev/fake"},{"index":1},"test")


def test_missing_companion_blocks_radio_before_adapter(monkeypatch):
    monkeypatch.setattr("dispatch.meshcore_adapter._send_serial",lambda *args: (_ for _ in ()).throw(AssertionError("radio opened")))
    with pytest.raises(RadioError,match="no verified Companion"):
        send({"transmit_enabled":True},None,{"index":1},"test")


def test_enabled_manual_send_reaches_mocked_adapter(monkeypatch):
    called=[]
    async def accepted(path,index,message): called.append((path,index,message)); return "accepted"
    monkeypatch.setattr("dispatch.meshcore_adapter._send_serial",accepted)
    assert send({"transmit_enabled":True},{"device":"/dev/fake"},{"index":1},"test")=="accepted"
    assert called==[("/dev/fake",1,"test")]
