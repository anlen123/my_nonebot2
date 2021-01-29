#!/bin/sh
screen -S qqbot -X quit
screen -S gocp -X quit
screen -wipe
screen -dmS qqbot /bin/bash
screen -x -S qqbot -p 0 -X stuff "cd /root/nonebot2/nb2/\n"
screen -x -S qqbot -p 0 -X stuff "python bot.py\n"
sleep 5
screen -dmS gocp /bin/bash
screen -x -S gocp -p 0 -X stuff "cd /root/nonebot2/gocp/\n"
screen -x -S gocp -p 0 -X stuff "./go\n" 