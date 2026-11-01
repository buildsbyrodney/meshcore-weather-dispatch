from . import create_app

app = create_app()
app.run(host=app.config.get("host", "127.0.0.1"), port=app.config.get("port", 8080))
