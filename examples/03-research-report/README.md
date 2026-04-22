# Example 03: Research Report

A multi-phase research pipeline that searches for information from three angles, summarizes each result, then synthesizes an executive report.

## What it demonstrates

- **Map-reduce pattern**: Search → Summarize → Synthesize
- **HTTP API adapter**: Uses Perplexity API for real-time web search
- **Phase dependencies**: Each phase waits for the previous
- **Multi-criterion rubrics**: Coverage, recency, technical depth, relevance
- **Real-world cost**: $0.50 ceiling for three API calls + synthesis

## Run it

```bash
export PERPLEXITY_API_KEY=your_key_here
python3 ../../src/orchestrator.py manifest.json
```

## Expected behavior

1. **Phase 1 (search)**: Three parallel HTTP calls to Perplexity for benchmarks, architecture, and use cases
2. **Phase 2 (summarize)**: Each search result condensed to structured JSON
3. **Phase 3 (synthesize)**: Final report combining all three summaries into executive markdown
4. Output: `vector_databases_2025.md`

## Variations

- Swap `http_api` for `python_script` with local search
- Add a fourth phase for peer-review (another worker critiques the report)
- Use `docker` adapter to run summarization in an isolated container
