"""Run the reference ``askme.py`` CLI against recorded model replies.

The native port (``native/askme_native.py``) answers model requests from a
``--replay`` file. This driver gives the reference harness the same transport
by replacing ``requests.post`` at the HTTP boundary, so both implementations
consume one reply file and can be compared request by request.

usage: native_replay_upstream.py REPLAY REQUEST_LOG [askme arguments...]
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests  # noqa: E402

import askme  # noqa: E402
from actions import ActionTransport, DecodedAction  # noqa: E402


class _Reply:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


def main(argv):
    replay_path, request_log, cli_args = argv[0], argv[1], argv[2:]
    entries = json.loads(Path(replay_path).read_text())
    position = [0]
    truncated = [False]

    def post(api, json=None, headers=None, timeout=None):  # noqa: A002
        with open(request_log, "a") as stream:
            record = {"api": api, "timeout": timeout, "headers": list(headers), "body": json}
            stream.write(_dumps(record) + "\n")
        truncated[0] = False
        if position[0] >= len(entries):
            raise requests.exceptions.ConnectionError("replay exhausted")
        entry = entries[position[0]]
        position[0] += 1
        control = entry.get("askme_replay") if isinstance(entry, dict) else None
        if isinstance(control, dict):
            if "transport_error" in control:
                raise requests.exceptions.ConnectionError(control["transport_error"])
            truncated[0] = bool(control.get("content_truncated"))
            text = control["text"] if "text" in control else _dumps(control.get("body"))
            return _Reply(control.get("status", 200), text)
        return _Reply(200, _dumps(entry))

    reference_ask = askme.LLMClient.ask

    def ask(self, messages, *args, **kwargs):
        # A replay entry may mark its write as a trusted partial payload, the
        # metadata an injected client supplies through ActionTransport.
        result = reference_ask(self, messages, *args, **kwargs)
        if truncated[0] and isinstance(result, DecodedAction) and result.envelope.name == "write":
            return DecodedAction(result.envelope, ActionTransport(content_truncated=True))
        return result

    requests.post = post
    time.sleep = lambda _seconds: None
    askme.LLMClient.ask = ask
    return askme._main(cli_args)


_dumps = json.dumps

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
