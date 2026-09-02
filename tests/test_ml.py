"""
Scoring the learned layer, and - more importantly - proving what it CANNOT do.

Two halves:

  1. the model works       (it learns signal, it ranks, it calibrates, it persists)
  2. the model is contained (it cannot promote a rung, change a verdict, leak a
                             label, extrapolate silently, or exceed its weight cap)

The second half is the one that matters. An engine whose argument is "the LLM is
never the source of a number" has to be able to say the same thing about the
model that IS learned, and saying it is worth nothing unless a test fails when it
stops being true.
"""
import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from whylayer.contract import Contract
from whylayer.sources import Estate
from whylayer import pipeline as PL
from whylayer import evidence as EV
from whylayer.ml import features as FT
from whylayer.ml import ranker as RK
from whylayer.ml.calibration import Isotonic, brier
from whylayer.ml.gbdt import HistGBDT, auc, ndcg_at_k, load as gbdt_load, save as gbdt_save

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = os.path.join(ROOT, "models", "driver-ranker-v1.json")

C = Contract()
E = Estate(C)


def go(sc, persona="data_analyst"):
    return PL.run(sc, persona, C, E, force_offline=True)


# ===================================================================== the model
def test_gbdt_learns_a_nonlinear_signal():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(2400, 6))
    logit = 1.6 * X[:, 0] * X[:, 1] - 1.2 * np.abs(X[:, 2]) + 0.9 * X[:, 3]
    y = (rng.random(2400) < 1 / (1 + np.exp(-logit))).astype(float)
    m = HistGBDT(objective="logistic", n_estimators=200).fit(
        X[:1800], y[:1800], eval_set=(X[1800:], y[1800:], None))
    assert auc(y[1800:], m.decision_function(X[1800:])) > 0.72, \
        "a boosted tree that cannot beat 0.72 on a clean interaction is broken"


def test_lambdarank_beats_the_heuristic_it_replaces_on_a_synthetic_ranking():
    rng = np.random.default_rng(1)
    Xs, ys, gs = [], [], []
    for g in range(500):
        k = int(rng.integers(3, 7))
        x = rng.normal(size=(k, 5))
        util = 1.4 * x[:, 0] - 0.9 * x[:, 1] ** 2 + rng.normal(scale=0.5, size=k)
        lab = np.zeros(k); lab[int(np.argmax(util))] = 1.0
        Xs.append(x); ys.append(lab); gs += [g] * k
    X, y, gid = np.vstack(Xs), np.concatenate(ys), np.array(gs)
    tr, te = gid < 380, gid >= 420
    m = HistGBDT(objective="lambdarank", n_estimators=250).fit(
        X[tr], y[tr], groups=gid[tr],
        eval_set=(X[(gid >= 380) & (gid < 420)], y[(gid >= 380) & (gid < 420)],
                  gid[(gid >= 380) & (gid < 420)]))
    assert ndcg_at_k(y[te], m.decision_function(X[te]), gid[te], 3) > 0.75


def test_model_serialises_to_readable_json_and_round_trips(tmp_path):
    rng = np.random.default_rng(2)
    X = rng.normal(size=(400, 4)); y = (X[:, 0] > 0).astype(float)
    m = HistGBDT(n_estimators=25).fit(X, y, feature_names=list("abcd"))
    p = str(tmp_path / "m.json")
    gbdt_save(m, p)
    blob = json.load(open(p))                      # plain JSON, not a pickle
    assert blob["kind"] == "HistGBDT" and blob["feature_names"] == list("abcd")
    assert np.allclose(m.decision_function(X), gbdt_load(p).decision_function(X))


def test_isotonic_is_monotone_and_improves_on_the_raw_squash():
    rng = np.random.default_rng(3)
    s = rng.normal(size=3000) * 2
    y = (rng.random(3000) < 1 / (1 + np.exp(-(0.8 * s - 0.5)))).astype(float)
    c = Isotonic().fit(s[:1500], y[:1500])
    p = c.predict(s[1500:])
    assert np.all(np.diff(c.y) >= -1e-12), "a calibration map must be monotone"
    assert brier(p, y[1500:]) <= brier(1 / (1 + np.exp(-s[1500:])), y[1500:])


# =============================================================== the containment
def test_no_post_hoc_field_is_ever_a_feature():
    assert not (set(FT.FEATURES) & FT.FORBIDDEN)
    with pytest.raises(ValueError):
        FT.assert_no_leakage(["share_abs", "outcome_delta"])


def test_the_evidence_rung_is_deliberately_withheld_from_the_model():
    """If the rung were a feature the model would mostly relearn the rung, and the
    fusion in evidence.verdict would be counting the same evidence twice."""
    assert not [f for f in FT.FEATURES if f in ("ladder", "verdict", "rung")]


def test_featurize_is_deterministic_and_finite():
    r = go("S1")
    h = r["hypotheses"][0]
    mv = type("M", (), r["movement"])
    a = FT.featurize(h["hyp"], h["grade"], mv, r["split"])
    b = FT.featurize(h["hyp"], h["grade"], mv, r["split"])
    assert len(a) == FT.N_FEATURES and np.array_equal(a, b)
    assert np.isfinite(a).all(), "a non-finite feature would silently poison a split"
    assert FT.snapshot_id(a) == FT.snapshot_id(b)


def test_ml_cannot_change_a_verdict_or_an_evidence_rung(monkeypatch):
    """The whole authority claim, as an assertion.

    The same runs are executed with the ranker loaded and with it removed. Every
    verdict status and every ladder grade must be byte-identical; only the ORDER
    of leaders is allowed to differ.
    """
    with_ml = {sc: go(sc) for sc in ("S1", "S2", "S4", "S6")}
    assert any(h["grade"].get("ml") for h in with_ml["S1"]["hypotheses"]), \
        "the ranker must actually be active, or this test proves nothing"

    monkeypatch.setattr(PL.MLR, "get",
                        lambda *a, **k: RK.DriverRanker(None, "/nonexistent.json"))
    without = {sc: go(sc) for sc in ("S1", "S2", "S4", "S6")}

    for sc in with_ml:
        assert with_ml[sc]["verdict"]["status"] == without[sc]["verdict"]["status"]
        a = {h["hyp"]["id"]: h["grade"]["ladder"] for h in with_ml[sc]["hypotheses"]}
        b = {h["hyp"]["id"]: h["grade"]["ladder"] for h in without[sc]["hypotheses"]}
        assert a == b, "the ranker moved an evidence rung in %s" % sc
        assert set(a) == set(b), "the ranker added or removed a candidate in %s" % sc


def test_engine_runs_unchanged_when_no_model_is_installed(monkeypatch):
    monkeypatch.setattr(PL.MLR, "get",
                        lambda *a, **k: RK.DriverRanker(None, "/nonexistent.json"))
    r = go("S1")
    assert r["verdict"]["status"] == "CONFIRMED"
    assert r["verdict"]["leader_ids"][0] == "H-SLA"
    assert r["ml_ranker"]["status"] == "unavailable"
    assert not any(m["method"] == "ML" for m in r["telemetry"]["methods"])


def test_a_feature_contract_mismatch_is_refused_not_coerced():
    card = json.load(open(MODEL))
    card["feature_contract"] = "driver-features-v0-from-last-year"
    r = RK.DriverRanker(card, MODEL)
    assert r.status == "refused" and not r.available
    assert r.score([], None) is None


def test_out_of_distribution_candidates_are_flagged_and_neutralised():
    r = RK.get()
    absurd = np.full(FT.N_FEATURES, 1e6)
    ok, n_out = r._ood(absurd)
    assert not ok and n_out > RK.OOD_TOLERANCE * FT.N_FEATURES
    # a flagged candidate falls back to the heuristic term alone
    assert RK.fuse_episode([0.5], [0.99], [False]) == RK.fuse_episode([0.5], None)


def test_the_ml_weight_is_capped_by_governance_not_by_tuning():
    a = RK.fuse_episode([1.0, 0.02], [0.1, 0.9], None, alpha=0.99)
    b = RK.fuse_episode([1.0, 0.02], [0.1, 0.9], None, alpha=RK.MAX_ALPHA)
    assert a == b, "alpha must be clamped to MAX_ALPHA however it is passed"
    assert RK.MAX_ALPHA <= 0.5, "the evidence-bearing term keeps at least half the weight"


def test_fusion_without_a_model_reproduces_the_heuristic_order():
    h = [0.6, 0.2, 0.02]
    assert RK.fuse_episode(h, None) == h
    fused = RK.fuse_episode(h, [None, None, None])
    assert sorted(range(3), key=lambda i: -fused[i]) == [0, 1, 2]


def test_data_quality_still_short_circuits_ahead_of_any_ranking():
    r = go("S4")
    assert r["verdict"]["status"] == "DATA_QUALITY"
    assert r["verdict"]["leader_ids"][0].startswith("H-DQ")


def test_graph_rejected_candidates_are_never_scored_by_the_model():
    for sc in ("S1", "S2", "S6"):
        for h in go(sc)["hypotheses"]:
            if h["grade"]["ladder"] == "REJECTED":
                assert h["grade"].get("ml") is None


# ================================================================ the model card
def test_a_trained_model_ships_with_the_repository():
    assert os.path.exists(MODEL), "run: python -m whylayer.ml.train --build"
    r = RK.get()
    assert r.available and r.version == "driver-ranker-v1"


def test_the_model_card_states_its_holdout_and_its_limits():
    card = json.load(open(MODEL))
    hold = card["holdout"]
    assert card["feature_contract"] == FT.FEATURE_CONTRACT_VERSION
    assert card["model"]["feature_names"] == FT.FEATURES
    for arm in ("heuristic", "ml", "fused"):
        assert hold["ranking"][arm]["top1_accuracy"] is not None
    assert hold["calibration"]["ece"] is not None
    assert hold["candidate_recall"]["overall"] is not None
    assert len(card["limitations"]) >= 3
    assert any("SIMULATED" in x or "simulated" in x for x in card["limitations"])


def test_the_holdout_is_temporal_and_the_learned_arm_actually_helps():
    hold = json.load(open(MODEL))["holdout"]
    tr_end = hold["split"]["train_window"].split("..")[1]
    te_start = hold["split"]["test_window"].split("..")[0]
    assert te_start > tr_end, "train and test must not overlap in time"
    assert (hold["ranking"]["fused"]["top1_accuracy"]
            > hold["ranking"]["heuristic"]["top1_accuracy"]), \
        "a fused ranking that does not beat the heuristic has no reason to ship"


def test_telemetry_declares_the_ml_step_and_still_reports_zero_llm_numbers():
    r = go("S1")
    ml = [m for m in r["telemetry"]["methods"] if m["method"] == "ML"]
    assert len(ml) == 1 and "driver-ranker" in ml[0]["detail"]
    assert r["telemetry"]["method_mix"]["counts"].get("ML") == 1
    assert r["telemetry"]["method_mix"]["pct_non_llm"] == 100.0


def test_every_scored_candidate_carries_its_provenance():
    for h in go("S1")["hypotheses"]:
        ml = h["grade"].get("ml")
        if not ml:
            continue
        for k in ("probability", "rank", "model_version", "feature_contract",
                  "feature_snapshot_id", "in_distribution", "authority"):
            assert k in ml, "a score with no %s cannot be audited later" % k
        assert 0.0 <= ml["probability"] <= 1.0
        assert "advisory" in ml["authority"]


def test_the_feature_snapshot_is_written_so_feedback_can_become_a_label():
    from whylayer import feedback as FB
    r = go("S1")
    p = os.path.join(FB.SNAPSHOTS, "%s.json" % r["telemetry"]["run_id"])
    assert os.path.exists(p)
    blob = json.load(open(p))
    assert blob["feature_contract"] == FT.FEATURE_CONTRACT_VERSION
    for cand in blob["candidates"].values():
        assert len(cand["vector"]) == FT.N_FEATURES


def test_the_holdout_split_never_puts_one_episode_on_both_sides():
    from whylayer.ml import dataset as DS
    df = DS.load_table()
    tr, ca, te = DS.time_split(df)
    a, b, c = (set(df[m].episode_id) for m in (tr, ca, te))
    assert not (a & b) and not (b & c) and not (a & c)
    assert df[tr].window_start.max() <= df[te].window_start.min()


def test_the_training_table_carries_exactly_the_contract_features():
    from whylayer.ml import dataset as DS
    cols = set(DS.load_table().columns)
    assert set(FT.FEATURES) <= cols
    assert not (cols & (FT.FORBIDDEN - {"label", "ladder"})), \
        "the corpus must not carry an outcome field that could be trained on"


def test_entitlement_moves_the_model_score_but_never_the_leader():
    """Two properties at once, and the second is the governance one.

    A persona denied the CRM verbatims loses the corroboration evidence, so the
    ranker's score for the service failure FALLS - the feature vector genuinely
    responds to what the persona may see, rather than the entitlement being
    applied only to the text. Denying evidence can never raise the model's
    confidence in the evidence-backed candidate.

    And whatever the model then thinks, the leader is the same driver for every
    persona looking at the same movement. The evidence term keeps at least half
    the weight, so it decides ties and near-ties.
    """
    seen = {}
    for pk in ("cfo", "rsm_north", "supply_chain_lead", "data_analyst"):
        r = go("S1", pk)
        cands = (r["ml_ranker"].get("candidates") or {})
        seen[pk] = {"leader": r["verdict"]["leader_ids"][0],
                    "sla": cands.get("H-SLA", {}).get("probability"),
                    "mix": cands.get("H-MIX", {}).get("probability")}

    assert {v["leader"] for v in seen.values()} == {"H-SLA"}, \
        "entitlement changed which driver leads: %s" % seen
    assert all(v["sla"] is not None and v["mix"] is not None for v in seen.values())

    unrestricted = seen["data_analyst"]["sla"]
    for pk in ("cfo", "supply_chain_lead"):          # both deny crm_verbatim
        assert seen[pk]["sla"] <= unrestricted, \
            "%s sees less evidence yet scores higher - entitlement is not reaching " \
            "the feature vector" % pk

    # where the model cannot separate the two candidates, the evidence term does
    ties = [pk for pk, v in seen.items() if v["sla"] <= v["mix"]]
    assert ties, "expected the restricted personas to lose the corroboration margin"
    for pk in ties:
        assert seen[pk]["leader"] == "H-SLA", \
            "%s: the model did not favour H-SLA and the ladder failed to hold it" % pk
