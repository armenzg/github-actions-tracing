from __future__ import annotations

import json
import logging
import os
import sys

import requests

from src.github_app import GithubAppToken
from src.github_sdk import GithubClient
from src.web_app_handler import init_config

logging.getLogger().setLevel(os.environ.get("LOGGING_LEVEL", "INFO"))
logging.basicConfig()

# Point this script to the URL of a job and we will trace it
# You give us this https://github.com/getsentry/sentry/runs/5759197422?check_suite_focus=true
# Or give it a path to a file with a webhook payload
# e.g. tests/fixtures/jobA/job.json


def main():
    argument = sys.argv[1]
    token = None

    if argument.startswith("https"):
        _, _, _, org, repo, _, run_id = argument.split("?")[0].split("/")
        req = requests.get(
            f"https://api.github.com/repos/{org}/{repo}/actions/jobs/{run_id}",
        )
        req.raise_for_status()
        job = req.json()
    else:
        with open(argument) as f:
            job = json.load(f)
        org = job["url"].split("/")[4]

    config = init_config()
    if os.environ.get("GH_APP_ID"):
        app = GithubAppToken(**config.gh_app._asdict())
        token = app.get_token()
    else:
        token = os.environ["GH_TOKEN"]

    client = GithubClient(
        token=token,
        dsn=os.environ.get("SENTRY_GITHUB_DSN"),
    )
    client.send_trace(job)


if __name__ == "__main__":
    raise SystemExit(main())
