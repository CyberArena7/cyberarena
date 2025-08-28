from flask import Flask, render_template, make_response
from datetime import datetime
import multiprocessing as mp

app = Flask(__name__)

manager = mp.Manager()
shared_dict = manager.dict(
    {"last_run": 0, "next_loop": datetime.now().timestamp(), "state": "running"}
)
shared_lock = manager.Lock()

warnings_lock = manager.Lock()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/status")
def status():
    with shared_lock:
        if shared_dict["state"] == "running":
            next_loop = "unknown (still running)"
        else:
            next_loop = datetime.fromtimestamp(shared_dict["next_loop"]) - datetime.now()
        return render_template(
            "status.html",
            status=shared_dict["state"],
            last_run=shared_dict["last_run"],
            next_loop=next_loop,
        )


@app.route("/logs")
def logs():
    return render_template("logs.html", logs=open("/tmp/logs.txt").read().split("\n"))


@app.route("/logs/clear")
def clear_logs():
    open("/tmp/logs.txt", "w").close()  # Clear file
    return ("", 204)
