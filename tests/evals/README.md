# Tagging evals

`labeled.jsonl` holds one JSON object per line: `{"creative_id": "<uuid>", "labels": {"hook_type": "question", ...}}`.
Label at least 50 creatives from the connected account (mix of image and video) by hand after the first backfill.
Run `make evals` (needs AI_GATEWAY_API_KEY). CI does not run this; run it before changing
`taxonomy.yaml` or `prompts.py` and again after, and record both numbers in the PR.
