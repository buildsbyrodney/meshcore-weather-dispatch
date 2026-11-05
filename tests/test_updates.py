from dispatch import create_app
from dispatch import updates
import subprocess
def config(tmp_path, calls): return {"TESTING":True,"secret_key":"test","database":str(tmp_path/"u.sqlite"),"admin_username":"admin","admin_password_hash":"scrypt:32768:8:1$X$C8YciwLCAazhj6PeXTvoUHl0LTB7qm4JzB47lqYsOiwD9sEzKJzQx9QHdT1yTQ","nws_user_agent":"x@example.org","states":["AL"],"counties":[],"max_message_bytes":80,"poll_seconds":60,"start_poller":False,"update_helper":lambda action:calls.append(action),"update_state_dir":str(tmp_path/"updates")}
def token(client): return client.get('/settings/software-updates').data.decode().split('name="csrf_token" value="')[1].split('"')[0]
def test_update_and_reboot_require_confirmation_and_use_fixed_helper(tmp_path,monkeypatch):
 calls=[];app=create_app(config(tmp_path,calls));client=app.test_client()
 with client.session_transaction() as s:s['user_id']=app.extensions['db'].user('admin')['id']
 monkeypatch.setattr('dispatch.routes.available_update',lambda:{'available':True,'commit':'next','error':None})
 page=client.get('/settings/software-updates').data.decode()
 assert 'A reboot is sometimes necessary after an update - follow the instructions in the console.' in page
 assert page.index('Reboot Raspberry Pi') < page.index('A reboot is sometimes necessary after an update - follow the instructions in the console.')
 client.post('/settings/software-updates/install',data={'csrf_token':token(client)})
 assert not calls
 client.post('/settings/software-updates/install',data={'csrf_token':token(client),'confirm':'yes'})
 client.post('/settings/software-updates/reboot',data={'csrf_token':token(client),'confirm':'yes'})
 assert calls==['update','reboot'] and [x['action'] for x in app.extensions['db'].history()][:2]==['reboot_requested','software_update_requested']

def test_missing_managed_checkout_reports_unavailable_instead_of_crashing(monkeypatch):
 monkeypatch.setattr(updates,"APP","/path/that-does-not-exist")
 assert updates.installed()["commit"]=="unavailable"
 assert updates.available()["error"]=="Unable to check GitHub for updates."
def test_production_update_request_uses_only_the_guarded_socket(monkeypatch):
 calls=[]
 class Socket:
  def __enter__(self): return self
  def __exit__(self,*args): pass
  def settimeout(self,value): calls.append(("timeout",value))
  def connect(self,value): calls.append(("connect",value))
  def sendall(self,value): calls.append(("send",value))
 monkeypatch.setattr(updates.socket,"socket",lambda *args:Socket())
 updates.launch({});updates.launch({},reboot=True)
 assert calls==[("timeout",5),("connect",updates.UPDATE_SOCKET),("send",b"update\n"),("timeout",5),("connect",updates.UPDATE_SOCKET),("send",b"reboot\n")]
def test_update_endpoints_require_an_authenticated_csrf_session(tmp_path):
 app=create_app(config(tmp_path,[]));client=app.test_client()
 assert client.get('/settings/software-updates').status_code==302
 assert client.post('/settings/software-updates/install',data={'confirm':'yes'}).status_code==400
