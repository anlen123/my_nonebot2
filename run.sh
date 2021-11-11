#!/bin/sh
#screen -S nb2 -X quit
#screen -S gocq -X quit
#screen -wipe
#screen -dmS nb2 /bin/bash
#screen -x -S nb2 -p 0 -X stuff "cd /root/my_nonebot2/nb2/\n"
#screen -x -S nb2 -p 0 -X stuff "export ALL_PROXY=http://127.0.0.1:1081\n"
#screen -x -S nb2 -p 0 -X stuff "python bot.py\n"
#sleep 5
#screen -dmS gocq /bin/bash
#screen -x -S gocq -p 0 -X stuff "cd /root/my_nonebot2/gocq/\n"
#screen -x -S gocq -p 0 -X stuff "./go\n" 



tmux kill-pane -t nb2 
tmux kill-pane -t gocq

tmux new -s nb2 -d 
tmux new -s gocq  -d 

#tmux send -t "nb2" "export ALL_PROXY=http://127.0.0.1:1081;cd /root/my_nonebot2/nb2/; python bot.py >> ../nb2.log" Enter
#tmux send -t "gocq" "cd /root/my_nonebot2/gocq/ ; ./go >> ../goqp.log" Enter

tmux send -t "nb2" "cd ;cd my_nonebot2/nb2/; python bot.py > /root/nb2.log" Enter
tmux send -t "gocq" "cd ;cd my_nonebot2/gocq/ ; ./go" Enter
