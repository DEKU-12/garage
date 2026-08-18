"""Follows events.jsonl and fans out to WS clients. Build week: 3.

150 ms poll interval, comfortably inside NFR-6's 250 ms event-to-pixel budget.
On connect the client sends its last `seq`; the server replays the gap from
disk then switches to live -- so reconnect and cold start are the SAME code
path (TAD §5.1).
"""
