import logging
import os

import sentry_sdk
from flask import jsonify, request, Flask
from sentry_sdk import capture_exception

from sentry_sdk.integrations.flask import FlaskIntegration

from .handle_event import handle_event

APP_DSN = os.environ.get("APP_DSN")
if APP_DSN:
    # This tracks errors and performance of the app itself rather than GH workflows
    sentry_sdk.init(
        dsn=APP_DSN,
        integrations=[FlaskIntegration()],
        # Set traces_sample_rate to 1.0 to capture 100%
        # of transactions for performance monitoring.
        # We recommend adjusting this value in production.
        traces_sample_rate=1.0,
        environment=os.environ.get("FLASK_ENV", "production"),
    )

LOGGING_LEVEL = os.environ.get("LOGGING_LEVEL", "INFO")
logger = logging.getLogger(__name__)
logger.setLevel(LOGGING_LEVEL)

app = Flask(__name__)


@app.route("/", methods=["POST"])
def main():
    # Top-level crash preventing try block
    try:
        reason, http_code = handle_event(request.json, request.headers)
        return jsonify({"reason": reason}), http_code
    except Exception as e:
        logger.exception(e)
        capture_exception(e)
        return jsonify({"reason": "There was an error."}), 500


if os.environ.get("FLASK_ENV") == "development":

    @app.route("/debug-sentry")
    def trigger_error():
        try:
            1 / 0
        except Exception as e:
            # Report it to Sentry
            capture_exception(e)
            # Let Flask handle the rest
            raise e
