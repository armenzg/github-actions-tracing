import gzip
import io
import logging
import os
import uuid
from datetime import datetime

import requests
from sentry_sdk.envelope import Envelope
from sentry_sdk.utils import format_timestamp

logging.basicConfig(level=os.environ.get("LOGGING_LEVEL", "INFO"))

# We need an authorized token to fetch the API. If you have SSO on your org you will need to grant permission
# Your app and the Github webhook will share this secret
# You can create an .env file and place the token in it
GH_TOKEN = os.environ.get("GH_TOKEN")
# Where to report Github actions transactions
SENTRY_GITHUB_DSN = os.environ.get("SENTRY_GITHUB_DSN")


def get(url):
    headers = {}
    if GH_TOKEN and url.find("github.com") >= 0:
        headers["Authorization"] = f"token {GH_TOKEN}"
    req = requests.get(url, headers=headers)
    if not req.ok:
        raise Exception(req.text)
    return req


def get_uuid():
    return uuid.uuid4().hex


def send_envelope(envelope):
    if not SENTRY_GITHUB_DSN:
        return None
    base_uri, project_id = SENTRY_GITHUB_DSN.rsplit("/", 1)
    sentry_key = base_uri.rsplit("@")[0].rsplit("https://")[1]
    headers = {
        "event_id": get_uuid(),  # Does this have to match anything?
        "sent_at": format_timestamp(datetime.utcnow()),
        "Content-Type": "application/x-sentry-envelope",
        "Content-Encoding": "gzip",
        "X-Sentry-Auth": f"Sentry sentry_key={sentry_key},"
        + f"sentry_client=gha-sentry/0.0.1,sentry_timestamp={str(datetime.utcnow())},"
        + "sentry_version=7",
    }

    # '{BASE_URI}/api/{PROJECT_ID}/{ENDPOINT}/'
    url = f"{base_uri}/api/{project_id}/envelope/"
    if GH_TOKEN and url.find("github.com") >= 0:
        headers["Authorization"] = f"token {GH_TOKEN}"

    body = io.BytesIO()
    with gzip.GzipFile(fileobj=body, mode="w") as f:
        envelope.serialize_into(f)

    req = requests.post(url, data=body.getvalue(), headers=headers)
    if not req.ok:
        raise Exception(req.text)
    return req


# XXX: This is a slow call
def get_extra_metadata(job):
    runs = get(job["run_url"]).json()
    workflow = get(runs["workflow_url"]).json()
    meta = {
        # This provides human friendly transaction names
        "name": f'{workflow["name"]}/{job["name"]}',
        "head_branch": runs.get("head_branch"),
        "head_sha": runs.get("head_sha"),
        "author": runs["head_commit"]["author"],
        "run_attempt": runs["run_attempt"],
    }
    if runs.get("pull_requests"):
        meta[
            "pull_request"
        ] = f'https://github.com/{runs["head_repository"]["full_name"]}/pull/{runs["pull_requests"][0]["number"]}'

    return meta


def _base_transaction():
    trace_id = get_uuid()
    parent_span_id = get_uuid()[:16]
    return {
        "event_id": get_uuid(),
        # The distinctive feature of a Transaction is type: "transaction".
        "type": "transaction",
        "transaction": "default",
        "contexts": {
            "trace": {
                "trace_id": trace_id,
                "span_id": parent_span_id,
                "type": "trace",
            },
        },
        "user": {},
    }


# https://develop.sentry.dev/sdk/event-payloads/span/
def _generate_spans(steps, parent_span_id, trace_id):
    spans = []
    for step in steps:
        try:
            spans.append(
                {
                    "op": step["name"],
                    "name": step["name"],
                    "parent_span_id": parent_span_id,
                    "span_id": get_uuid()[:16],
                    "start_timestamp": step["started_at"],
                    "timestamp": step["completed_at"],
                    "trace_id": trace_id,
                }
            )
        except Exception as e:
            logging.exception(e)
    return spans


github_status_trace_status = {"success": "ok", "failure": "internal_error"}
# Documentation about traces, transactions and spans
# https://docs.sentry.io/product/sentry-basics/tracing/distributed-tracing/#traces
# https://develop.sentry.dev/sdk/performance/
def _generate_trace(workflow):
    meta = get_extra_metadata(workflow)
    transaction = _base_transaction()
    # Transactions have name, spans don't.
    transaction["transaction"] = meta["name"]
    transaction["user"] = meta["author"]
    # When ingesting old data during development (e.g. using fixtures), Sentry's UI will
    # show an error for transactions with "Clock drift detected in SDK"; It is harmeless.
    transaction["start_timestamp"] = workflow["started_at"]
    transaction["timestamp"] = workflow["completed_at"]

    transaction["tags"] = {
        "job_status": workflow["conclusion"],  # e.g. success, failure, skipped
        "branch": meta["head_branch"],
        # To create metrics of how often we re-run
        "run_attempt": meta["run_attempt"],
        # To filter jobs that run on a specific sha
        "head_sha": meta["head_sha"],
    }
    if meta.get("pull_request"):
        transaction["tags"]["pull_request"] = meta["pull_request"]
    transaction["contexts"]["trace"]["op"] = workflow["name"]
    transaction["contexts"]["trace"]["description"] = workflow["name"]
    transaction["contexts"]["trace"]["status"] = github_status_trace_status.get(
        workflow["conclusion"], "unimplemented"
    )
    transaction["spans"] = _generate_spans(
        workflow["steps"],
        transaction["contexts"]["trace"]["span_id"],
        transaction["contexts"]["trace"]["trace_id"],
    )
    return transaction


def send_trace(workflow):
    # This can happen when the workflow is skipped and there are no steps
    if workflow["conclusion"] == "skipped":
        logging.info(
            f"We are ignoring '{workflow['name']}' because it was skipped -> {workflow['html_url']}"
        )
        return
    trace = _generate_trace(workflow)
    if trace:
        envelope = Envelope()
        envelope.add_transaction(trace)
        send_envelope(envelope)
