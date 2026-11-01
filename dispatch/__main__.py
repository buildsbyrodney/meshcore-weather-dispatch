from . import create_app
from .poller import run_poller
import threading

app = create_app()
threading.Thread(target=run_poller, args=(app,), daemon=True).start()
app.run(host=app.config.get("host", "127.0.0.1"), port=app.config.get("port", 8080))
