from flask import Flask, render_template, make_response, request, redirect
from datetime import datetime
import multiprocessing as mp
from dataclasses import dataclass
import json


@dataclass
class Warning:
    messages: list[str]
    hd_invoice_id: str | None
    rd_invoice_id: str | None
    order_id: str
    id: str | None = None


app = Flask(__name__)
CONFIG = json.load(open("/etc/repairdesk-to-holded.conf.json"))

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


@app.route("/warnings")
def warnings():
    with warnings_lock:
        try:
            return render_template(
                "warnings.html",
                warnings=map(
                    lambda w: Warning(
                        id=w["id"],
                        messages=w["messages"],
                        rd_invoice_id=w["rd_invoice_id"],
                        hd_invoice_id=w["hd_invoice_id"],
                        order_id=w["order_id"],
                    ),
                    json.load(open(CONFIG["data_dir"].rstrip("/") + "/warnings.json")),
                ),
                business_name=CONFIG["business_name"],
            )
        except FileNotFoundError:
            return ""


@app.route("/warnings/discard")
def discard_warning():
    with warnings_lock:
        # Remove the warning with given id
        warns_removed = filter(
            lambda w: w["id"] != request.args.get("id", ""),
            json.load(open(CONFIG["data_dir"].rstrip("/") + "/warnings.json")),
        )
        json.dump(list(warns_removed), open(CONFIG["data_dir"].rstrip("/") + "/warnings.json", "w"))
    return redirect("/")
