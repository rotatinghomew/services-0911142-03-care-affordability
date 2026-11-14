# 家庭照护负担试算器

这是供服务规划人员核对资料、查看业务范围和留存讨论依据的本地应用。当前提供家庭收入区间与照护费用构成的浏览，以及家庭可负担性情景试算：在家庭层面计算净支出区间相对可支配资源的分布、超过自定负担线的家庭比例（含抽样不确定性）、收费与补助条款贡献，并支持相同家庭基础上的情景对比与逐户计算过程核对。示例为独立生成的虚构数据，不含真实家庭、医疗资料或对外服务凭据。

## 试算口径

- 家庭是共同资源与抽样单位：补助资格按成员判定，补助合计与月度封顶在家庭层面只扣一次，生活保留额每户只扣一次。
- 收入只有档位（含开区间），全程区间运算，不用档位中点制造精确排名；结果分为超过负担线、低于负担线、不可判断三类，不可判断附原因。
- observed 情景只计已购买账单并单独标记未满足需要；uniform 情景忽略购买状态按需要提供统一服务，不把未购买解释成无需要。
- 交通费漏报保留可成立区间，点值口径可选中位数插补、按0或不计入；成员关系待确认的家庭强制不可判断。
- 总体解释保留抽样权重，不确定性用分层整群自助法；情景对比使用相同家庭基础与配对重抽。

## 使用环境

使用 Python 3.11，独立容器镜像锁定到 3.11.14。页面为原生浏览器界面，数据处理通过本地 HTTP 服务完成，持久化使用应用内置数据库。基础浏览不依赖数值库；研究扩展所需的数值计算和建模库已按 `requirements.txt` 固定所有传递版本。依赖安装及镜像构建只在环境准备时做，启动和检查不访问网络。

```bash
python3 -m pip install -r requirements.txt -r requirements-build.txt -r requirements-test.txt
python3 -m playwright install --with-deps chromium
python3 -m care_affordability --host 127.0.0.1 --port 8080
```

浏览器打开本地8080端口。容器方式先准备镜像，运行时不需要其他服务：

```bash
docker build -t care_affordability:local .
docker run --rm -p 8080:8080 -v care_affordability-state:/app/.state care_affordability:local
```

## 验证

```bash
python3 -m unittest discover -s tests -v
python3 -m build --wheel --no-isolation
python3 tests/browser_smoke.py
```

检查包含真实领域字段、非法导入、边界数据保留、HTTP交互、版本指纹、重启后存档，以及家庭区间核算、家庭层面封顶、不可判断分类、加权自助不确定性、相同家庭基础对比和试算页面交互的回归。新增能力需要补充针对统计识别、支持范围和页面一致性的回归。运行检查使用临时状态，不改动资料。

字段语义与现有接口分别见 `docs/data-contract.md` 和 `docs/http.md`。短时操作见 `docs/walkthrough.md`，环境准备不包含在正式操作中。
