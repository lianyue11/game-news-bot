import requests
import datetime


FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/34da06fe-0df9-48a6-b6a5-da52fd52cf9c"


def send_message(text):

    data = {
        "msg_type": "text",
        "content": {
            "text": text
        }
    }

    requests.post(
        FEISHU_WEBHOOK,
        json=data
    )


today = datetime.date.today()

message = f"""
🎮 游戏公司日报

日期：{today}

机器人第一次上线！

以后这里会变成：
- 游戏公司新闻
- 行业动态
- 自动总结

"""

send_message(message)
