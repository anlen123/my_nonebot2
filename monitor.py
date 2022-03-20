#!/root/miniconda3/bin/python
# -*- coding: utf-8 -*- 

import os 
cmd = 'ps -ef  | grep "python bot.py" | grep -v grep'

task = os.popen(cmd)
ret = task.read()

os.system("date")
if ret:
    os.system("echo 机器人正常")
else:
    os.system("/root/my_nonebot2/kill.sh")
    os.system("/root/my_nonebot2/run.sh")
    os.system("echo 机器人重启")

