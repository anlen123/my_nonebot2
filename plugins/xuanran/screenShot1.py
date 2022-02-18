
from selenium import webdriver
import sys 
import os 
import time
def main(url):
    # url = "http://liuhuaqiang.top"
    option = webdriver.ChromeOptions()
    option.add_argument("--headless")  # 设置无头模式
    option.add_argument("--no-sandbox")
    driver = webdriver.Chrome(options=option)
    driver.get(url)
    # driver.maximize_window()
    width = driver.execute_script(
        "return document.documentElement.scrollWidth")
    height = driver.execute_script(
        "return document.documentElement.scrollHeight")
    driver.set_window_size(width, height)
    driver.implicitly_wait(10)
    picture_time = time.strftime(
        "%Y-%m-%d-%H_%M_%S", time.localtime(time.time()))
    picture_url = driver.get_screenshot_as_file(f"{picture_time}.png")
    print("%s：截图成功！！！" % picture_url)
    driver.quit()
    os.system(f"mv {picture_time}.png /root/QQbotFiles/xr")
    # os.system("./QQbotFiles/QQbotFiles_update.sh")
    return picture_time+".png"

if __name__ == "__main__":
    for k,v in enumerate(sys.argv):
        if k == 1:
            print(main(v))

