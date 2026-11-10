"""物流利润中心表格上报。

按需求文档 P001「各项公式参考上个月份的」，从四地办事处简表的上月列
平移出本月列，注入本月源报表外部链接，并提供当月 / 累计双口径对平校验。

模块边界：
    config   —— 四地逐表规则（只描述怎么推导，不存放答案数值）
    formula  —— 公式平移、外链索引归一化、引用提取
    engine   —— 主流程：输入解析、平移、外链注入、复现比对、双口径校验
    service  —— 任务编排、结果包 ZIP、无状态 job store
    routes   —— /api/profit-center/* HTTP 接口
"""
from .service import ProfitCenterError, config_payload, get_job, run_job

__all__ = ["ProfitCenterError", "config_payload", "get_job", "run_job"]
