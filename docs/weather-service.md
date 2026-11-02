# 天气服务：预报数据与原生工具分层

CADSG 将 native `web.run` 的 weather 操作接到 Open-Meteo 官方 geocoding/forecast 接口。Codex 继续管理工具调用、session、权限和取消；天气 adapter 只做数据服务请求、验证与引用保存，不调用模型或另写工具循环。

当前组合为 App 26.1023.6621.0 / native 0.165.0-alpha.1.2 P7，协议依赖固定 CDS 0.6.9。旧 r4/r5、CDS 0.6.6 是历史阶段，不是当前安装身份。完整范围见[资格索引](qualification-status.md)。

## 输入与返回

```json
{"weather":[{"location":"US,California,San Francisco","duration":1}]}
```

字段为 location、可选 ISO 日期 start、可选 duration。省略 start 使用**解析地点当地的今天**，省略 duration 为 7 天。本实现接收的 horizon 是今天至 15 天后；输出按天保留行，包含地点、国家/区域、坐标、时区、单位、检索时间、来源哈希和引用。温度为摄氏度、降水毫米、风速 km/h。

这是 `forecast_not_observation`。当天预报不是当前实测温度、实时传感器读数、历史观测或 archived forecast；没有数据的请求日、错误单位/数值、错网格或无法验证的日期明确失败，不包装成成功。

推荐明确的 Country,Area,City，例如 `US,California,San Francisco`、`US,CA,San Francisco` 或 `Germany,Berlin,Berlin`。US 州/DC 缩写按固定 USPS 表处理，不外推到其他国家。区域必须由服务返回字段验证，缺失则 422；服务省略行政区时，无区域要求的城市/国家查询仍可成立。

adapter 不挑第一项或最大同名城市；须精确匹配 city/country/area。两段式短代码可能混淆国家/区域，明确拒绝。完整 geocoder 页仍不确定时，只有已可验证的国家代码允许一次有界精化；不是重试一个失败请求，也不宣称识别所有语言别名和行政区缩写。

## 网络与失败合同

只用固定的 geocoding-api.open-meteo.com 与 api.open-meteo.com。沿用公共 DNS/domain/TLS 检查、限时限字节和单次尝试；不跟随重定向、不用 cookie/继承代理/自动重试。一次操作最多两个 geocoding 查询与一个 forecast 请求。429 保留；cached/indexed 天气不能偷取 live 数据，在出站前拒绝。

当前代码没有配置商业认证入口，公共端点的配额、用途条件与可用性以[服务条款](https://open-meteo.com/en/terms)为准；CADSG 不提供这些账号权益或 SLA。输出保留 Open-Meteo/GeoNames attribution。

## 本次实际范围

当前 P7 的真实 App 网页任务实际调用上例：省略 start、duration=1，解析到 San Francisco, California, US / America/Los_Angeles；geocoding 与 forecast 各一次实际 HTTP 200，完整响应哈希与保留天气 source ref 匹配。当地当天日期、所选日预报和 UTC 检索时间与真实任务窗口核对。

这是当前安装/真实 App 的特定正向案例，不是所有地点/日期/故障都现场重跑，也不证明天气预测准确率。源码/HTTP fixtures 的允许、拒绝、429、歧义与范围检查按相同组件承接，中间原失败保留。天气地点时区**不表示搜索 user_location.timezone 已支持**；搜索 timezone 仍明确 unsupported。

金融报价和体育私有 typed feed 仍单列为未接入云数据源，普通网页文字不替代它们。其他本地 runtime/web 要求不会因此被一起排除。

官方接口：[forecast](https://open-meteo.com/en/docs)、[geocoding](https://open-meteo.com/en/docs/geocoding-api)、[USPS 区域代码](https://pe.usps.com/text/pub28/28apb.htm)。
