import os
import pytest
from dispatch.meshcore_adapter import RadioError, send

def test_dry_run_prevents_radio_open_for_every_enablement(monkeypatch):
    monkeypatch.setenv("MESHCORE_SEND_ENABLED","1")
    monkeypatch.setattr("dispatch.meshcore_adapter._send_serial",lambda *args: (_ for _ in ()).throw(AssertionError("radio opened")))
    assert send({"dry_run":True,"transmit_enabled":True},{"device":"/dev/fake"},{"index":1},"test").startswith("dry-run")

@pytest.mark.parametrize("web,environment",[(False,"0"),(False,"1"),(True,"0")])
def test_both_web_and_emergency_interlocks_are_required(monkeypatch,web,environment):
    monkeypatch.setenv("MESHCORE_SEND_ENABLED",environment)
    monkeypatch.setattr("dispatch.meshcore_adapter._send_serial",lambda *args: (_ for _ in ()).throw(AssertionError("radio opened")))
    with pytest.raises(RadioError,match="disabled"):
        send({"dry_run":False,"transmit_enabled":web},{"device":"/dev/fake"},{"index":1},"test")

def test_real_send_requires_both_interlocks_before_adapter(monkeypatch):
    monkeypatch.setenv("MESHCORE_SEND_ENABLED","1"); called=[]
    async def accepted(path,index,message): called.append((path,index,message)); return "accepted"
    monkeypatch.setattr("dispatch.meshcore_adapter._send_serial",accepted)
    assert send({"dry_run":False,"transmit_enabled":True},{"device":"/dev/fake"},{"index":1},"test")=="accepted"
    assert called==[("/dev/fake",1,"test")]
