"""AI 文字「影響／要看的條件／準備」共用的界線（本人 2026-11-20：「準備」不要寫成「現在買進／賣出」的指令）。

消息（market_news.py）與白話分析（plain_daily.py）用同一條：不寫買賣指令或投資建議；「準備」只寫「如果…就…」的情境。
新聞與籌碼本身常寫「外資賣出」「投信逢低布局」，那是在講事情，所以一般句子只擋對著讀者說的話；
「準備」是寫給讀者的，「就」後面要讀者做的事一律不能是買賣。
"""
from __future__ import annotations

import re

_ACTIONS = r"買進|買入|賣出|加碼|減碼|進場|出場|承接|布局|佈局|出脫|停損|停利|獲利了結|抄底|追價|追高|殺低|建倉|平倉"
_ADVICE = re.compile(rf"(?:建議|不妨|應該|可以).{{0,3}}(?:{_ACTIONS})")
_ORDER_WORDS = re.compile(_ACTIONS)
_CONDITIONAL = re.compile(r"如果|若|一旦|萬一|要是")

NO_ORDERS = "不能寫買賣指令或投資建議"
NOT_CONDITIONAL = "準備要用「如果…就…」寫"


def order_problem(text: str, *, prepare: bool = False) -> str | None:
    """這一句能不能上站：不能有買賣指令；prepare 還要是「如果…就…」的情境。合格回 None，不合格回原因。"""
    # prepare 的「如果外資持續賣出」是在講條件；只有「就」後面要讀者做的事不能是買賣
    todo = text.split("就", 1)[-1] if prepare else ""
    if _ADVICE.search(text) or _ORDER_WORDS.search(todo):
        return NO_ORDERS
    if prepare and not _CONDITIONAL.search(text):
        return NOT_CONDITIONAL
    return None
