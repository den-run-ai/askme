Fix a real Requests bug: requests.exceptions.JSONDecodeError cannot be unpickled.
For example, constructing JSONDecodeError('Extra data', '{}{}', 2), then calling
pickle.loads(pickle.dumps(error)), raises a TypeError about missing constructor
arguments. This also prevents errors crossing process boundaries normally.

Make round trips preserve the exception type, message, document, position,
line/column, and string representation. Preserve existing exception behavior.
Keep production changes confined to src/requests/exceptions.py; add a focused
regression test under tests/ if useful. Inspect, fix, run a relevant local test,
and finish. This checkout contains the actual Requests repository before the fix.
Use the available Python and standard-library json backend. PYTHONPATH already
points to this checkout's src directory. Do not install packages, access the
network, inspect other workspaces, or change unrelated files.
