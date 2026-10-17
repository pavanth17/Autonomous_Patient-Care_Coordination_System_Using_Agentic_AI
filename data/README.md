# Demo vital-sign training data

`demo_vitals_training.csv` is a small, fully synthetic and de-identified
dataset for exercising CareSync's risk-model plumbing. Each row contains one
vital-sign snapshot and a `risk_label` of `0` (stable) or `1` (high risk).

It is **not** clinical evidence and must not be used to make, validate, or
automate real treatment decisions. Before use outside a demonstration,
replace it with governed, representative, properly labeled data and complete
clinical validation, privacy review, bias assessment, and monitoring.

The application trains its supplementary demo model from this file at startup.
Core risk alerts continue to use the transparent threshold and trend rules.
