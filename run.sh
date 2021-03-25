#!/bin/sh
#screen -S nb2 -X quit
#screen -S gocp -X quit
#screen -wipe
#screen -dmS nb2 /bin/bash
#screen -x -S nb2 -p 0 -X stuff "cd /root/my_nonebot2/nb2/\n"
#screen -x -S nb2 -p 0 -X stuff "export ALL_PROXY=http://127.0.0.1:1081\n"
#screen -x -S nb2 -p 0 -X stuff "python bot.py\n"
#sleep 5
#screen -dmS gocp /bin/bash
#screen -x -S gocp -p 0 -X stuff "cd /root/my_nonebot2/gocp/\n"
#screen -x -S gocp -p 0 -X stuff "./go\n" 



tmux kill-pane -t nb2 
tmux kill-pane -t gocp

tmux new -s nb2 -d 
tmux new -s gocp  -d 

tmux send -t "nb2" "export ALL_PROXY=http://127.0.0.1:1081;cd /root/my_nonebot2/nb2/; python bot.py" Enter
tmux send -t "gocp" "cd /root/my_nonebot2/gocp/ ; ./go" Enter


