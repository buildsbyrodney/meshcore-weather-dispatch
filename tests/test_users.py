from werkzeug.security import generate_password_hash
from dispatch import create_app

def config(tmp_path):
 return {"TESTING":True,"secret_key":"test","database":str(tmp_path/"users.sqlite"),"admin_username":"admin","admin_password_hash":generate_password_hash("simple"),"nws_user_agent":"test@example.invalid","states":["AL"],"counties":[],"max_message_bytes":80,"transmit_enabled":False,"poll_seconds":60,"start_poller":False}
def login(client,user="admin",password="simple"):
 page=client.get("/login").data.decode();csrf=page.split('name="csrf_token" value="')[1].split('"')[0]
 return client.post("/login",data={"csrf_token":csrf,"username":user,"password":password})
def token(client): return client.get("/settings/users").data.decode().split('name="csrf_token" value="')[1].split('"')[0]
def test_legacy_singleton_migrates_and_login_uses_users_table(tmp_path):
 app=create_app(config(tmp_path));db=app.extensions["db"];assert [u["username"] for u in db.users()]==["admin"]
 client=app.test_client();assert login(client).status_code==302
def test_add_edit_disable_and_remove_users_with_audit_and_last_active_guard(tmp_path):
 app=create_app(config(tmp_path));db=app.extensions["db"];client=app.test_client();login(client)
 client.post("/settings/users/add",data={"csrf_token":token(client),"username":"operator","password":"simple2","confirm_password":"simple2"})
 assert db.user("operator",active_only=True) and db.history()[0]["action"]=="user_added"
 client.post("/settings/users/edit",data={"csrf_token":token(client),"target":"operator","username":"operator2","password":"changed","confirm_password":"changed"})
 other=app.test_client();assert login(other,"operator2","changed").status_code==302
 client.post("/settings/users/status",data={"csrf_token":token(client),"target":"operator2","disable":"yes"})
 blocked=app.test_client();assert b"Invalid credentials" in login(blocked,"operator2","changed").data
 client.post("/settings/users/remove",data={"csrf_token":token(client),"target":"operator2","confirm":"yes"})
 assert db.user("operator2") is None
 response=client.post("/settings/users/status",data={"csrf_token":token(client),"target":"admin","disable":"yes"},follow_redirects=True)
 assert db.user("admin",active_only=True) and b"last active account" in response.data
def test_disabled_session_cannot_keep_admin_access(tmp_path):
 app=create_app(config(tmp_path));db=app.extensions["db"];client=app.test_client();login(client)
 db.add_user("other",generate_password_hash("simple2"));db.set_user_disabled("admin",True)
 response=client.get("/settings",follow_redirects=True)
 assert b"Sign in with an active account" in response.data and b"Settings" not in response.data
