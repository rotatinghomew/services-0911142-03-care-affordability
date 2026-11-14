from pathlib import Path
import importlib
import json
import sys
import tempfile
import threading
import unittest

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class BrowserWorkflow(unittest.TestCase):
    def test_scope_freeze_raw_and_export(self):
        product = json.loads((ROOT / "product.json").read_text())
        package = json.loads((ROOT / "package.json").read_text())
        module = package["scripts"]["start"].split()[2]
        server_module = importlib.import_module(module + ".server")
        with tempfile.TemporaryDirectory() as state:
            server = server_module.make_server(ROOT, state, port=0)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with sync_playwright() as runtime:
                    browser = runtime.chromium.launch(headless=True)
                    try:
                        page = browser.new_page()
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto("http://127.0.0.1:" + str(server.server_port), wait_until="networkidle")
                        page.locator("#status").filter(has_text="已更新描述").wait_for()
                        self.assertEqual(page.locator("h1").inner_text(), product["name"])
                        self.assertGreater(page.locator("#rows tr").count(), 0)
                        page.locator("#rows tr").first.click()
                        self.assertIn("{", page.locator("#details").inner_text())
                        page.locator("#title").fill("复核记录")
                        page.locator("#save").click()
                        page.locator("#status").filter(has_text="已保存记录").wait_for()
                        frozen = json.loads(page.locator("#details").inner_text())
                        option = product["options"][-1]
                        alternative = next(value for value in option[2] if value != product["selection"][option[0]])
                        page.locator("#select-" + option[0]).select_option(alternative)
                        page.locator("#inspect").click()
                        page.wait_for_function("value => {try{return JSON.parse(document.getElementById('details').textContent).selection[value.key] === value.choice}catch{return false}}", arg={"key":option[0],"choice":alternative})
                        page.locator("#raw").click()
                        raw_table = next(iter(json.loads((ROOT / "fixtures/data.json").read_text())))
                        page.wait_for_function("key => document.getElementById('details').textContent.includes(JSON.stringify(key))", arg=raw_table)
                        page.locator("#archive button").first.click()
                        page.locator("#status").filter(has_text="已读取保存的描述").wait_for()
                        reopened = json.loads(page.locator("#details").inner_text())
                        self.assertEqual(reopened["revision"], frozen["revision"])
                        self.assertEqual(reopened["selection"], frozen["selection"])
                        self.assertEqual(reopened["selection"][option[0]], product["selection"][option[0]])
                        self.assertEqual(page.locator("#select-" + option[0]).input_value(),
                                         product["selection"][option[0]])
                        with page.expect_download() as event:
                            page.locator("#export").click()
                        exported = json.loads(Path(event.value.path()).read_text())
                        self.assertEqual(exported["description"]["revision"], frozen["revision"])
                        self.assertIn(raw_table, exported["data"])
                        page.locator("#upload").set_input_files(str(ROOT / "fixtures/data.json"))
                        page.locator("#status").filter(has_text="已导入独立数据版本").wait_for()
                        self.assertEqual(page.locator("#revision option").count(), 1)
                        self.assertEqual(errors, [])
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                worker.join()
                server.server_close()

    def test_trial_workflow_trace_and_compare(self):
        package = json.loads((ROOT / "package.json").read_text())
        module = package["scripts"]["start"].split()[2]
        trial_server_module = importlib.import_module(module + ".server")
        with tempfile.TemporaryDirectory() as state:
            server = trial_server_module.make_server(ROOT, state, port=0)
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with sync_playwright() as runtime:
                    browser = runtime.chromium.launch(headless=True)
                    try:
                        page = browser.new_page()
                        errors = []
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.goto("http://127.0.0.1:" + str(server.server_port), wait_until="networkidle")
                        page.locator("#status").filter(has_text="已更新描述").wait_for()
                        page.locator("#tab-trial").click()
                        self.assertFalse(page.locator("#view-trial").is_hidden())
                        page.locator("#trial-mode").select_option("uniform")
                        page.locator("#run-trial").click()
                        page.locator("#status").filter(has_text="试算完成").wait_for()
                        self.assertGreater(page.locator("#household-rows tr").count(), 100)
                        summary_text = page.locator("#trial-summary").inner_text()
                        self.assertIn("超过负担线家庭比例", summary_text)
                        self.assertIn("自助95%区间", summary_text)
                        # 点开一户核对完整计算过程
                        page.locator("#household-rows tr").first.click()
                        trace = page.locator("#details").inner_text()
                        self.assertIn("逐账单过程", trace)
                        self.assertIn("家庭层面汇总过程", trace)
                        self.assertIn("household_cap_applied_once", trace)
                        # 不可判断家庭带有原因，而非空白
                        page.locator("#household-rows tr .badge-indeterminate").first.click()
                        self.assertIn("不能确定或需提示的原因", page.locator("#details").inner_text())
                        # 情景对比：相同家庭基础
                        page.locator("#run-compare").click()
                        page.locator("#compare-box").filter(has_text="共同家庭").wait_for()
                        compare_text = page.locator("#compare-box").inner_text()
                        self.assertIn("配对自助95%区间", compare_text)
                        self.assertIn("150 户", compare_text)
                        # 调整收费表后重算，服务端生效而非前端编造
                        page.locator("#tariff-price").fill("40")
                        page.locator("#run-trial").click()
                        page.locator("#status").filter(has_text="试算完成").wait_for()
                        tariff_detail = json.loads(page.locator("#details").inner_text())
                        self.assertEqual(tariff_detail["effective_tariff"]["hour_price"], 40.0)
                        # 保存试算记录并重新打开
                        page.locator("#title").fill("窗口试算存档")
                        page.locator("#save").click()
                        page.locator("#status").filter(has_text="已保存记录").wait_for()
                        page.locator("#archive button").first.click()
                        page.locator("#status").filter(has_text="已读取保存的试算").wait_for()
                        self.assertFalse(page.locator("#view-trial").is_hidden())
                        self.assertEqual(errors, [])
                    finally:
                        browser.close()
            finally:
                server.shutdown()
                worker.join()
                server.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
