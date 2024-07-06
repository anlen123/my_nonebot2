from PIL import Image
import requests
from io import BytesIO
import base64


def pin_quality(card_id:int, quality:str):
    # 加载URL图片
    url_image_url = f'https://cdn.233.momobako.com/ygopro/pics/{card_id}.jpg'
    response = requests.get(url_image_url)
    url_image = Image.open(BytesIO(response.content))

    # 加载本地图片
    local_image_path = f'./{quality}'
    local_image = Image.open(local_image_path)
    local_image.thumbnail((local_image.width * 0.5, local_image.height * 0.5))

    # 计算拼接后的图像大小
    result_width = url_image.width
    result_height = url_image.height + local_image.height

    # 创建空白图像作为拼接结果
    result_image = Image.new('RGB', (result_width, result_height))

    # 将本地图片粘贴到拼接结果的顶部
    result_image.paste(local_image, (url_image.width - local_image.width, 0))

    # 将URL图片粘贴到拼接结果的底部
    result_image.paste(url_image, (0, local_image.height))

    # 将拼接后的图像转换为Base64格式
    buffered = BytesIO()
    result_image.save(buffered, format='JPEG')
    result_image.save("1.jpeg")
    image_base64 = base64.b64encode(buffered.getvalue()).decode()

    return image_base64