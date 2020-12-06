#!/bin/sh
screen -x -S qqbot -p 0 -X stuff "^C"
screen -x -S qqbot -p 0 -X stuff "/root/miniconda3/envs/qqbot/bin/python bot.py\n"
screen -x -S gocp -p 0 -X stuff "^C"
screen -x -S gocp -p 0 -X stuff "./go-cqhttp-v0.9.33-linux-amd64\n" 
