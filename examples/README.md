# Example conversations

`scenarios.json` defines five scripted conversations covering the scenarios from the challenge brief
(price lookup, catalog filter, store info, return policy) plus order tracking with identity
verification and out-of-scope handling.

The transcripts (`01-….md` … `05-….md`) are **recorded from the real agent**, not written by hand:

```bash
python scripts/record_examples.py        # needs a configured API key (see main README)
```

Every transcript lists the tools the model called and their raw results, so you can check each
number the agent stated against the data. The script also asserts that the expected tools were used
and that key facts appear in the answers (exit code ≠ 0 otherwise).

The store clock is pinned per scenario (`now`), so the facts (dates, windows, prices) are reproducible; the model's exact wording varies between runs. The dataset's orders end in March 2026, so scenarios about order status/returns use dates in that window.