# One-Hour Feature Study

**Status: draft, provisional feature generation only.** This is not a frozen model experiment or a training-eligibility approval. The configuration pins local cleaned/audit manifests that are ignored by Git; obtain the exact referenced artifacts before reproducing it. No live collection or automatic latest-snapshot selection is performed.

The task is consumption over `[t, t+1h)`, forecast at `t`. Measurement inputs require source-period end plus configured publication delay at or before the issue time. Zero publication delay is an unverified retrospective assumption. API revisions and actual historical publication times remain unresolved.

From this directory:

```bash
python -m oslo_energy.pipeline.run_research_audit --config config.json --output-dir ../../data/audits/statnett
python -m oslo_energy.pipeline.run_features --config config.json --output-dir ../../data/features/statnett
```

Relative input references resolve from the configuration location; output paths resolve from the current working directory. Running from the repository root works by passing `experiments/001_hour_ahead_v1/config.json`. The audit command creates additional evidence; it does not silently replace the config's pinned audit reference.

Changing windows requires a matching newly generated audit reference before features can run. Preserve previous generated executions. After a model experiment is frozen, changed splits, recipes, treatments, or models must receive a new experiment version. A future notebook will call package APIs rather than duplicate feature/training code. Current tests are pytest checks, not forecasting results.

See [feature contracts and leakage checks](../../docs/features.md) for exact feature semantics, output masks, and limitations. Models, fitted preprocessing, and notebook evaluation are not implemented by this study.
