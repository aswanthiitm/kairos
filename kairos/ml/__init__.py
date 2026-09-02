"""
The learned layer.

Everything in this package is a LEARNED PRIOR over candidate drivers. It ranks;
it never adjudicates. The evidence ladder in ``kairos.evidence`` remains the
sole arbiter of whether a candidate may be called a cause, and no code path in
this package can promote a rung, change a verdict status, or add or remove a
candidate. See ``ranker.DriverRanker.AUTHORITY`` for the enforced contract.

Layout
------
    gbdt.py         histogram gradient-boosted trees (numpy only, JSON model file)
    calibration.py  isotonic regression, so a score becomes a probability
    features.py     the versioned feature contract + the leakage guard
    ranker.py       load / score / persist, with the out-of-distribution gate
    dataset.py      builds the training table by running the REAL engine
    evaluate.py     time-based holdout scoring: heuristic vs ML vs fused
    train.py        the end-to-end trainer entry point
"""
