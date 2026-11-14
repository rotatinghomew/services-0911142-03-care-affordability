from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from urllib.request import Request, urlopen
import json
import math
import unittest

from care_affordability.domain import validate
from care_affordability.scenario import (
    MISSING_TRANSPORT, MODES, affordability, compare, validate_scenario)
from care_affordability.server import make_server
from care_affordability.service import Workbench

ROOT = Path(__file__).resolve().parents[1]


def load_data():
    return json.loads((ROOT / "fixtures" / "data.json").read_text(encoding="utf-8"))


class ScenarioValidationTests(unittest.TestCase):
    def setUp(self):
        self.data = load_data()

    def test_valid_modes_accepted(self):
        for mode in MODES:
            normalized, tariff = validate_scenario(
                self.data, {"period": "2026-10", "zone": "all", "mode": mode})
            self.assertEqual(normalized["mode"], mode)
            self.assertEqual(tariff["period"], "2026-10")

    def test_invalid_inputs_rejected(self):
        cases = [
            {"period": "2000-01", "zone": "all", "mode": "observed"},
            {"period": "2026-10", "zone": "coast", "mode": "observed"},
            {"period": "2026-10", "zone": "all", "mode": "middle"},
            {"period": "2026-10", "zone": "all", "mode": "observed", "burden_line": 0},
            {"period": "2026-10", "zone": "all", "mode": "observed", "burden_line": 9},
            {"period": "2026-10", "zone": "all", "mode": "observed", "missing_transport": "guess"},
            {"period": "2026-10", "zone": "all", "mode": "observed",
             "tariff_override": {"hour_price": -1}},
            {"period": "2026-10", "zone": "all", "mode": "observed",
             "tariff_override": {"eligible_share": 2}},
            {"period": "2026-10", "zone": "all", "mode": "observed", "uniform_hours": 10},
        ]
        for scenario in cases:
            with self.assertRaises(ValueError):
                validate_scenario(self.data, scenario)

    def test_override_is_complete_and_does_not_mutate_tariff_table(self):
        original = deepcopy(self.data["tariffs"])
        scenario = {"period": "2026-10", "zone": "all", "mode": "uniform",
                    "tariff_override": {"hour_price": 40}}
        _, tariff = validate_scenario(self.data, scenario)
        self.assertEqual(tariff["hour_price"], 40.0)
        self.assertEqual(tariff["eligible_share"], 0.35)
        self.assertEqual(self.data["tariffs"], original)


class HouseholdAccountingTests(unittest.TestCase):
    def setUp(self):
        self.data = load_data()
        validate(self.data)

    def test_cap_is_applied_once_per_household(self):
        view = affordability(self.data, {
            "period": "2026-10", "zone": "all", "mode": "uniform"})
        tariff = next(row for row in self.data["tariffs"] if row["period"] == "2026-10")
        for row in view["rows"]:
            uncapped = row["steps"]["uncapped_subsidy"]
            after_cap = row["components"]["subsidy_after_cap"]
            self.assertEqual(after_cap, min(uncapped, tariff["household_cap"]))
            self.assertGreaterEqual(uncapped - after_cap, 0.0)
            self.assertAlmostEqual(
                after_cap + row["components"]["cap_clawback"], uncapped, places=6)

    def test_missing_transport_policies_keep_interval_and_move_point(self):
        def run(policy):
            view = affordability(self.data, {
                "period": "2026-10", "zone": "all", "mode": "observed",
                "missing_transport": policy})
            target = next(row for row in view["rows"] if row["missing_transport_items"] > 0)
            return target
        median_row = run("impute_median")
        zero_row = run("zero")
        self.assertGreater(median_row["steps"]["transport_point"],
                           zero_row["steps"]["transport_point"])
        self.assertEqual(median_row["net_spend_low"], zero_row["net_spend_low"])
        self.assertEqual(median_row["net_spend_high"], zero_row["net_spend_high"])
        self.assertLess(median_row["net_spend_point"], median_row["net_spend_high"] + 1e-9)
        for policy in MISSING_TRANSPORT:
            run(policy)

    def test_disputed_membership_always_indeterminate(self):
        for line in (0.05, 0.99):
            view = affordability(self.data, {
                "period": "2026-10", "zone": "all", "mode": "uniform", "burden_line": line})
            for row in view["rows"]:
                if row["membership_status"] == "disputed":
                    self.assertEqual(row["status"], "indeterminate")
                    self.assertTrue(any("成员关系待确认" in reason for reason in row["reasons"]))

    def test_open_income_never_yields_finite_upper_ratio(self):
        view = affordability(self.data, {
            "period": "2026-10", "zone": "all", "mode": "uniform"})
        open_rows = [row for row in view["rows"] if row["income_high"] is None]
        self.assertTrue(open_rows)
        for row in open_rows:
            self.assertIsNone(row["ratio_low"])
        # 没有任何输出包含无穷或 NaN，JSON 可安全序列化
        encoded = json.dumps(view, allow_nan=False)
        self.assertNotIn("Infinity", encoded)
        self.assertNotIn("NaN", encoded)

    def test_burden_classification_respects_interval_bounds(self):
        view = affordability(self.data, {
            "period": "2026-10", "zone": "all", "mode": "uniform", "burden_line": 0.3})
        for row in view["rows"]:
            if row["status"] == "over_burden":
                self.assertIsNotNone(row["ratio_low"])
                self.assertGreater(row["ratio_low"], 0.3)
            if row["status"] == "under_burden":
                self.assertIsNotNone(row["ratio_high"])
                self.assertLessEqual(row["ratio_high"], 0.3)
            if row["status"] == "indeterminate" and row["membership_status"] != "disputed":
                self.assertTrue(row["reasons"])

    def test_observed_marks_unmet_need_but_uniform_does_not(self):
        observed = affordability(self.data, {
            "period": "2026-10", "zone": "all", "mode": "observed"})
        uniform = affordability(self.data, {
            "period": "2026-10", "zone": "all", "mode": "uniform"})
        self.assertGreater(observed["summary"]["存在未满足照护需要家庭数"], 0)
        self.assertEqual(uniform["summary"]["存在未满足照护需要家庭数"], 0)
        unmet = next(row for row in observed["rows"] if row["unmet_need_bills"] > 0)
        excluded = [step for step in unmet["bill_steps"] if not step["included"]]
        self.assertTrue(excluded)
        self.assertTrue(all("未购买" in step["reason"] for step in excluded))

    def test_engine_does_not_mutate_inputs(self):
        snapshot = deepcopy(self.data)
        affordability(self.data, {
            "period": "2026-10", "zone": "all", "mode": "uniform",
            "tariff_override": {"hour_price": 99, "household_cap": 1}})
        self.assertEqual(snapshot, self.data)

    def test_trace_contains_every_bill_and_household_level_steps(self):
        view = affordability(self.data, {
            "period": "2026-10", "zone": "all", "mode": "uniform"})
        member_counts = {}
        for member in self.data["members"]:
            member_counts[member["household"]] = member_counts.get(member["household"], 0) + 1
        for row in view["rows"]:
            october_bills = sum(1 for bill in self.data["bills"]
                                if bill["period"] == "2026-10"
                                and any(member["member"] == bill["member"]
                                        for member in self.data["members"]
                                        if member["household"] == row["household"]))
            self.assertEqual(len(row["bill_steps"]), october_bills)
            for key in ("service_cost", "subsidy_after_cap", "cap_clawback",
                        "net_spend_low", "net_spend_high", "disposable_low"):
                self.assertIn(key, row["steps"])


class PopulationAndComparisonTests(unittest.TestCase):
    def setUp(self):
        self.data = load_data()

    def test_weighted_shares_sum_to_one_hundred(self):
        view = affordability(self.data, {
            "period": "2026-10", "zone": "all", "mode": "observed"})
        total = sum(bucket["weighted_share_pct"] for bucket in view["buckets"])
        self.assertAlmostEqual(total, 100.0, places=4)

    def test_bootstrap_is_deterministic_and_brackets_point(self):
        scenario = {"period": "2026-10", "zone": "all", "mode": "uniform"}
        first = affordability(self.data, scenario)
        second = affordability(self.data, deepcopy(scenario))
        self.assertEqual(first["summary"], second["summary"])
        interval = first["summary"]["超过负担线比例95pct自助区间"]
        self.assertIsNotNone(interval)
        point = first["summary"]["超过负担线家庭比例_pct"] / 100
        self.assertLessEqual(interval[0], point + 1e-9)
        self.assertGreaterEqual(interval[1], point - 1e-9)
        self.assertEqual(first["uncertainty"]["seed"], second["uncertainty"]["seed"])

    def test_compare_uses_same_household_base_and_pairs_bootstrap(self):
        result = compare(self.data,
                         {"period": "2026-09", "zone": "all", "mode": "uniform"},
                         {"period": "2026-10", "zone": "all", "mode": "uniform"})
        base_ids = [row["household"] for row in result["base_view"]["rows"]]
        alt_ids = [row["household"] for row in result["alternative_view"]["rows"]]
        self.assertEqual(base_ids, alt_ids)
        self.assertEqual(result["common_base"]["household_count"], len(base_ids))
        self.assertIsNotNone(result["over_burden_share"]["delta_95pct_ci"])

    def test_compare_rejects_different_zones(self):
        with self.assertRaises(ValueError):
            compare(self.data,
                    {"period": "2026-10", "zone": "urban", "mode": "observed"},
                    {"period": "2026-10", "zone": "rural", "mode": "observed"})

    def test_price_rise_shows_in_service_component(self):
        base = affordability(self.data, {
            "period": "2026-10", "zone": "all", "mode": "uniform"})
        raised = affordability(self.data, {
            "period": "2026-10", "zone": "all", "mode": "uniform",
            "tariff_override": {"hour_price": 40}})
        base_service = next(item for item in base["components"]
                            if item["component"] == "service_cost")["weighted_mean"]
        raised_service = next(item for item in raised["components"]
                              if item["component"] == "service_cost")["weighted_mean"]
        self.assertGreater(raised_service, base_service)


class TrialServiceAndHttpTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.workbench = Workbench(ROOT, self.temporary.name)
        self.server = make_server(ROOT, self.temporary.name, port=0)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def post(self, path, payload):
        request = Request(self.base + path, data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"})
        with urlopen(request) as response:
            return response.status, json.load(response)

    def test_trial_endpoint_and_saved_notebook_kind(self):
        status, result = self.post("/api/trial", {
            "scenario": {"period": "2026-10", "zone": "all", "mode": "uniform",
                         "burden_line": 0.3}})
        self.assertEqual(status, 200)
        self.assertEqual(len(result["view"]["rows"]), 150)
        status, saved = self.post("/api/notebooks", {
            "title": "uniform 试算存档", "kind": "trial",
            "scenario": result["scenario"]})
        self.assertEqual(status, 201)
        self.assertEqual(saved["kind"], "trial")
        reopened = self.workbench.store.notebook(saved["id"])
        self.assertEqual(reopened["result"]["view"]["summary"],
                         result["view"]["summary"])
        self.assertEqual(reopened["request_body"]["kind"], "trial")

    def test_compare_endpoint_rejects_bad_payload(self):
        from urllib.error import HTTPError
        request = Request(self.base + "/api/compare",
                          data=json.dumps({"base": {}}).encode(),
                          headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(caught.exception.code, 400)
        caught.exception.close()

    def test_compare_notebook_roundtrip(self):
        status, saved = self.post("/api/notebooks", {
            "title": "月份对比", "kind": "compare",
            "base": {"period": "2026-09", "zone": "all", "mode": "uniform"},
            "alternative": {"period": "2026-10", "zone": "all", "mode": "uniform"}})
        self.assertEqual(status, 201)
        self.assertEqual(saved["kind"], "compare")
        self.assertEqual(saved["kind"], "compare")
        self.assertEqual(saved["result"]["view"]["common_base"]["household_count"], 150)

    def test_legacy_explore_save_defaults_to_explore_kind(self):
        status, saved = self.post("/api/notebooks", {"title": "旧式浏览保存"})
        self.assertEqual(status, 201)
        self.assertEqual(saved["kind"], "explore")
        self.assertIsNone(saved["request_body"])

    def test_restart_migrates_state_and_keeps_trial_record(self):
        _, saved = self.post("/api/notebooks", {
            "title": "重启保留试算", "kind": "trial",
            "scenario": {"period": "2026-10", "zone": "all", "mode": "observed"}})
        restarted = Workbench(ROOT, self.temporary.name)
        reopened = restarted.store.notebook(saved["id"])
        self.assertEqual(reopened["kind"], "trial")
        self.assertEqual(reopened["result"]["view"]["summary"]["家庭数"], 150)


if __name__ == "__main__":
    unittest.main()
