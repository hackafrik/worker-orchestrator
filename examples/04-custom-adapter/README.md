# Example 04: Custom Adapter

Shows how to register and use a custom adapter that integrates with any external service.

## What it demonstrates

- **Adapter registry extension**: Adding a custom adapter to `src/adapters.py`
- **HTTP polling pattern**: Worker that submits a job, polls for completion, then retrieves results
- **Custom worker configuration**: Arbitrary key-value pairs passed to the adapter

## The Use Case

You have an internal ML inference server. You want to:
1. Submit a batch inference job
2. Poll every 5s until completion
3. Retrieve the results

## Custom Adapter Code

```python
# custom_inference_adapter.py
import time, requests
from typing import Dict, Any

def submit_and_poll(config: Dict[str, Any]) -> str:
    endpoint = config["submit_endpoint"]
    poll_endpoint = config["poll_endpoint"]
    payload = config["payload"]
    headers = config.get("headers", {})
    poll_interval = config.get("poll_interval_seconds", 5)
    max_wait = config.get("max_wait_seconds", 300)

    # Submit job
    resp = requests.post(endpoint, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    job_id = resp.json()["job_id"]

    # Poll until done
    waited = 0
    while waited < max_wait:
        status = requests.get(f"{poll_endpoint}/{job_id}", headers=headers, timeout=30)
        status.raise_for_status()
        data = status.json()
        if data["status"] == "completed":
            return data["result"]
        time.sleep(poll_interval)
        waited += poll_interval

    raise TimeoutError(f"Job {job_id} did not complete within {max_wait}s")
```

## Register It

In `src/adapters.py`, add to the `ADAPTERS` dict:

```python
from custom_inference_adapter import submit_and_poll

ADAPTERS = {
    ...,
    "ml_inference": submit_and_poll,
}
```

## Manifest Snippet

```json
{
  "id": "batch-inference",
  "adapter": "ml_inference",
  "config": {
    "submit_endpoint": "https://ml.internal/api/v1/jobs",
    "poll_endpoint": "https://ml.internal/api/v1/jobs",
    "payload": {"model": "embedding-v3", "inputs": [...]},
    "headers": {"Authorization": "Bearer ${ML_API_KEY}"},
    "poll_interval_seconds": 5,
    "max_wait_seconds": 120
  },
  "rubric": [
    {"criterion": "latency", "weight": 0.5, "description": "Completed within 60s"},
    {"criterion": "accuracy", "weight": 0.5, "description": "Result matches expected schema"}
  ]
}
```

## Key Insight

Adapters are just Python callables. They can:
- Spawn subprocesses (like `cli`)
- Make HTTP calls (like `http_api`)
- Run Docker containers (like `docker`)
- **Do anything**: read from queues, write to databases, interact with hardware

The manifest just describes *what* to do. The adapter defines *how*.
