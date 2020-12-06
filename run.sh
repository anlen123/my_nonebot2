#!/bin/sh
screen -dmS qqbot
screen -x -S qqbot -p 0 -X stuff "cd /root/nonebot2/nb2/\n"
screen -x -S qqbot -p 0 -X stuff "/root/miniconda3/envs/qqbot/bin/python bot.py\n"
screen -dmS gocp
screen -x -S gocp -p 0 -X stuff "cd /root/nonebot2/gocp/\n"
screen -x -S gocp -p 0 -X stuff "./go-cqhttp-v0.9.33-linux-amd64\n" 
