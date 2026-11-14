# 应用接口

服务绑定本地地址，输入输出为 UTF-8 JSON。错误输入为400，未找到对象为404，超过8MB请求为413。页面与服务端共用口径，前端不另算一份统计答案。

- `GET /health`：检查启动是否成功。
- `GET /api/context`：产品配置、默认数据和已保存材料目录。
- `GET /api/data?revision=版本`：查看完整原始表。
- `POST /api/datasets`：传入 `label` 与完整 `data`，校验后返回不可变 `revision`。
- `POST /api/explore`：传入可选的 `revision` 和 `selection`，返回实际选择、资料指纹与 `view`。该 view 的 summary、rows、series、notes 只用于挂牌费用描述，不含家庭推断。
- `POST /api/trial`：家庭可负担性试算。传入 `revision` 与 `scenario`，返回 `scenario`（生效后的完整情景）、资料指纹与 `view`。
- `POST /api/compare`：情景对比。传入 `revision`、`base` 与 `alternative` 两个情景；服务端强制相同居住范围，并校验两个情景覆盖完全相同的家庭集合后才返回对比。
- `POST /api/notebooks`：传入 `title` 与 `kind`（`explore`/`trial`/`compare`，缺省 `explore`）及对应选择，由服务端重新生成结果后保存，不接受客户端伪造结果。
- `GET /api/notebooks` 与 `GET /api/notebooks/编号`：目录（含 `kind`）以及冻结资料、选择、结果与请求参数。旧版无 `kind` 的存档按 `explore` 读取。

## 情景参数

`scenario` 在 `selection`（`period`、`zone`）之外接受：

- `mode`：`observed`（仅计入已购买账单，未满足需要单独标记）或 `uniform`（忽略购买状态，按记录小时数作为统一服务需要）。
- `burden_line`：负担线，净支出/可支配资源的阈值，须为正数且不超过2。
- `missing_transport`：交通费漏报的点值口径，`impute_median`（默认）、`zero` 或 `exclude_cost`；无论选哪种，费用区间下界按0、上界按同期同范围观测最大值同时返回。
- `uniform_hours`：仅 uniform 可用，给定后每位成员统一按该小时数试算，留空则用各自记录小时数。
- `tariff_override`：对象，可含 `hour_price`、`eligible_share`（0至1）、`household_cap`、`disposable_deduction` 中任意字段；缺省字段沿用所选月份收费表。调整只存在于本次试算，原始事实不修改。

## 试算 view 结构

- `summary`：三分类的加权比例与分层整群自助95%区间、户均净支出点估计与区间、待确认/漏报/未满足需要/触顶/开区间家庭计数。
- `buckets`：超过负担线、低于负担线、不可判断三类的户数与加权比例，三者加权比例合计100%。
- `components`：服务费、餐费、交通费、封顶后补助、封顶削减额的加权户均与加权总量。
- `cdf`：负担率加权累计分布的区间带（确定不超过 x 与可能不超过 x 两条包络），开区间负担率家庭不参与绘制但保留在分类统计中。
- `rows`：每户的净支出区间、负担率区间、分类、原因与完整 `steps`/`bill_steps` 计算过程，供窗口逐户核对。
- `notes`、`uncertainty`：口径说明与自助法参数（层数、PSU 重抽、种子、区间不可用时的原因）。

对比 view 额外给出 `common_base`（共同家庭数与权重合计）、`over_burden_share` 的配对自助差值区间与 `component_delta`。所有输出只含有限 JSON 数值，开区间用 `null` 表示，不输出 `Infinity`/`NaN`。

新增分析可扩展接口，但已有导入、描述和历史读取仍供业务使用，不能静默改换其含义。
