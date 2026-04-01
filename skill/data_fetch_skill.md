# Data Fetch Skill（数据获取）

## 数据源
- Twelve Data API

## 规则
- 单个标的分析：可直接请求 API
- 批量拉取：
  - 通用批量：`script/fetch_daily.py`
  - 日报批量：`script/prepare_daily_context.py`
- 限频：每分钟最多 8 次请求（通过 `config/rate_limit_state.json` 做跨脚本全局限频）

## 推荐参数
- 周期：默认 `1day`
- K线数量：`outputsize >= 120`（结构分析建议 200）

## 异常处理
- 遇到限流或网络错误：记录失败标的并继续
- 任务结束输出成功/失败列表
