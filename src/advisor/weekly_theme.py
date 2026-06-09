"""Weekly theme rotation — each weekday emphasizes a different analytical angle.

The point: even when market data hasn't moved much, the daily Discord brief
should *feel* different so the reader actively engages instead of glossing
over a familiar layout. Different angle, different headline, different
hero stat.
"""
from datetime import date


# weekday (Monday=0) -> (中文短名, focus 描述, hero metric key)
WEEKLY_THEMES = {
    0: ("周一开局", "本周事件预告 + 全板块状态扫描",
        "用本周财报日历开局, 给一周定调"),
    1: ("技术面深扫", "重点关注价位 / 止损位 / 动量",
        "突出止损 / 目标价 / 拉伸警告"),
    2: ("新闻日", "财报临近股 + 跨股票主题热点",
        "突出新闻情绪 + 主题聚类"),
    3: ("板块轮动", "子板块强弱变化 + 资金流向",
        "突出板块平均涨幅对比"),
    4: ("周末预备", "本周回顾 + 下周日程",
        "突出本周 winners/losers + 下周关键事件"),
    5: ("周末扫描", "周末美股休市, 数据少, 简版报告",
        "突出价位 + 等明周"),
    6: ("周末扫描", "周末美股休市, 数据少, 简版报告",
        "突出价位 + 等明周"),
}


def get_today_theme(d: date | None = None) -> dict:
    """Return today's theme dict."""
    if d is None:
        d = date.today()
    wd = d.weekday()
    name, focus, hero = WEEKLY_THEMES.get(wd, ("常规扫描", "默认", "默认"))
    return {
        "weekday": wd,
        "weekday_name": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][wd],
        "name": name,
        "focus": focus,
        "hero": hero,
    }
