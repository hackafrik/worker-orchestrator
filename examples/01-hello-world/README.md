# Example 01: Hello World

The simplest possible manifest. Three workers generate greetings in different tones, then a fourth worker synthesizes the best elements into a single message.

## What it demonstrates

- **Parallel phase**: Three workers run simultaneously (formal, casual, poetic)
- **Sequential phase**: Synthesis worker waits for all three to complete
- **Rubric evaluation**: Each output is scored on tone, completeness, enthusiasm
- **Best-of-N synthesis**: The highest-scoring greeting is selected
- **Budget constraints**: 3 workers max, $0.01 cost ceiling, 60s timeout

## Run it

```bash
python3 ../../src/orchestrator.py manifest.json
```

## Expected behavior

1. All three greeting workers spawn in parallel
2. Each outputs a greeting string
3. The rubric scores each output
4. The synthesize phase waits for completion
5. Final output: a single greeting acknowledging all three tones
