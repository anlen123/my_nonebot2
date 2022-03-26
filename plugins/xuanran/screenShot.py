from selenium import webdriver
import sys 
import os 
import pendulum

list_options = ("--headless","--no-sandbox","--disable-gpu","--hide-scrollbars")
def main(url):
    # url = "http://liuhuaqiang.top"
    option = webdriver.ChromeOptions()
    for o in list_options:
        option.add_argument(o)
    driver = webdriver.Chrome(options=option)
    driver.get(url)
    width = driver.execute_script(
        "return document.documentElement.scrollWidth")
    height = driver.execute_script(
        "return document.documentElement.scrollHeight")
    driver.set_window_size(width, height)
    driver.implicitly_wait(10)
    picture_time = pendulum.now().format("Y-MM-DD-HH_mm_ss")
    png = f"{picture_time}.png"
    picture_url = driver.get_screenshot_as_file(png)
    print(f"{picture_url}：截图成功！！！")
    driver.quit()
    os.system(f"mv {png} /root/QQbotFiles/xr")
    # os.system("./QQbotFiles/QQbotFiles_update.sh")
    return picture_time + ".png"
if __name__ == "__main__":
    for k,v in enumerate(sys.argv):
        if k == 1:
            print(main(v))

