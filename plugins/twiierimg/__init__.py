from pytz import timezone
from nonebot import require
from nonebot.plugin import on_regex
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment
from datetime import datetime
import httpx, parsel, nonebot, re

global_config = nonebot.get_driver().config
config = global_config.dict()

cook = 'guest_id_marketing=v1%3A165156023285011075; guest_id_ads=v1%3A165156023285011075; personalization_id="v1_Pvv0TQogUkbR+NOZTzHKdw=="; guest_id=v1%3A165156023285011075; gt=1521380011949047808; external_referer=padhuUp37zjgzgv1mFWxJ5Xq0CLV%2BbpWuS41v6lN3QU%3D|0|8e8t2xd8A2w%3D; g_state={"i_l":0}; kdt=JmttmzMXhiMF5H83vmA8f3niTdx3wVOXL1alOyx4; auth_token=9ea51b6f169d0c789e12595c1a62c02cb619d720; ct0=ec82e7786200032e743ba55b31bea2fa6865384f3062590cd4506be07198131950e80a082580876635e19b638babcaf64da52a11a5604fd684d655978916dec67d2b507a0a952960972c20a3f8c29b99; twid=u%3D1124959607418441728; att=1-RcXNRCL2BII4NX2L6oJfTGfLvPRPNXQK8NDkzsG2; guest_id=v1%3A165156086844575104; guest_id_ads=v1%3A165156086844575104; guest_id_marketing=v1%3A165156086844575104; lang=en; personalization_id="v1_Xi5IGkKhScN0yo3hpm+gWw=="'


async def get_twitter_img(id: str):
    url = f"https://twitter.com/i/api/graphql/6n-3uwmsFr53-5z_w5FTVw/TweetDetail?variables=%7B%22focalTweetId%22%3A%22{id}%22%2C%22with_rux_injections%22%3Afalse%2C%22includePromotedContent%22%3Atrue%2C%22withCommunity%22%3Atrue%2C%22withQuickPromoteEligibilityTweetFields%22%3Atrue%2C%22withBirdwatchNotes%22%3Afalse%2C%22withSuperFollowsUserFields%22%3Atrue%2C%22withDownvotePerspective%22%3Atrue%2C%22withReactionsMetadata%22%3Afalse%2C%22withReactionsPerspective%22%3Afalse%2C%22withSuperFollowsTweetFields%22%3Atrue%2C%22withVoice%22%3Atrue%2C%22withV2Timeline%22%3Atrue%2C%22__fs_responsive_web_like_by_author_enabled%22%3Afalse%2C%22__fs_dont_mention_me_view_api_enabled%22%3Atrue%2C%22__fs_interactive_text_enabled%22%3Atrue%2C%22__fs_responsive_web_uc_gql_enabled%22%3Afalse%2C%22__fs_responsive_web_edit_tweet_api_enabled%22%3Afalse%7D"
    headers = {
        'authority': 'twitter.com',
        'accept': '*/*',
        'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'authorization': 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA',
        'content-type': 'application/json',
        'cookie': f'{cook}',
        'referer': 'https://twitter.com/Aibek_U/status/1519572124133822464',
        'sec-ch-ua': '" Not A;Brand";v="99", "Chromium";v="100", "Google Chrome";v="100"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/100.0.4896.127 Safari/537.36',
        'x-csrf-token': 'ec82e7786200032e743ba55b31bea2fa6865384f3062590cd4506be07198131950e80a082580876635e19b638babcaf64da52a11a5604fd684d655978916dec67d2b507a0a952960972c20a3f8c29b99',
        'x-twitter-active-user': 'yes',
        'x-twitter-auth-type': 'OAuth2Session',
        'x-twitter-client-language': 'en'
    }
    urls = []
    async with httpx.AsyncClient() as client:
        resp = await client.get(url=url, headers=headers)
        urls = re.findall('https:\/\/pbs\.twimg\.com\/media\/(.*?)\.jpg', resp.text)
        urls = list(set(urls))
        urls = ["https://pbs.twimg.com/media/" + url + ".jpg?format=jpg&name=large" for url in urls]

    return urls


twitter_img = on_regex(pattern="^https://twitter.com/.*?/status/\d+$")


@twitter_img.handle()
async def twitter_img(bot: Bot, event: Event):
    print("进来了")
    return
    urls = await get_twitter_img("xxx")
    msgs = ""
    for url in urls:
        msgs += MessageSegment.image(url)
    await bot.send(event=event, message=msgs)
