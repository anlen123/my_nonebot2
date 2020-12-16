#!/bin/sh
screen -X -S qqbot quit
screen -X -S gocp quit
screen -wipe
screen -dmS qqbot /bin/zsh
screen -x -S qqbot -p 0 -X stuff "cd /root/nonebot2/nb2/\n"
screen -x -S qqbot -p 0 -X stuff "/root/miniconda3/envs/qqbot/bin/python bot.py\n"
screen -dmS gocp /bin/zsh
screen -x -S gocp -p 0 -X stuff "cd /root/nonebot2/gocp/\n"
screen -x -S gocp -p 0 -X stuff "./go\n" 
