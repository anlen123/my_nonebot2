#!/bin/sh
screen -S nb2 -X quit
screen -S gocp -X quit
screen -wipe
screen -dmS nb2 /bin/bash
screen -x -S nb2 -p 0 -X stuff "cd /root/my_nonebot2/nb2/\n"
screen -x -S nb2 -p 0 -X stuff "export ALL_PROXY=http://127.0.0.1:1081\n"
screen -x -S nb2 -p 0 -X stuff "python bot.py\n"
sleep 5
screen -dmS gocp /bin/bash
screen -x -S gocp -p 0 -X stuff "cd /root/my_nonebot2/gocp/\n"
screen -x -S gocp -p 0 -X stuff "./go\n" 
