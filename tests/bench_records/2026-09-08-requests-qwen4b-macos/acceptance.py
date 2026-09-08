"""Post-run checks, never copied into the model's workspace or prompt."""

import json
import pickle
import sys
from pathlib import Path


def check(workspace):
    root = Path(workspace).resolve()
    sys.path.insert(0, str(root / "src"))
    import requests

    assert Path(requests.__file__).resolve() == root / "src/requests/__init__.py"
    assert requests.compat.json.__name__ == "json", "This protocol freezes stdlib json"
    checks = 0
    for message, document, position in [
        ("Extra data", "{}{}", 2),
        ("Expecting value", '{\n  "value": 雪}', 13),
        ("Expecting value", "", 0),
    ]:
        error = requests.exceptions.JSONDecodeError(message, document, position)
        for protocol in range(pickle.HIGHEST_PROTOCOL + 1):
            restored = pickle.loads(pickle.dumps(error, protocol=protocol))
            assert type(restored) is type(error)
            for attr in ("msg", "doc", "pos", "lineno", "colno", "args"):
                assert getattr(restored, attr) == getattr(error, attr), attr
            assert str(restored) == str(error)
            assert repr(restored) == repr(error)
            checks += 1
    response = requests.Response()
    response._content = b'{"answer": 42}'
    assert response.json() == {"answer": 42}
    checks += 1
    response._content = b'{"bad":}'
    try:
        response.json()
    except requests.exceptions.JSONDecodeError as error:
        restored = pickle.loads(pickle.dumps(error))
        assert isinstance(restored, requests.exceptions.RequestException)
        assert (restored.msg, restored.doc, restored.pos) == (error.msg, error.doc, error.pos)
        checks += 1
    else:
        raise AssertionError("Invalid JSON must still raise JSONDecodeError")
    ordinary = requests.exceptions.RequestException("original")
    assert pickle.loads(pickle.dumps(ordinary)).args == ordinary.args
    return {"accepted": True, "checks": checks + 1}


if __name__ == "__main__":
    print(json.dumps(check(sys.argv[1])))
