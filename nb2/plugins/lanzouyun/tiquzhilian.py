import requests
import re
from fake_useragent import UserAgent

ua = UserAgent()
headers = {'User-Agent': ua.random, 'Referer': 'https://lanzous.com'}


def get_jumpUrl(shareUrl):
    r = requests.get(url=shareUrl, headers=headers)
    sign = re.findall('<iframe class=".*" name=".*?" src="(.*?)" frameborder="0" scrolling=".*?"></iframe>', r.text)[1]
    jumpUrl = 'https://lanzous.com' + sign
    return jumpUrl


def get_down_url(jumpUrl):
    jumpHtml = requests.get(jumpUrl, headers=headers)
    sign = re.search('[0-9a-zA-Z\_]{20,1000}', jumpHtml.text)
    json = requests.post('https://lanzous.com/ajaxm.php', headers=headers,
                         data={'action': 'downprocess', 'sign': sign.group(), 'ves': '1'}).json()
    downUrl = json['dom'] + '/file/' + json['url']
    return downUrl


def main(shareUrl):
    jumpUrl = get_jumpUrl(shareUrl)
    url = get_down_url(jumpUrl)
    headers2 = {
        'User-Agent': ua.random,
        'Accept-Language': 'zh-CN,zh;q=0.8,zh-TW;q=0.7,zh-HK;q=0.5,en-US;q=0.3,en;q=0.2',
    }
    res4 = requests.head(url, headers=headers2)
    return res4.headers['Location']


# if __name__ == '__main__':
    # shareUrl = 'https://liuhuaqiang.lanzous.com/iHa78gn5icj'
    # x = main(shareUrl)
    # print(x)
